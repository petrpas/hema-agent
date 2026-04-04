"""Interactive AI agent backend for the HEMA tournament Discord bot.

The agent guides organisers through the registration enrichment pipeline
one step at a time, with human-in-the-loop approval after each step.
Discord channel history is the sole persistent conversation layer.
"""

import asyncio
import csv
import re
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
from pydantic_ai.exceptions import ModelHTTPError

langfuse = get_langfuse_client()

from config import RegConfig, RegUserConfig, save_config
from discord_bot.discord_utils import send_long
from models import FencerRecord
from discord_bot.msg_constants import PAYMENTS_THREAD_INTRO, POOLS_CHANNEL_NAME
from msgs import read_msg as _read_msg, render_msg as _render_msg
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
from step4_5_init import init_fencers_sheet
from step5_ratings import fetch_ratings
from step6_upload import upload_results, create_discipline_worksheets, setup_output_sheet, recalculate_seeds, remove_fencers_from_sheets
from step_typst import render_all as _render_all
from step7_payments import (
    load_all_parsed,
    match_payments,
    format_payments_report,
    PaymentsResult,
    PAYMENTS_MATCHED_FILE,
)
from utils import (
    load_fencers_list,
    save_fencers_list,
    load_ratings,
    load_withdrawn,
    save_withdrawn,
    fuzzy_match_fencers,
    normalize_name,
    WithdrawnEntry,
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

_PAYMENTS_THREAD_PREFIX = "💰"
_PAYMENTS_THREAD_NAMES = {
    "EN": "💰 Payments",
    "CS": "💰 Platby",
}

_TYPST_THREAD_PREFIX = "📸"
_TYPST_THREAD_NAMES = {
    "EN": "📸 Fencers Lists",
    "CS": "📸 Seznamy šermířů",
}



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
    sheet_access = _render_msg("shared/sheet_access_request", {"bot_email": bot_email}, lang)
    reg_complete = _read_msg("reg/complete", lang)  # contains <<CHANNEL>> placeholder
    return _render_msg("reg/system_prompt", {
        "tournament_name": ctx.deps.config.tournament_name,
        "sheet_access_request": sheet_access,
        "reg_complete": reg_complete,
        "memory": _read_memory(),
    }, lang)


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


async def _find_payments_thread(channel: discord.TextChannel) -> discord.Thread | None:
    """Return the most recent payments thread, unarchiving if needed."""
    candidates = [t for t in channel.threads if t.name.startswith(_PAYMENTS_THREAD_PREFIX)]
    if candidates:
        return max(candidates, key=lambda t: t.created_at)
    async for t in channel.archived_threads(limit=10):
        if t.name.startswith(_PAYMENTS_THREAD_PREFIX):
            await t.edit(archived=False)
            return t
    return None


async def _create_payments_thread(channel: discord.TextChannel, lang: str = "EN") -> discord.Thread | None:
    """Create the payments thread and post the intro message."""
    try:
        thread_name = _PAYMENTS_THREAD_NAMES.get(lang, _PAYMENTS_THREAD_NAMES["EN"])
        intro = PAYMENTS_THREAD_INTRO.get(lang, PAYMENTS_THREAD_INTRO["EN"])
        anchor = await channel.send(thread_name)
        thread = await anchor.create_thread(
            name=thread_name,
            auto_archive_duration=1440,
        )
        await thread.send(intro)
        return thread
    except Exception:
        log.exception("Failed to create payments thread")
        return None


async def _find_typst_thread(channel: discord.TextChannel) -> discord.Thread | None:
    """Return the most recent typst/lists thread, unarchiving if needed."""
    candidates = [t for t in channel.threads if t.name.startswith(_TYPST_THREAD_PREFIX)]
    if candidates:
        return max(candidates, key=lambda t: t.created_at)
    async for t in channel.archived_threads(limit=10):
        if t.name.startswith(_TYPST_THREAD_PREFIX):
            await t.edit(archived=False)
            return t
    return None


async def _ensure_typst_thread(channel: discord.TextChannel, lang: str) -> discord.Thread | None:
    """Return existing typst thread or create one."""
    thread = await _find_typst_thread(channel)
    if thread is not None:
        return thread
    try:
        name = _TYPST_THREAD_NAMES.get(lang, _TYPST_THREAD_NAMES["EN"])
        anchor = await channel.send(name)
        thread = await anchor.create_thread(name=name, auto_archive_duration=1440)
        return thread
    except Exception:
        log.exception("Failed to create typst thread")
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
def list_worksheets(ctx: RunContext[AgentDeps], sheet_url: str) -> str:
    """List all worksheet (tab) names in a Google Sheet.

    Use this before tool_download_registrations when the worksheet name is not known,
    so you can pick the correct tab without guessing.
    """
    import gspread
    try:
        gc = gspread.service_account(filename=ctx.deps.config.creds_path)
        sh = gc.open_by_url(sheet_url)
        names = [ws.title for ws in sh.worksheets()]
        return ", ".join(names)
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
    If the worksheet name is unknown, call list_worksheets first rather than guessing.
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
                    single_row=(section == "confirmed"),
                ):
                    await thread.send(chunk)
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
def tool_search_hr_profile(ctx: RunContext[AgentDeps], name: str) -> str:
    """Search the local HEMA Ratings fighters list for profiles matching a name.

    Uses diacritic-insensitive fuzzy matching — useful when the registered name
    differs from the HEMA Ratings profile name (e.g. missing diacritics, typos).
    Returns up to 10 closest matches with hr_id, name, nationality, and club.
    The fighters list must have been downloaded during step 3.
    """
    import difflib as _difflib
    try:
        fighters_text = _get_fighters_compact(ctx.deps.config.data_dir)
    except Exception as e:
        return f"error loading fighters list: {e}"

    index: list[tuple[str, str]] = []  # (normalized_name, original_line)
    for line in fighters_text.splitlines():
        parts = line.split(";", 3)
        if len(parts) >= 2:
            index.append((_normalize(parts[1]), line))

    query = _normalize(name)
    all_norms = [item[0] for item in index]
    close = _difflib.get_close_matches(query, all_norms, n=10, cutoff=0.5)

    # Also include any line where the last token of the query appears in the name
    tokens = query.split()
    if tokens:
        surname = tokens[-1]
        close_set = set(close)
        for norm, line in index:
            if surname in norm and norm not in close_set:
                close.append(norm)
                if len(close) >= 10:
                    break

    if not close:
        return f"No profiles found matching '{name}'."

    results = []
    seen: set[str] = set()
    for norm_name in close[:10]:
        for n, line in index:
            if n == norm_name and line not in seen:
                seen.add(line)
                parts = line.split(";", 3)
                hr_id, hr_name = parts[0], parts[1]
                nat = parts[2] if len(parts) > 2 else ""
                club = parts[3] if len(parts) > 3 else ""
                results.append(f"hr_id={hr_id}  {hr_name}  [{nat}]  {club}")
                break

    return "\n".join(results)


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

    # Also patch fencers_deduped.json so upload_results sees the corrected hr_id immediately.
    deduped = load_fencers_list(data_dir, FENCERS_DEDUPED_FILE)
    if deduped is not None:
        corrected_name_lower = normalize_name(fencer_name)
        for i, f in enumerate(deduped):
            if normalize_name(f.name) == corrected_name_lower:
                deduped[i] = f.model_copy(update={"hr_id": correct_hr_id})
                break
        save_fencers_list(deduped, data_dir / FENCERS_DEDUPED_FILE)

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
async def tool_init_fencers_sheet(ctx: RunContext[AgentDeps]) -> str:
    """Step 4.5: Initialize the Fencers worksheet in the output sheet.

    If no output sheet URL is set yet, creates a blank sheet in the configured Drive
    folder and asks the organiser to make a copy, share it, and paste the link back.
    Once the URL is set (via tool_set_output_sheet), writes the Fencers worksheet with
    a dynamic header and all deduplicated fencer data.
    """
    fencers = load_fencers_list(ctx.deps.config.data_dir, FENCERS_DEDUPED_FILE)
    if fencers is None:
        return "No deduplicated fencers found — run tool_deduplicate_fencers first."

    if not ctx.deps.config.output_sheet_url:
        bot_email = _bot_email(ctx.deps.config)
        lang = ctx.deps.config.language
        try:
            url = await asyncio.to_thread(setup_output_sheet, ctx.deps.config)
        except Exception as e:
            log.error("setup_output_sheet failed: %s", e, exc_info=True)
            return f"error creating output sheet: {e}"
        await ctx.deps.channel.send(
            _render_msg("shared/sheet_clone_request", {"url": url, "bot_email": bot_email}, lang)
        )
        return (
            f"Output sheet created at {url}. "
            "Clone request with all instructions has been posted to the organiser. "
            "Output only: \"⏳ Waiting for the link to your copy.\""
        )

    if err := _once_per_turn(ctx.deps, "tool_init_fencers_sheet"):
        return err

    try:
        await asyncio.to_thread(init_fencers_sheet, fencers, ctx.deps.config)
    except Exception as e:
        log.error("init_fencers_sheet failed: %s", e, exc_info=True)
        return f"error initializing Fencers sheet: {e}"

    await _post_to_thread(ctx.deps, "step45-fencers", "✅ 4.5 — Fencers sheet initialized")
    return f"Fencers sheet initialized with {len(fencers)} fencers. Output sheet: {ctx.deps.config.output_sheet_url}"


