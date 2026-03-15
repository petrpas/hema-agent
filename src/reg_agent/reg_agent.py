"""Interactive AI agent backend for the HEMA tournament Discord bot.

The agent guides organisers through the registration enrichment pipeline
one step at a time, with human-in-the-loop approval after each step.
Discord channel history is the sole persistent conversation layer.
"""

import asyncio
import csv
import io
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, UTC
from pathlib import Path

# Ensure src/ (for the config package) and src/reg_agent/ (for step bare-imports)
# are both on sys.path regardless of how this module is imported.
_DIR = Path(__file__).parent        # src/reg_agent/
_SRC = _DIR.parent                  # src/
for _p in (_SRC, _DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import discord
from config.tracing import get_langfuse_client, observe
from pydantic_ai import Agent, RunContext

langfuse = get_langfuse_client()

from config import RegConfig, RegUserConfig, save_config
from discord_bot.discord_utils import send_long
from models import FencerRecord
from discord_bot.msg_constants import SHEET_ACCESS_REQUEST, SHEET_CLONE_REQUEST
from setup_agent.setup_agent import SHARED_MEMORY_PATH
from step1_download import download_registrations
from step2_parse import parse_registrations
from step3_match import (
    match_fencers,
    load_corrections,
    save_corrections,
    _load_cache,
    _save_cache,
    _upsert_cache_entry,
    _normalize,
    _build_hr_index,
    _get_fighters_compact,
    _categorize_fencer,
    _match_table_chunks,
    _MATCH_TABLE_LEGEND,
    _MATCH_TABLE_TEMPLATE,
)
from step4_dedup import (
    deduplicate_fencers,
    merge_group,
    FENCERS_LIKELY_GROUPS_PENDING_FILE,
    _dedup_table_text,
    _dedup_likely_table_text,
)
from step5_ratings import fetch_ratings
from step6_upload import upload_results, upload_results_initial, setup_output_sheet
from utils import (
    load_fencers_list,
    save_fencers_list,
    load_ratings,
    REG_VER_DIR,
    REG_VER_FILE_PTN,
    REG_VER_FILE_REG,
    FENCERS_PARSED_FILE,
    FENCERS_MATCHED_FILE,
    FENCERS_CACHE_FILE,
    FENCERS_DEDUPED_FILE,
)

log = logging.getLogger(__name__)

MAX_HISTORY = 60  # Discord messages to include as context
_THREAD_PREFIX = "📊"
_THREAD_NAMES = {
    "EN": "📊 Processing outputs",
    "CS": "📊 Průběžné zpracování",
}

_SYSTEM_PROMPT = """\
You are the HEMA Tournament Registration Agent running inside a Discord channel.
You help tournament organisers enrich fencer registration data with HEMA Ratings scores.

## Language
Use the organiser's preferred language (stored in memory) for all messages to the organiser.
Internal reasoning, tool call arguments, and all other agent outputs must remain in English.

## Behaviour
- Never greet or re-introduce yourself — the channel welcome message already does that.
- Your text output is posted directly to the Discord channel — do not use any tool to send messages.
- Run **one pipeline step per turn**, then write a short summary as your output and STOP.
- Never advance to the next step without explicit organiser approval.
- Approval phrases: "ok", "yes", "proceed", "go", "continue", "looks good", "do it",
  "next", "start", "run", or equivalent — all count.
- **No step is mandatory.** If the organiser wants to skip a step, acknowledge and move on.
- Rejection or correction → ask for clarification in your output; do not advance.
- Organiser provides a fact to remember → call `store_memory`, then acknowledge in your output.
- `/status` → describe current pipeline state from history; do not run anything.
- On any step error → report it in your output and ask the organiser how to proceed.
- Answer questions if asked; do not advance the pipeline until the organiser resumes.
- Keep each response brief: one short paragraph (2–4 sentences). Never repeat information or state the same thing twice.

## No internal reveals
Never expose implementation details to the organiser. This means:
- Never mention memory, tools, tags, thread indexes, config, or file paths.
- Never say "I don't have X in memory" — just ask for the information naturally.
- Never reference tool names or step tags in output visible to the organiser.

## Pipeline steps (run in order, one at a time)
1. `tool_download_registrations`      — fetch latest registrations from Google Sheet
2. `tool_parse_registrations`         — parse and normalise fencer data
3. `tool_match_fencers`               — fuzzy-match fencers to HEMA Ratings profiles
4. `tool_deduplicate_fencers`         — merge duplicate registrations
   4a. If it reports likely groups pending: call `tool_find_likely_duplicates` immediately.
       Tell the organiser to ✅ groups in the thread (and reply with instructions if needed).
       Do NOT proceed to step 5 — wait for the next /run.
   4b. If the thread already has `#dedup-likely-*` messages from a prior turn:
       call `tool_merge_confirmed_duplicates` before `tool_fetch_ratings`.
5. `tool_fetch_ratings`               — fetch current ratings from hemaratings.com
6. `tool_upload_results`              — write enriched data to the output Google Sheet
   After an initial upload (fresh sheet), the bot posts a clone request to the channel.
   When the organiser replies with a link to their own copy, call `tool_set_output_sheet`
   to update the URL. Do NOT advance to step 7 until the organiser has provided their copy link
   or explicitly said they do not want to clone the sheet.
7. Payment matching                   — **not yet implemented**; mention this to the organiser and skip
8. Group seeding                      — **not yet implemented**; mention this to the organiser and skip

After each step, write a short natural-language summary and ask for approval before proceeding.

Each completed step posts a `✅ N — summary` message to the channel. These are the
authoritative pipeline state — use them to determine what has run when answering `/status`
or handling out-of-order events like CSV uploads.

## Registration sheet
The organiser can provide registration data in two ways:
- **Google Sheet** — share a sheet URL (standard flow below)
- **Direct CSV upload** — if you receive a `[system: organiser uploaded a CSV file …]`,
  check channel history for `✅ N` markers to determine pipeline state:
  - No `✅` markers yet: treat it as step 1 complete, confirm the upload and ask whether to proceed to parsing.
  - Steps already completed: ask the organiser whether this replaces the current data (restart from step 2) or is something else.

The registration Google Sheet URL is not stored in config — it comes from the organiser.
Before calling `tool_download_registrations`:
1. Check organiser memory for a line containing the registration sheet URL.
2. If not found, output the following message verbatim (it is already in the organiser's language):

{sheet_access_request}

   Then call `store_memory` with the URL they provide.
3. Call `check_access` with the URL.
   - If it returns `ok` and access was not already confirmed in memory,
     call `store_memory("registration sheet access verified")`.
   - If it returns an error, tell the organiser the bot cannot open the sheet and ask them
     to check the sharing settings. Do not proceed.

## Pipeline thread
The thread is created during step 1 and mentioned in the step 1 summary — include that mention
verbatim in your output so the organiser knows where to follow along.
Each step automatically posts its full tabulated output to the thread (side effect —
not visible in this context). The tag returned in each step summary can be used to retrieve
that data if the organiser raises an objection:
- Call `read_thread_message(tag)` to fetch the most recent data for that step.
- Only the **current run's thread** is accessible. If the organiser asks about data from a
  previous run, explain that it is not available here and they should consult the thread directly.

## Weapon / discipline codes
Weapons: LS = Long Sword, SA = Sabre, RA = Rapier, RD = Rapier & Dagger, SB = Sword & Buckler
Gender suffix: no suffix = Open by default, O = explicitly Open, W = Women, M = Men (e.g. LS = Long Sword open, LSO = Long Sword Open, LSW = Long Sword Women, LSM = Long Sword Men — rare, most men's categories run as Open)

## Correcting a match (step 3)
If the organiser reports a wrong match after step 3:
- Wrong hr_id or no profile: call tool_correct_match immediately.
  This fixes the current run and persists the correction for all future reruns.
- General matching guidance (nationality rules, proxy patterns, etc.):
  call store_memory with the text prefixed by "[match-hint]".
  These hints are automatically passed to the matcher on every rerun.
Do NOT re-run step 3 to apply a correction — tool_correct_match patches the data in place.

## Tournament
{tournament_name}

## Organiser memory
{memory}
"""


@dataclass
class AgentDeps:
    channel: discord.TextChannel
    thread: discord.Thread | None
    thread_index: dict[str, int]  # tag → message_id of most recent post with that tag
    config: RegConfig
    _called_this_turn: set[str] = field(default_factory=set)
    pending_likely_groups: list[list[FencerRecord]] = field(default_factory=list)


from config.tracing import enabled as _tracing_enabled
if _tracing_enabled:
    Agent.instrument_all()

registration_agent = Agent(
    "anthropic:claude-sonnet-4-6",
    deps_type=AgentDeps,
)


@registration_agent.system_prompt
def _system_prompt(ctx: RunContext[AgentDeps]) -> str:
    lang = ctx.deps.config.language
    bot_email = _bot_email(ctx.deps.config)
    sheet_request_template = SHEET_ACCESS_REQUEST.get(lang, SHEET_ACCESS_REQUEST["EN"])
    return _SYSTEM_PROMPT.format(
        tournament_name=ctx.deps.config.tournament_name,
        bot_email=bot_email,
        sheet_access_request=sheet_request_template.format(bot_email=bot_email),
        memory=_read_memory(),
    )


# ── Creds helpers ──────────────────────────────────────────────────────────────

def _bot_email(config: RegConfig) -> str:
    """Read client_email from the service account creds file."""
    import json as _json
    with open(config.creds_path) as f:
        return _json.load(f)["client_email"]


# ── Memory helpers ─────────────────────────────────────────────────────────────

def _read_memory() -> str:
    return SHARED_MEMORY_PATH.read_text().strip() if SHARED_MEMORY_PATH.exists() else "(empty)"


def _append_memory(fact: str) -> None:
    SHARED_MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    with SHARED_MEMORY_PATH.open("a") as f:
        f.write(f"- [{ts}] {fact}\n")


# ── Thread helpers ─────────────────────────────────────────────────────────────

async def _find_latest_thread(channel: discord.TextChannel) -> discord.Thread | None:
    """Return the most recent pipeline thread in the channel, unarchiving if needed."""
    candidates = [t for t in channel.threads if t.name.startswith(_THREAD_PREFIX)]
    if candidates:
        return max(candidates, key=lambda t: t.created_at)
    async for t in channel.archived_threads(limit=10):
        if t.name.startswith(_THREAD_PREFIX):
            await t.edit(archived=False)
            return t
    return None


async def _scan_thread(thread: discord.Thread) -> dict[str, int]:
    """Scan thread history and return a tag → message_id index (latest per tag wins)."""
    index: dict[str, int] = {}
    async for msg in thread.history(limit=200, oldest_first=True):
        first_line = msg.content.split("\n")[0].strip()
        if first_line.startswith("#"):
            index[first_line[1:]] = msg.id
    return index


async def _ensure_thread(deps: AgentDeps) -> discord.Thread | None:
    """Create the pipeline thread if it doesn't exist yet. Returns the thread or None on error."""
    if deps.thread is not None:
        return deps.thread
    try:
        date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        prefix = _THREAD_NAMES.get(deps.config.language, _THREAD_NAMES["EN"])
        thread_name = f"{prefix} {date_str}"
        anchor = await deps.channel.send(thread_name)
        deps.thread = await anchor.create_thread(
            name=thread_name,
            auto_archive_duration=1440,
        )
        deps.thread_index = {}
        return deps.thread
    except Exception:
        log.exception("Failed to create pipeline thread")
        return None


async def _post_to_thread(deps: AgentDeps, tag: str, content: str) -> None:
    """Post tagged content to the pipeline thread as a side effect of a step tool.

    Creates the thread on first call. Updates deps.thread and deps.thread_index in place.
    Errors are logged and swallowed so they never interrupt the pipeline.
    """
    try:
        thread = await _ensure_thread(deps)
        if thread is None:
            return
        first = await send_long(thread, f"#{tag}\n{content}")
        deps.thread_index[tag] = first.id
    except Exception:
        log.exception("Failed to post to thread (tag=%s)", tag)


async def _post_dedup_table(deps: AgentDeps, inputs: list[FencerRecord], merge_result) -> None:
    """Post one duplicate-merge table to the pipeline thread."""
    try:
        thread = await _ensure_thread(deps)
        if thread is None:
            return
        text = _dedup_table_text(inputs, merge_result.fencer, merge_result.merge_note)
        await send_long(thread, text)
    except Exception:
        log.exception("Failed to post dedup table for hr_id=%s", merge_result.fencer.hr_id)


async def _post_csv_to_thread(
    deps: AgentDeps, tag: str, caption: str, header: list[str], rows: list[list[str]], filename: str
) -> None:
    """Post a CSV file attachment to the pipeline thread.

    The text caption carries #tag so the thread index still works.
    Errors are logged and swallowed so they never interrupt the pipeline.
    """
    try:
        thread = await _ensure_thread(deps)
        if thread is None:
            return
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(header)
        writer.writerows(rows)
        buf.seek(0)
        file = discord.File(io.BytesIO(buf.read().encode()), filename=filename)
        msg = await thread.send(f"#{tag}\n{caption}", file=file)
        deps.thread_index[tag] = msg.id
    except Exception:
        log.exception("Failed to post CSV to thread (tag=%s)", tag)


# ── Tools ──────────────────────────────────────────────────────────────────────

def _once_per_turn(deps: AgentDeps, tool_name: str) -> str | None:
    """Return an error string if tool_name was already called this turn, else register it."""
    if tool_name in deps._called_this_turn:
        return f"error: {tool_name} was already called this turn — do not call the same step tool twice in one turn."
    deps._called_this_turn.add(tool_name)
    return None

@registration_agent.tool
def store_memory(ctx: RunContext[AgentDeps], fact: str) -> str:
    """Persist an organiser-provided fact to the tournament memory file."""
    _append_memory(fact)
    return "stored"


@registration_agent.tool
def read_memory(ctx: RunContext[AgentDeps]) -> str:
    """Return the full contents of the tournament memory file."""
    return _read_memory()


@registration_agent.tool
async def read_thread_message(ctx: RunContext[AgentDeps], tag: str) -> str:
    """Fetch the most recent data posted to the pipeline thread for a given step tag.

    Only the current run's thread is accessible — data from previous runs is not available.
    tag: as returned in the step summary, e.g. "step2-parse", "step3-match".
    """
    if ctx.deps.thread is None:
        return "No pipeline thread exists for this run yet."
    msg_id = ctx.deps.thread_index.get(tag)
    if msg_id is None:
        available = list(ctx.deps.thread_index.keys())
        return f"No data found for tag '{tag}'. Available tags: {available or 'none yet'}"
    try:
        msg = await ctx.deps.thread.fetch_message(msg_id)
        return msg.content
    except Exception as e:
        return f"error fetching message: {e}"


@registration_agent.tool
def check_access(ctx: RunContext[AgentDeps], sheet_url: str) -> str:
    """Try to open a Google Sheet to verify the bot has access. Returns 'ok' or an error message."""
    import gspread
    try:
        gc = gspread.service_account(filename=ctx.deps.config.creds_path)
        gc.open_by_url(sheet_url)
        return "ok"
    except Exception as e:
        return f"error: {e}"


@registration_agent.tool
async def tool_download_registrations(
    ctx: RunContext[AgentDeps],
    sheet_url: str,
    worksheet_index: int = 0,
    worksheet_name: str | None = None,
) -> str:
    """Step 1: Download the latest registrations from the Google Sheet.

    Specify either worksheet_index (0-based, default 0) or worksheet_name — not both.
    """
    if err := _once_per_turn(ctx.deps, "tool_download_registrations"):
        return err
    try:
        path = await asyncio.to_thread(
            download_registrations, ctx.deps.config, sheet_url, worksheet_index, worksheet_name
        )
    except Exception as e:
        return f"error: {e}"

    # Step 1 always starts a new pipeline run — force a fresh thread even if an old one exists.
    ctx.deps.thread = None
    ctx.deps.thread_index = {}
    thread = await _ensure_thread(ctx.deps)
    thread_mention = f" Detailed results are being posted to {thread.mention}." if thread else ""
    try:
        if thread is not None:
            file = discord.File(str(path), filename=path.name)
            msg = await thread.send("#step1-download", file=file)
            ctx.deps.thread_index["step1-download"] = msg.id
    except Exception:
        log.exception("Failed to post step1 file to thread")
    await _post_to_thread(ctx.deps, "step1-download", f"✅ 1 — {path.name}")
    return f"Downloaded → {path.name}.{thread_mention}"  # path.name only, never full server path


@registration_agent.tool
async def tool_parse_registrations(ctx: RunContext[AgentDeps]) -> str:
    """Step 2: Parse raw registration CSV into structured fencer records."""
    import re

    data_dir = ctx.deps.config.data_dir / REG_VER_DIR
    csvs = sorted(data_dir.glob(REG_VER_FILE_PTN))
    if not csvs:
        return "No registration CSV found — run tool_download_registrations first."

    def _ver(p: Path) -> int:
        m = re.search(REG_VER_FILE_REG, p.name)
        return int(m.group(1)) if m else -1

    latest = max(csvs, key=_ver)
    if err := _once_per_turn(ctx.deps, "tool_parse_registrations"):
        return err
    try:
        fencers = await asyncio.to_thread(parse_registrations, latest, ctx.deps.config)
    except Exception as e:
        return f"error: {e}"

    weapon_counts: dict[str, int] = {}
    for f in fencers:
        for d in f.disciplines:
            weapon_counts[str(d.weapon)] = weapon_counts.get(str(d.weapon), 0) + 1
    weapons_str = ", ".join(f"{w}×{c}" for w, c in sorted(weapon_counts.items()))
    no_id = sum(1 for f in fencers if f.hr_id is None)

    rows = [
        [f.name, f.nationality or "—", f.club or "—",
         " / ".join(d.str() for d in f.disciplines), str(f.hr_id) if f.hr_id else "—"]
        for f in fencers
    ]
    await _post_csv_to_thread(ctx.deps, "step2-parse", f"✅ 2 — {len(fencers)} fencers ({weapons_str}), {no_id} without HR ID",
                              ["Name", "Nationality", "Club", "Disciplines", "HR ID"], rows, "step2_parsed.csv")
    return (
        f"Parsed {len(fencers)} fencers. Disciplines: {weapons_str}. "
        f"{no_id} without hr_id (will be matched in next step). "
        f"Thread tag: step2-parse"
    )


@registration_agent.tool
async def tool_match_fencers(ctx: RunContext[AgentDeps]) -> str:
    """Step 3: Fuzzy-match fencers without hr_id to HEMA Ratings profiles."""
    fencers = load_fencers_list(ctx.deps.config.data_dir, FENCERS_PARSED_FILE)
    if fencers is None:
        return "No parsed fencers found — run tool_parse_registrations first."

    if err := _once_per_turn(ctx.deps, "tool_match_fencers"):
        return err
    before_unmatched = sum(1 for f in fencers if f.hr_id is None)
    parsed_fencers = fencers  # keep original for table building

    # Extract [match-hint] lines from organiser memory and pass to matcher
    memory_text = _read_memory()
    hints = []
    for line in memory_text.splitlines():
        if "[match-hint]" in line:
            idx = line.index("[match-hint]")
            hint_text = line[idx + len("[match-hint]"):].strip()
            if hint_text:
                hints.append(hint_text)
    instructions = "\n".join(hints) if hints else None

    try:
        fencers = await asyncio.to_thread(match_fencers, fencers, ctx.deps.config, instructions)
    except Exception as e:
        return f"error: {e}"
    after_unmatched = sum(1 for f in fencers if f.hr_id is None)
    unmatched_names = [f.name for f in fencers if f.hr_id is None]

    # Post human-readable matching table grouped by category
    try:
        fighters_text = await asyncio.to_thread(_get_fighters_compact, ctx.deps.config.data_dir)
        hr_index = _build_hr_index(fighters_text)
        lang   = ctx.deps.config.language
        tmpl   = _MATCH_TABLE_TEMPLATE.get(lang, _MATCH_TABLE_TEMPLATE["EN"])
        legend = _MATCH_TABLE_LEGEND.get(lang, _MATCH_TABLE_LEGEND["EN"])

        # Pre-compute proxy_emails from full list (must not use a subset)
        from collections import defaultdict
        email_names: dict[str, set[str]] = defaultdict(set)
        for f in fencers:
            if f.email:
                email_names[f.email.lower()].add(f.name.lower())
        proxy_emails = {e for e, names in email_names.items() if len(names) > 1}

        # Categorize fencers
        # Key by name — more unique than email (proxy fencers share email).
        parsed_by_name = {_normalize(f.name): f for f in parsed_fencers}
        groups: dict[str, list[FencerRecord]] = {"confirmed": [], "found": [], "unmatched": [], "rejected": []}
        for mf in fencers:
            pf = parsed_by_name.get(_normalize(mf.name), mf)
            groups[_categorize_fencer(pf, mf)].append(mf)

        SECTION_ORDER = ["confirmed", "found", "unmatched", "rejected"]
        non_empty = [s for s in SECTION_ORDER if groups[s]]

        thread = await _ensure_thread(ctx.deps)
        if thread is not None:
            await thread.send(tmpl["header"] + "\n" + legend)
            for i, section in enumerate(non_empty):
                await thread.send(tmpl[section])
                for chunk in _match_table_chunks(
                    parsed_fencers, groups[section], hr_index,
                    proxy_emails=proxy_emails,
                ):
                    await thread.send(chunk)
                if i < len(non_empty) - 1:
                    await thread.send("---")
    except Exception:
        log.exception("Failed to post step3 match table to thread")

    rows = [
        [f.name, f.nationality or "—", f.club or "—", str(f.hr_id) if f.hr_id else "unmatched"]
        for f in fencers
    ]
    await _post_csv_to_thread(ctx.deps, "step3-match", f"✅ 3 — matched {before_unmatched - after_unmatched}, {after_unmatched} still unmatched",
                              ["Name", "Nationality", "Club", "HR ID"], rows, "step3_matched.csv")

    result = (
        f"Matched {before_unmatched - after_unmatched} new fencers. "
        f"{after_unmatched} still unmatched."
    )
    if unmatched_names:
        result += f" Unmatched: {', '.join(unmatched_names)}."
    result += " Thread tag: step3-match"
    return result


@registration_agent.tool
async def tool_correct_match(
    ctx: RunContext[AgentDeps],
    fencer_name: str,
    correct_hr_id: int | None,
) -> str:
    """Fix a wrong step-3 match in fencers_matched.json and persist it so reruns stay correct.

    fencer_name: registered name exactly as it appears in the matched fencers list.
    correct_hr_id: the correct HEMA Ratings ID, or null if the fencer has no profile.
    """
    data_dir = ctx.deps.config.data_dir
    fencers = load_fencers_list(data_dir, FENCERS_MATCHED_FILE)
    if fencers is None:
        return "No matched fencers file — run tool_match_fencers first."

    cache_path = data_dir / FENCERS_CACHE_FILE
    cache = _load_cache(cache_path)
    corrections = load_corrections(data_dir)

    # Find fencer by name — exact match first, then case-insensitive
    target_idx: int | None = None
    for i, f in enumerate(fencers):
        if f.name == fencer_name:
            target_idx = i
            break
    if target_idx is None:
        for i, f in enumerate(fencers):
            if f.name.lower() == fencer_name.lower():
                target_idx = i
                break
    if target_idx is None:
        return f"Fencer '{fencer_name}' not found in matched fencers."

    fencer = fencers[target_idx]
    old_hr_id = fencer.hr_id

    if old_hr_id == correct_hr_id:
        return f"No change needed: {fencer_name} already has hr_id={correct_hr_id}."

    # Clean up old cache entry
    if old_hr_id is not None:
        old_key = str(old_hr_id)
        if old_key in cache:
            entry = cache[old_key]
            name_lower = fencer.name.lower()
            entry.alternative_names_used = [
                n for n in entry.alternative_names_used if n.lower() != name_lower
            ]
            if fencer.email:
                correct_key = str(correct_hr_id) if correct_hr_id is not None else None
                email_in_correct = (
                    correct_key is not None
                    and correct_key in cache
                    and fencer.email.lower() in {e.lower() for e in cache[correct_key].emails_used}
                )
                if not email_in_correct:
                    entry.emails_used = [
                        e for e in entry.emails_used if e.lower() != fencer.email.lower()
                    ]

    # Apply correction
    fencers[target_idx] = fencer.model_copy(update={"hr_id": correct_hr_id})

    # Add new cache entry
    if correct_hr_id is not None:
        _upsert_cache_entry(
            cache, correct_hr_id,
            fencer.name, fencer.club or "",
            fencer.email or "",
            fencer.nationality or None,
            None,
        )

    # Persist correction so reruns reproduce the correct result
    corrections[fencer_name] = correct_hr_id

    save_fencers_list(fencers, data_dir / FENCERS_MATCHED_FILE)
    _save_cache(cache, cache_path)
    save_corrections(corrections, data_dir)

    old_str = "no profile" if old_hr_id is None else f"hr_id={old_hr_id}"
    new_str = "no profile" if correct_hr_id is None else f"hr_id={correct_hr_id}"
    summary = f"Corrected: {fencer_name} → {new_str} (was {old_str})"
    await _post_to_thread(ctx.deps, "step3-correct", summary)
    return summary


@registration_agent.tool
async def tool_deduplicate_fencers(ctx: RunContext[AgentDeps]) -> str:
    """Step 4: Merge duplicate registrations (shared hr_id and surely-identical no-hr_id pairs)."""
    fencers = load_fencers_list(ctx.deps.config.data_dir, FENCERS_MATCHED_FILE)
    if fencers is None:
        return "No matched fencers found — run tool_match_fencers first."

    if err := _once_per_turn(ctx.deps, "tool_deduplicate_fencers"):
        return err
    before = len(fencers)
    try:
        fencers, dedup_report, likely_groups = await asyncio.to_thread(
            deduplicate_fencers, fencers, ctx.deps.config
        )
    except Exception as e:
        return f"error: {e}"
    merged = before - len(fencers)

    rows = [
        [f.name, f.nationality or "—", f.club or "—",
         " / ".join(d.str() for d in f.disciplines), str(f.hr_id) if f.hr_id else "—"]
        for f in fencers
    ]
    await _post_csv_to_thread(
        ctx.deps, "step4-dedup",
        f"✅ 4 — {before} → {len(fencers)} fencers ({merged} duplicate{'s' if merged != 1 else ''} merged)",
        ["Name", "Nationality", "Club", "Disciplines", "HR ID"], rows, "step4_deduped.csv",
    )

    for dup_inputs, dup_merged in dedup_report:
        await _post_dedup_table(ctx.deps, dup_inputs, dup_merged)

    ctx.deps.pending_likely_groups = likely_groups

    result = (
        f"Deduplication done: {before} → {len(fencers)} fencers "
        f"({merged} duplicate{'s' if merged != 1 else ''} merged). "
        f"Thread tag: step4-dedup"
    )
    if likely_groups:
        result += (
            f" Found {len(likely_groups)} likely no-hr_id duplicate group(s) awaiting confirmation — "
            f"call tool_find_likely_duplicates next."
        )
    return result


@registration_agent.tool
async def tool_find_likely_duplicates(ctx: RunContext[AgentDeps]) -> str:
    """Step 4b: Post likely no-hr_id duplicate groups to the pipeline thread for organiser ✅ confirmation.

    Call this immediately after tool_deduplicate_fencers reports pending likely groups.
    """
    if err := _once_per_turn(ctx.deps, "tool_find_likely_duplicates"):
        return err

    likely_groups = ctx.deps.pending_likely_groups
    if not likely_groups:
        return "No pending likely duplicate groups."

    # Persist groups to file so tool_merge_confirmed_duplicates can recover them on next /run
    import json as _json
    groups_data = {
        str(i + 1): [r.model_dump() for r in group]
        for i, group in enumerate(likely_groups)
    }
    groups_path = ctx.deps.config.data_dir / FENCERS_LIKELY_GROUPS_PENDING_FILE
    groups_path.write_text(_json.dumps(groups_data, ensure_ascii=False, indent=2))

    # Post each group as a tagged thread message
    for i, group in enumerate(likely_groups, 1):
        tag = f"dedup-likely-{i}"
        table = _dedup_likely_table_text(group)
        caption = f"#{tag}\n{table}\n_✅ to merge — reply to this message with merge instructions if needed_"
        thread = await _ensure_thread(ctx.deps)
        if thread:
            try:
                msg = await send_long(thread, caption)
                ctx.deps.thread_index[tag] = msg.id
            except Exception:
                log.exception("Failed to post dedup-likely-%d to thread", i)

    n = len(likely_groups)
    return (
        f"{n} likely group{'s' if n != 1 else ''} posted to thread — "
        f"awaiting user confirmation via ✅ reactions."
    )


@registration_agent.tool
async def tool_merge_confirmed_duplicates(ctx: RunContext[AgentDeps]) -> str:
    """Step 4c: Apply merges for likely-duplicate groups the organiser confirmed with ✅.

    Reads ✅ reactions from thread messages, merges confirmed groups, updates fencers_deduped.json.
    Call this at the start of a /run when the thread has pending #dedup-likely-* messages.
    """
    if err := _once_per_turn(ctx.deps, "tool_merge_confirmed_duplicates"):
        return err

    import json as _json

    groups_path = ctx.deps.config.data_dir / FENCERS_LIKELY_GROUPS_PENDING_FILE
    if not groups_path.exists():
        return "No pending likely groups file found — nothing to merge."

    groups_data: dict[str, list[dict]] = _json.loads(groups_path.read_text())

    fencers = load_fencers_list(ctx.deps.config.data_dir, FENCERS_DEDUPED_FILE)
    if fencers is None:
        return "No deduplicated fencers found."

    if ctx.deps.thread is None:
        return "No pipeline thread found."

    # Check each group for ✅ reactions
    confirmed_groups: list[list[FencerRecord]] = []
    confirmed_hints: list[str | None] = []
    skipped = 0

    for group_num_str, records_data in groups_data.items():
        tag = f"dedup-likely-{group_num_str}"
        msg_id = ctx.deps.thread_index.get(tag)
        if msg_id is None:
            skipped += 1
            continue

        try:
            msg = await ctx.deps.thread.fetch_message(msg_id)
        except Exception:
            log.exception("Failed to fetch thread message for tag=%s", tag)
            skipped += 1
            continue

        # Check for ✅ from any non-bot user
        confirmed_reaction = False
        for r in msg.reactions:
            if str(r.emoji) == "✅":
                async for user in r.users():
                    if not user.bot:
                        confirmed_reaction = True
                        break
            if confirmed_reaction:
                break

        if not confirmed_reaction:
            skipped += 1
            continue

        # Look for a thread reply on this message as a merge hint
        hint: str | None = None
        async for reply in ctx.deps.thread.history(limit=50):
            if reply.reference and reply.reference.message_id == msg_id and not reply.author.bot:
                hint = reply.content
                break

        group = [FencerRecord(**r) for r in records_data]
        confirmed_groups.append(group)
        confirmed_hints.append(hint)

    if not confirmed_groups:
        return f"No groups confirmed — {skipped} group{'s' if skipped != 1 else ''} skipped (no ✅)."

    # Merge each confirmed group
    merge_results: list[FencerRecord] = []
    merged_name_pairs: list[str] = []
    group_name_sets: list[set[str]] = []

    for group, hint in zip(confirmed_groups, confirmed_hints, strict=False):
        merge_result = await asyncio.to_thread(merge_group, group, ctx.deps.config, hint)
        merge_results.append(merge_result.fencer)
        merged_name_pairs.append(" + ".join(f.name for f in group))
        group_name_sets.append({f.name for f in group})

    # Update fencers list: replace each group's first member with the merged record, skip the rest
    new_fencers: list[FencerRecord] = []
    first_placed: set[int] = set()  # indices into confirmed_groups already emitted

    for fencer in fencers:
        placed = False
        for i, names_set in enumerate(group_name_sets):
            if fencer.name in names_set:
                if i not in first_placed:
                    new_fencers.append(merge_results[i])
                    first_placed.add(i)
                # else: subsequent member of this group — skip
                placed = True
                break
        if not placed:
            new_fencers.append(fencer)

    save_fencers_list(new_fencers, ctx.deps.config.data_dir / FENCERS_DEDUPED_FILE)
    groups_path.unlink(missing_ok=True)

    names_str = ", ".join(f"[{n}]" for n in merged_name_pairs)
    summary = (
        f"✅ Confirmed merge{'s' if len(confirmed_groups) != 1 else ''} applied: {names_str}. "
        f"{skipped} group{'s' if skipped != 1 else ''} skipped."
    )
    await ctx.deps.channel.send(summary)

    return (
        f"Applied {len(confirmed_groups)} confirmed merge{'s' if len(confirmed_groups) != 1 else ''}: "
        f"{', '.join(merged_name_pairs)}. {skipped} skipped."
    )


@registration_agent.tool
async def tool_fetch_ratings(ctx: RunContext[AgentDeps]) -> str:
    """Step 5: Fetch current ratings and rankings from hemaratings.com."""
    fencers = load_fencers_list(ctx.deps.config.data_dir, FENCERS_DEDUPED_FILE)
    if fencers is None:
        return "No deduplicated fencers found — run tool_deduplicate_fencers first."

    if err := _once_per_turn(ctx.deps, "tool_fetch_ratings"):
        return err
    try:
        ratings = await asyncio.to_thread(fetch_ratings, fencers, ctx.deps.config)
    except Exception as e:
        return f"error: {e}"
    rated = len(ratings)
    total_with_id = sum(1 for f in fencers if f.hr_id is not None)

    rows = [
        [str(hr_id), weapon, str(r.rating) if r.rating is not None else "—", str(r.rank) if r.rank is not None else "—"]
        for hr_id, weapon_map in ratings.items()
        for weapon, r in weapon_map.items()
    ]
    await _post_csv_to_thread(ctx.deps, "step5-ratings", f"✅ 5 — ratings fetched for {rated}/{total_with_id} fencers",
                              ["HR ID", "Weapon", "Rating", "Rank"], rows, "step5_ratings.csv")
    return f"Fetched ratings for {rated}/{total_with_id} fencers with hr_id. Thread tag: step5-ratings"


@registration_agent.tool
async def tool_upload_results(ctx: RunContext[AgentDeps], force_recreate: bool = False) -> str:
    """Step 6: Write enriched data to the output Google Sheet.

    force_recreate: if True, copy the template again and replace the existing output sheet URL.
    Use this when the organiser asks to regenerate the sheet from scratch.
    """
    fencers = load_fencers_list(ctx.deps.config.data_dir, FENCERS_DEDUPED_FILE)
    if fencers is None:
        return "No deduplicated fencers found — run tool_deduplicate_fencers first."

    rating_files = sorted(ctx.deps.config.data_dir.glob("ratings_*.json"))
    if not rating_files:
        return "No ratings file found — run tool_fetch_ratings first."

    ratings = load_ratings(ctx.deps.config.data_dir, rating_files[-1].name)
    if ratings is None:
        return "Could not load ratings — run tool_fetch_ratings first."

    if err := _once_per_turn(ctx.deps, "tool_upload_results"):
        return err

    sheet_just_created = ctx.deps.config.output_sheet_url is None or force_recreate
    if sheet_just_created:
        try:
            url = await asyncio.to_thread(setup_output_sheet, ctx.deps.config)
        except Exception as e:
            return f"error setting up output sheet: {e}"
        ctx.deps.config.output_sheet_url = url
        user_config_path = os.environ.get("USER_CONFIG")
        save_config(
            RegUserConfig(
                tournament_name=ctx.deps.config.tournament_name,
                language=ctx.deps.config.language,
                output_sheet_url=url,
                disciplines=ctx.deps.config.disciplines,
            ),
            user_config_path,
        )
        await _post_to_thread(ctx.deps, "step6-sheet-created", f"📄 Output sheet created: {url}")

    upload_fn = upload_results_initial if sheet_just_created else upload_results
    try:
        await asyncio.to_thread(upload_fn, fencers, ratings, ctx.deps.config)
    except Exception as e:
        return f"error: {e}"
    await _post_to_thread(ctx.deps, "step6-upload", "✅ 6 — upload complete")

    if sheet_just_created:
        lang = ctx.deps.config.language
        tmpl = SHEET_CLONE_REQUEST.get(lang, SHEET_CLONE_REQUEST["EN"])
        bot_email = _bot_email(ctx.deps.config)
        await ctx.deps.channel.send(tmpl.format(url=ctx.deps.config.output_sheet_url, bot_email=bot_email))
        return (
            f"Upload complete. Output sheet: {ctx.deps.config.output_sheet_url}. "
            f"Waiting for the organiser to clone the sheet and share their copy."
        )

    return f"Upload complete. Output sheet: {ctx.deps.config.output_sheet_url}"


@registration_agent.tool
async def tool_set_output_sheet(ctx: RunContext[AgentDeps], url: str) -> str:
    """Update the output sheet URL after the organiser shares their own copy with the bot.

    Call this when the organiser pastes a link to their cloned copy of the output sheet.
    Verifies access, updates the running config, and persists the new URL to user_config.json.
    """
    import gspread as _gs
    try:
        gc = _gs.service_account(filename=ctx.deps.config.creds_path)
        gc.open_by_url(url)
    except Exception as e:
        return f"error: cannot access the sheet: {e}"

    ctx.deps.config.output_sheet_url = url
    user_config_path = os.environ.get("USER_CONFIG")
    save_config(
        RegUserConfig(
            tournament_name=ctx.deps.config.tournament_name,
            language=ctx.deps.config.language,
            output_sheet_url=url,
            disciplines=ctx.deps.config.disciplines,
        ),
        user_config_path,
    )
    await _post_to_thread(ctx.deps, "step6-sheet-updated", f"📄 Output sheet URL updated: {url}")
    return f"Output sheet URL updated. Future uploads will use: {url}"


# ── Entry point ────────────────────────────────────────────────────────────────

async def _build_prompt(channel: discord.TextChannel, new_message_content: str) -> str:
    """Build the agent prompt from channel history + the triggering message."""
    # Fetch newest-first, skip index 0 (the trigger message), then reverse to oldest-first
    msgs: list[discord.Message] = []
    async for msg in channel.history(limit=MAX_HISTORY + 1):
        msgs.append(msg)

    msgs = msgs[1:]   # drop the trigger message (already added as "new message")
    msgs.reverse()    # oldest first

    lines = [
        f"{'bot' if msg.author.bot else 'organiser'}: {msg.content}"
        for msg in msgs
    ]
    history = "\n".join(lines) if lines else "(no prior messages)"
    return (
        f"[Channel history — oldest first]\n{history}\n\n"
        f"[New message from organiser]\n{new_message_content}"
    )


@observe(capture_input=False, capture_output=False)
async def run_agent(
    channel: discord.TextChannel,
    new_message_content: str,
    config: RegConfig,
) -> None:
    """Run one agent turn: read channel history, decide next action, post response."""
    thread = await _find_latest_thread(channel)
    thread_index = await _scan_thread(thread) if thread else {}
    deps = AgentDeps(channel=channel, thread=thread, thread_index=thread_index, config=config)
    prompt = await _build_prompt(channel, new_message_content)

    try:
        result = await registration_agent.run(prompt, deps=deps)
        output_str = result.output or ""
        log.info("Agent result: %d chars, %d msgs, preview=%r",
                 len(output_str), len(result.all_messages()), output_str[:300])
        # If the agent replied with plain text instead of calling inform(), post it.
        if output_str.strip():
            await send_long(channel, output_str)
    except Exception:
        log.exception("Agent run failed")
        await channel.send("⚠ Internal error — check logs.")