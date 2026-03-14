"""Interactive AI agent backend for the HEMA tournament Discord bot.

The agent guides organisers through the registration enrichment pipeline
one step at a time, with human-in-the-loop approval after each step.
Discord channel history is the sole persistent conversation layer.
"""

import asyncio
import csv
import io
import logging
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

from config import RegConfig
from discord_bot.discord_utils import send_long
from discord_bot.msg_constants import SHEET_ACCESS_REQUEST
from setup_agent.setup_agent import SHARED_MEMORY_PATH
from step1_download import download_registrations
from step2_parse import parse_registrations
from step3_match import match_fencers
from step4_dedup import deduplicate_fencers
from step5_ratings import fetch_ratings
from step6_upload import upload_results
from utils import (
    load_fencers_list,
    load_ratings,
    REG_VER_DIR,
    REG_VER_FILE_PTN,
    REG_VER_FILE_REG,
    FENCERS_PARSED_FILE,
    FENCERS_MATCHED_FILE,
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
1. `tool_download_registrations` — fetch latest registrations from Google Sheet
2. `tool_parse_registrations`    — parse and normalise fencer data
3. `tool_match_fencers`          — fuzzy-match fencers to HEMA Ratings profiles
4. `tool_deduplicate_fencers`    — merge duplicate registrations
5. `tool_fetch_ratings`          — fetch current ratings from hemaratings.com
6. `tool_upload_results`         — write enriched data to the output Google Sheet
7. Payment matching              — **not yet implemented**; mention this to the organiser and skip
8. Group seeding                 — **not yet implemented**; mention this to the organiser and skip

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
    try:
        fencers = await asyncio.to_thread(match_fencers, fencers, ctx.deps.config)
    except Exception as e:
        return f"error: {e}"
    after_unmatched = sum(1 for f in fencers if f.hr_id is None)
    unmatched_names = [f.name for f in fencers if f.hr_id is None]

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
async def tool_deduplicate_fencers(ctx: RunContext[AgentDeps]) -> str:
    """Step 4: Merge duplicate registrations sharing the same hr_id."""
    fencers = load_fencers_list(ctx.deps.config.data_dir, FENCERS_MATCHED_FILE)
    if fencers is None:
        return "No matched fencers found — run tool_match_fencers first."

    if err := _once_per_turn(ctx.deps, "tool_deduplicate_fencers"):
        return err
    before = len(fencers)
    try:
        fencers = await asyncio.to_thread(deduplicate_fencers, fencers, ctx.deps.config)
    except Exception as e:
        return f"error: {e}"
    merged = before - len(fencers)

    rows = [
        [f.name, f.nationality or "—", f.club or "—",
         " / ".join(d.str() for d in f.disciplines), str(f.hr_id) if f.hr_id else "—"]
        for f in fencers
    ]
    await _post_csv_to_thread(ctx.deps, "step4-dedup", f"✅ 4 — {before} → {len(fencers)} fencers ({merged} duplicate{'s' if merged != 1 else ''} merged)",
                              ["Name", "Nationality", "Club", "Disciplines", "HR ID"], rows, "step4_deduped.csv")
    return (
        f"Deduplication done: {before} → {len(fencers)} fencers "
        f"({merged} duplicate{'s' if merged != 1 else ''} merged). "
        f"Thread tag: step4-dedup"
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
async def tool_upload_results(ctx: RunContext[AgentDeps]) -> str:
    """Step 6: Write enriched data to the output Google Sheet."""
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
    try:
        await asyncio.to_thread(upload_results, fencers, ratings, ctx.deps.config)
    except Exception as e:
        return f"error: {e}"
    await _post_to_thread(ctx.deps, "step6-upload", "✅ 6 — upload complete")
    return "Upload complete. Output sheet updated."


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