@registration_agent.tool
async def tool_fetch_ratings(ctx: RunContext[AgentDeps]) -> str:
    """Step 5: Fetch current ratings and rankings from hemaratings.com."""
    fencers = load_fencers_list(ctx.deps.config.data_dir, FENCERS_DEDUPED_FILE)
    if fencers is None:
        return "No deduplicated fencers found — run tool_deduplicate_fencers first."

    if err := _once_per_turn(ctx.deps, "tool_fetch_ratings"):
        return err
    try:
        ratings, not_found = await asyncio.to_thread(fetch_ratings, fencers, ctx.deps.config)
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

    result = f"Fetched ratings for {rated}/{total_with_id} fencers with hr_id. Thread tag: step5-ratings"
    if not_found:
        id_to_name = {f.hr_id: f.name for f in fencers if f.hr_id is not None}
        not_found_desc = ", ".join(
            f"{id_to_name.get(hr_id, '?')} (hr_id={hr_id})" for hr_id in sorted(not_found)
        )
        result += (
            f" WARNING: {len(not_found)} profile(s) returned HTTP 404 and were skipped (rank=9999): {not_found_desc}. "
            f"The hr_id is likely wrong — call tool_correct_match to fix it."
        )

    if ctx.deps.config.output_sheet_url:
        import gspread as _gspread
        try:
            gc = _gspread.service_account(filename=ctx.deps.config.creds_path)
            sh = gc.open_by_url(ctx.deps.config.output_sheet_url)
            await asyncio.to_thread(create_discipline_worksheets, ctx.deps.config, sh, fencers, ratings)
            await _post_to_thread(ctx.deps, "step5-disciplines", "📋 5 — discipline worksheets created")
        except Exception as e:
            log.warning("create_discipline_worksheets failed: %s", e)
            result += f" (discipline worksheet creation failed: {e})"

    return result


@registration_agent.tool
async def tool_upload_results(ctx: RunContext[AgentDeps]) -> str:
    """Step 6: Sync enriched data (including ratings) to the output Google Sheet.

    Requires step 4.5 (init_fencers_sheet) and step 5 (fetch_ratings) to have run first.
    """
    fencers = load_fencers_list(ctx.deps.config.data_dir, FENCERS_DEDUPED_FILE)
    if fencers is None:
        return "No deduplicated fencers found — run tool_deduplicate_fencers first."

    if not ctx.deps.config.output_sheet_url:
        return "No output sheet URL set — run tool_init_fencers_sheet first."

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
        log.error("upload_results failed: %s", e, exc_info=True)
        return f"error: {e}"
    await _post_to_thread(ctx.deps, "step6-upload", "✅ 6 — upload complete")
    return f"Upload complete. Output sheet: {ctx.deps.config.output_sheet_url}"


@registration_agent.tool
async def tool_recalculate_seeds(ctx: RunContext[AgentDeps]) -> str:
    """Recalculate the Seed column in all discipline worksheets.

    Call when the organiser requests a seed recalculation, e.g. after manually editing
    HRank values. Seeds are assigned 1..N by ascending HRank; unranked fencers follow
    in their row order (registration order).
    """
    if not ctx.deps.config.output_sheet_url:
        return "No output sheet URL set — complete step 4.5 first."

    import gspread as _gs
    try:
        gc = _gs.service_account(filename=ctx.deps.config.creds_path)
        sh = gc.open_by_url(ctx.deps.config.output_sheet_url)
    except Exception as e:
        return f"error opening sheet: {e}"

    results = []
    for code in ctx.deps.config.disciplines:
        try:
            ws = sh.worksheet(code)
            await asyncio.to_thread(recalculate_seeds, ws)
            results.append(f"{code} ✓")
        except Exception as e:
            log.error("recalculate_seeds failed for %s: %s", code, e, exc_info=True)
            results.append(f"{code} ✗ {e}")

    return "Seeds recalculated: " + ", ".join(results)


@registration_agent.tool
async def tool_remove_fencers(
    ctx: RunContext[AgentDeps],
    names: list[str],
    confirmed: bool = False,
) -> str:
    """Withdraw fencers who will no longer attend.

    Call first with confirmed=False to fuzzy-match the names and get a confirmation
    summary. Then call again with confirmed=True and the exact matched names to execute.

    On confirmation: adds fencers to the withdrawn list (so re-running the pipeline
    never re-adds them) and deletes their rows from all worksheets.
    """
    fencers = load_fencers_list(ctx.deps.config.data_dir, FENCERS_DEDUPED_FILE)
    if fencers is None:
        return "No fencer data found — run the pipeline through step 4 first."

    if not confirmed:
        lines = []
        for query in names:
            matches = fuzzy_match_fencers(query, fencers)
            if not matches:
                lines.append(f'• "{query}" — no match found')
            else:
                candidates = ", ".join(
                    f"{f.name} (HR_ID={f.hr_id})" for f in matches[:3]
                )
                lines.append(f'• "{query}" → {candidates}')
        return (
            "Fuzzy match results:\n" + "\n".join(lines) +
            "\n\nCall again with the exact matched names and confirmed=True to proceed."
        )

    # confirmed=True — execute withdrawal
    withdrawn = load_withdrawn(ctx.deps.config.data_dir)
    existing_names = {w.name.lower() for w in withdrawn}

    newly_withdrawn: list[WithdrawnEntry] = []
    not_in_data: list[str] = []
    for name in names:
        match = next((f for f in fencers if normalize_name(f.name) == normalize_name(name)), None)
        if match is None:
            not_in_data.append(name)
        elif match.name.lower() not in existing_names:
            newly_withdrawn.append(WithdrawnEntry(name=match.name, hr_id=match.hr_id))

    save_withdrawn(withdrawn + newly_withdrawn, ctx.deps.config.data_dir)

    sheet_result: dict = {"removed": [], "not_found": names}
    if ctx.deps.config.output_sheet_url:
        try:
            sheet_result = await asyncio.to_thread(
                remove_fencers_from_sheets, names, ctx.deps.config
            )
        except Exception as e:
            log.error("remove_fencers_from_sheets failed: %s", e, exc_info=True)
            return f"Withdrawn list updated but sheet removal failed: {e}"

    parts = []
    if sheet_result["removed"]:
        parts.append(f"Removed from sheets: {', '.join(sheet_result['removed'])}")
    if sheet_result["not_found"]:
        parts.append(f"Not found in sheets: {', '.join(sheet_result['not_found'])}")
    if not_in_data:
        parts.append(f"Not found in fencer data: {', '.join(not_in_data)}")
    parts.append(f"Withdrawn list now has {len(withdrawn) + len(newly_withdrawn)} fencer(s).")
    return " | ".join(parts)


@registration_agent.tool
async def tool_unwithdraw_fencers(
    ctx: RunContext[AgentDeps],
    names: list[str],
    confirmed: bool = False,
) -> str:
    """Re-admit previously withdrawn fencers.

    Call first with confirmed=False to see who would be un-withdrawn.
    Call again with confirmed=True and exact names to execute.

    After un-withdrawing, the organiser must re-run step 6 to add the fencers
    back to the sheets.
    """
    withdrawn = load_withdrawn(ctx.deps.config.data_dir)
    if not withdrawn:
        return "No withdrawn fencers on record."

    if not confirmed:
        lines = []
        for query in names:
            matches = [w for w in withdrawn if query.lower() in w.name.lower()]
            if not matches:
                lines.append(f'• "{query}" — not in withdrawn list')
            else:
                candidates = ", ".join(
                    f"{w.name} (HR_ID={w.hr_id})" for w in matches[:3]
                )
                lines.append(f'• "{query}" → {candidates}')
        return (
            "Withdrawn list matches:\n" + "\n".join(lines) +
            "\n\nCall again with the exact matched names and confirmed=True to proceed."
        )

    # confirmed=True — remove from withdrawn list
    names_norm = {normalize_name(n) for n in names}
    remaining = [w for w in withdrawn if normalize_name(w.name) not in names_norm]
    restored = [w for w in withdrawn if normalize_name(w.name) in names_norm]

    save_withdrawn(remaining, ctx.deps.config.data_dir)

    if not restored:
        return "None of the provided names matched the withdrawn list."
    names_str = ", ".join(w.name for w in restored)
    return (
        f"Removed from withdrawn list: {names_str}. "
        f"Re-run step 6 to add them back to the sheets."
    )


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
            discipline_limits=ctx.deps.config.discipline_limits,
        ),
        user_config_path,
    )
    await _post_to_thread(ctx.deps, "step6-sheet-updated", f"📄 Output sheet URL updated: {url}")
    return f"Output sheet URL updated. Future uploads will use: {url}"


@registration_agent.tool
async def tool_open_payments_thread(ctx: RunContext[AgentDeps]) -> str:
    """Step 7a: Ensure the 💰 Payments thread exists and return a Discord mention link.

    Call this first when entering step 7 so the organiser has a clickable link to the thread
    where they should upload their bank export files.
    """
    channel = ctx.deps.channel
    lang = ctx.deps.config.language
    thread = await _find_payments_thread(channel)
    if thread is None:
        thread = await _create_payments_thread(channel, lang)
    if thread is None:
        return "Could not create the payments thread — check bot permissions."
    return f"<#{thread.id}>"


@registration_agent.tool
async def tool_process_payments(
    ctx: RunContext[AgentDeps],
    hints: str | None = None,
) -> str:
    """Step 7b: Aggregate all parsed payment files and match to fencers.

    hints: optional organiser corrections injected into the matcher (e.g. "line 12 is David Brown").
    Files must already be uploaded to the 💰 Payments thread and auto-parsed before calling this.
    """
    data_dir = ctx.deps.config.data_dir

    transactions = load_all_parsed(data_dir)
    if not transactions:
        return (
            "No parsed payment files found. "
            "Ask the organiser to upload bank export files in the 💰 Payments thread."
        )

    fencers = load_fencers_list(data_dir, FENCERS_DEDUPED_FILE)
    if fencers is None:
        return "No fencer data found — complete the pipeline through step 4 first."

    fencer_summaries = [
        {
            "name": f.name,
            "club": f.club or "unknown",
            "disciplines": ", ".join(d.str() for d in f.disciplines),
            "afterparty": f.after_party or "unknown",
            "borrow": ", ".join(str(w) for w in f.borrow) if f.borrow else "none",
        }
        for f in fencers
    ]

    # Combine persistent [payment-hint] lines from memory with any ad-hoc hints argument
    memory_hints = [
        line[line.index("[payment-hint]") + len("[payment-hint]"):].strip()
        for line in _read_memory().splitlines()
        if "[payment-hint]" in line
    ]
    combined_hints = "\n".join(filter(None, memory_hints + ([hints] if hints else []))) or None

    try:
        result = await asyncio.to_thread(
            match_payments, transactions, fencer_summaries, combined_hints, ctx.deps.config
        )
    except Exception as e:
        return f"error matching payments: {e}"

    (data_dir / PAYMENTS_MATCHED_FILE).write_text(result.model_dump_json(indent=2))

    fencer_disciplines = {
        f.name: ", ".join(d.str() for d in f.disciplines)
        for f in fencers
    }
    report = format_payments_report(result, fencer_disciplines)
    thread = await _find_payments_thread(ctx.deps.channel)
    if thread is None:
        thread = await _create_payments_thread(ctx.deps.channel, ctx.deps.config.language)
    if thread is not None:
        await send_long(thread, report)

    return (
        f"Done. Results in 💰 Payments — "
        f"{len(result.matched)} matched, {len(result.possible)} possible, "
        f"{len(result.unmatched_payments)} unmatched payments."
    )


def _parse_amount(amount_str: str) -> float:
    """Strip currency and convert to float. Handles both European (600,05) and English (600.05) formats."""
    digits = re.sub(r"[^\d.,]", "", amount_str)
    if "," in digits and "." in digits:
        digits = digits.replace(",", "")   # comma = thousands separator
    else:
        digits = digits.replace(",", ".")  # comma = decimal separator (Czech/European)
    return float(digits)


@registration_agent.tool
async def tool_write_payments(ctx: RunContext[AgentDeps]) -> str:
    """Step 7b: Write hi-confidence payment amounts to the Paid column (col 7) of the Fencers sheet.

    Loads payments_matched.json and writes only hi-confidence matches.
    Low-confidence matches are skipped — organiser should re-run with hints or fix manually.
    """
    import gspread
    import gspread.utils

    data_dir = ctx.deps.config.data_dir
    matched_path = data_dir / PAYMENTS_MATCHED_FILE
    if not matched_path.exists():
        return "No payments match file found — run tool_process_payments first."

    result = PaymentsResult.model_validate_json(matched_path.read_text())
    hi_matches = result.matched  # only hi-confidence

    if not hi_matches:
        return "No hi-confidence matches to write."

    if ctx.deps.config.output_sheet_url is None:
        return "No output sheet URL configured — complete step 6 first."

    try:
        gc = gspread.service_account(filename=ctx.deps.config.creds_path)
        sh = gc.open_by_url(ctx.deps.config.output_sheet_url)
        ws = sh.worksheet("Fencers")
        all_values = ws.get_all_values()
    except Exception as e:
        return f"error opening sheet: {e}"

    # Build name → row index (1-based, row 1 = header)
    name_to_row: dict[str, int] = {}
    for i, row in enumerate(all_values[1:], start=2):  # skip header
        if row and row[1].strip():  # col 2 (index 1) = Name
            name_to_row[row[1].strip().lower()] = i

    written: list[str] = []
    not_found: list[str] = []

    updates: list[tuple[str, float]] = []  # (A1 cell, numeric amount)
    for match in hi_matches:
        for fname in match.fencer_names:
            row_idx = name_to_row.get(fname.strip().lower())
            if row_idx is None:
                not_found.append(fname)
                continue
            cell = gspread.utils.rowcol_to_a1(row_idx, 7)  # col 7 = Paid
            updates.append((cell, _parse_amount(match.amount)))
            written.append(fname)

    try:
        for cell, amount in updates:
            ws.update([[amount]], cell)
    except Exception as e:
        return f"error writing to sheet: {e}"

    thread = await _find_payments_thread(ctx.deps.channel)
    if thread is None:
        thread = await _create_payments_thread(ctx.deps.channel, ctx.deps.config.language)
    if thread is not None:
        skip_count = len(result.possible)
        msg = (
            f"✅ Wrote payments for {len(written)} fencer(s): {', '.join(written)}."
            + (f"\n⚠️ {skip_count} possible match(es) skipped — re-run with hints or fix manually." if skip_count else "")
            + (f"\n❓ Not found in sheet: {', '.join(not_found)}." if not_found else "")
            + f"\n\n➡️ Continue in <#{ctx.deps.channel.id}>"
        )
        await send_long(thread, msg)

    result_msg = f"Wrote payments for {len(written)} fencer(s)."
    if result.possible:
        result_msg += f" {len(result.possible)} possible match(es) need manual review."
    if not_found:
        result_msg += f" Not found in sheet: {', '.join(not_found)}."
    return result_msg


# ── Pipeline finale helpers ────────────────────────────────────────────────────

@registration_agent.tool
async def tool_create_pools_channel(ctx: RunContext[AgentDeps]) -> str:
    """Create the #hsq-pools-alchemy channel for pool setup and return a Discord mention link.

    Creates the channel if it does not exist. Returns <#channel_id> so the agent
    can post a clickable mention directly in the registration channel.
    """
    guild = ctx.deps.channel.guild
    if guild is None:
        return "error: not in a guild"
    existing = discord.utils.get(guild.text_channels, name=POOLS_CHANNEL_NAME)
    if existing is not None:
        return f"<#{existing.id}>"
    try:
        ch = await guild.create_text_channel(POOLS_CHANNEL_NAME)
        log.info("Created #%s in guild %s", POOLS_CHANNEL_NAME, guild)
    except Exception as e:
        log.error("Failed to create #%s: %s", POOLS_CHANNEL_NAME, e)
        return f"error creating channel: {e}"
    return f"<#{ch.id}>"


@registration_agent.tool
async def tool_generate_social_media_list(ctx: RunContext[AgentDeps]) -> str:
    """Render PNG participant lists (overall fencer list + one per discipline) and post them.

    Uses Typst to compile fencers.typ and disciplines.typ templates. PNGs are saved to
    data/{tournament}/lists/ and posted to the dedicated 📸 thread. Each call appends a
    new post — previous renders are preserved.

    Requires the output sheet to be set and accessible (steps 1-6 complete).
    """
    try:
        png_paths = await asyncio.get_event_loop().run_in_executor(
            None, _render_all, ctx.deps.config
        )
    except ValueError as e:
        return f"error: {e}"
    except Exception as e:
        log.exception("render_all failed")
        return f"error rendering lists: {e}"

    if not png_paths:
        return "error: no PNG files produced — check typst compilation logs."

    thread = await _ensure_typst_thread(ctx.deps.channel, ctx.deps.config.language)
    if thread is None:
        return f"Rendered {len(png_paths)} PNG(s) but could not post to thread — files saved to data/{ctx.deps.config.tournament_name}/lists/."

    files = [discord.File(str(p), filename=p.name) for p in png_paths]
    # Discord allows up to 10 files per message; send in batches
    batch_size = 10
    for i in range(0, len(files), batch_size):
        batch = files[i:i + batch_size]
        await thread.send(files=batch)

    return f"Posted {len(png_paths)} PNG(s) to the 📸 thread."


@registration_agent.tool
async def tool_set_discipline_limit(ctx: RunContext[AgentDeps], discipline_code: str, limit: int) -> str:
    """Update the participant capacity limit for one discipline and persist it.

    discipline_code: e.g. "LS", "LSW". limit: maximum number of accepted fencers.
    """
    if discipline_code not in ctx.deps.config.disciplines:
        known = ", ".join(ctx.deps.config.disciplines.keys()) or "(none configured)"
        return f"error: unknown discipline '{discipline_code}'. Known: {known}"

    ctx.deps.config.discipline_limits[discipline_code] = limit
    user_config_path = os.environ.get("USER_CONFIG")
    save_config(
        RegUserConfig(
            tournament_name=ctx.deps.config.tournament_name,
            language=ctx.deps.config.language,
            output_sheet_url=ctx.deps.config.output_sheet_url,
            disciplines=ctx.deps.config.disciplines,
            discipline_limits=ctx.deps.config.discipline_limits,
        ),
        user_config_path,
    )
    return f"Limit for {discipline_code} set to {limit}."


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
    response_channel: discord.abc.Messageable | None = None,
) -> None:
    """Run one agent turn: read channel history, decide next action, post response."""
    reply_to: discord.abc.Messageable = response_channel or channel
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
            await send_long(reply_to, output_str)
    except ModelHTTPError as e:
        log.exception("Agent run failed")
        if e.status_code == 529:
            await reply_to.send("⚠ Anthropic API is overloaded — please try again in a moment.")
        else:
            await reply_to.send(f"⚠ Anthropic API error ({e.status_code}) — check logs.")
    except Exception:
        log.exception("Agent run failed")
        await reply_to.send("⚠ Internal error — check logs.")