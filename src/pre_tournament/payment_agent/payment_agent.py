"""The Treasurer — payment matching agent for a HEMA tournament.

Lives in the payments thread and handles the complete payment workflow:
parse → match → review → write to sheet.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import discord
import gspread
import gspread.utils
from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import ModelHTTPError

from pre_tournament.config import PreConfig
from discord_bot.discord_utils import send_long
from pre_tournament.msgs import read_msg, render_msg
from step7_payments import (
    PAYMENTS_MATCHED_FILE,
    PaymentsResult,
    build_fencer_summaries,
    format_payments_report,
    load_all_parsed,
    match_payments,
    parse_amount,
)
from utils import FENCERS_DEDUPED_FILE, load_fencers_list

log = logging.getLogger(__name__)

MAX_HISTORY = 40

# ── Memory helpers (shared with reg_agent) ────────────────────────────────────

_MEMORY_PATH = Path(__file__).parent.parent / "setup_agent" / "setup_memory.md"


def _read_memory() -> str:
    return _MEMORY_PATH.read_text().strip() if _MEMORY_PATH.exists() else "(empty)"


def _read_payment_hints() -> str:
    """Extract [payment-hint] lines from shared memory."""
    memory = _read_memory()
    hints = [
        line[line.index("[payment-hint]") + len("[payment-hint]"):].strip()
        for line in memory.splitlines()
        if "[payment-hint]" in line
    ]
    return "\n".join(hints) if hints else "(none)"


def _append_memory(fact: str) -> None:
    _MEMORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    with _MEMORY_PATH.open("a") as f:
        f.write(f"- [{ts}] {fact}\n")


# ── Agent definition ──────────────────────────────────────────────────────────

@dataclass
class PaymentDeps:
    thread: discord.Thread
    config: PreConfig


payment_agent = Agent(
    "anthropic:claude-sonnet-4-6",
    deps_type=PaymentDeps,
)


@payment_agent.system_prompt
def _system_prompt(ctx: RunContext[PaymentDeps]) -> str:
    lang = ctx.deps.config.language
    hints = _read_payment_hints()
    lang_name = "Czech" if lang == "CS" else "English"
    welcome = read_msg("payment/welcome", lang)
    return render_msg("payment/system_prompt", {
        "welcome": welcome,
        "language": lang_name,
        "hints": hints,
    })


# ── Tools ─────────────────────────────────────────────────────────────────────

@payment_agent.tool
async def tool_match_payments(
    ctx: RunContext[PaymentDeps],
    hints: str | None = None,
) -> str:
    """Aggregate all uploaded payment files and match transactions to registered fencers.

    hints: optional organiser corrections (e.g. "line 8 is Novak", "club X has 50% discount").
    Previously uploaded files are always reused — never ask for a re-upload.
    """
    data_dir = ctx.deps.config.data_dir

    transactions = load_all_parsed(data_dir)
    if not transactions:
        return (
            "No parsed payment files found. "
            "Upload bank export files here (text or CSV, not PDF)."
        )

    fencers = load_fencers_list(data_dir, FENCERS_DEDUPED_FILE)
    if fencers is None:
        return "No fencer data found — the registration pipeline must complete through step 4 first."

    fencer_summaries = build_fencer_summaries(fencers)

    # Combine persistent hints from memory with ad-hoc hints argument
    memory_hints = [
        line[line.index("[payment-hint]") + len("[payment-hint]"):].strip()
        for line in _read_memory().splitlines()
        if "[payment-hint]" in line
    ]
    combined_hints = "\n".join(filter(None, memory_hints + ([hints] if hints else []))) or None

    result = await asyncio.to_thread(
        match_payments, transactions, fencer_summaries, combined_hints, ctx.deps.config
    )

    total = len(result.matched) + len(result.possible) + len(result.unmatched_payments) + len(result.unmatched_fencers)
    if total == 0 and (transactions or fencers):
        return (
            f"ERROR: matching returned empty results for {len(transactions)} transactions "
            f"and {len(fencers)} fencers. This is a bug — do NOT fabricate a report. "
            "Tell the organiser there was a technical error and ask them to retry."
        )

    (data_dir / PAYMENTS_MATCHED_FILE).write_text(
        result.model_dump_json(indent=2), encoding="utf-8"
    )

    fencer_disciplines = {
        f.name: ", ".join(d.str() for d in f.disciplines)
        for f in fencers
    }
    report = format_payments_report(result, fencer_disciplines)
    await send_long(ctx.deps.thread, report)

    return (
        f"Done. {len(result.matched)} matched, {len(result.possible)} possible, "
        f"{len(result.unmatched_payments)} unmatched payments, "
        f"{len(result.unmatched_fencers)} fencers without payment."
    )


@payment_agent.tool
async def tool_write_payments(ctx: RunContext[PaymentDeps]) -> str:
    """Write hi-confidence payment amounts to the Paid column of the Fencers sheet.

    Only hi-confidence matches are written. Low-confidence matches are skipped —
    re-run matching with hints to promote them.
    """
    data_dir = ctx.deps.config.data_dir
    matched_path = data_dir / PAYMENTS_MATCHED_FILE
    if not matched_path.exists():
        return "No payments match file found — run tool_match_payments first."

    result = PaymentsResult.model_validate_json(matched_path.read_text())
    hi_matches = result.matched

    if not hi_matches:
        return "No hi-confidence matches to write."

    if ctx.deps.config.output_sheet_url is None:
        return "No output sheet URL configured — the registration pipeline must complete step 6 first."

    try:
        gc = gspread.service_account(filename=ctx.deps.config.creds_path)
        sh = gc.open_by_url(ctx.deps.config.output_sheet_url)
        ws = sh.worksheet("Fencers")
        all_values = ws.get_all_values()
    except Exception as e:
        return f"Error opening sheet: {e}"

    name_to_row: dict[str, int] = {}
    for i, row in enumerate(all_values[1:], start=2):
        if row and row[1].strip():
            name_to_row[row[1].strip().lower()] = i

    written: list[str] = []
    not_found: list[str] = []

    updates: list[tuple[str, float]] = []
    for match in hi_matches:
        for fname in match.fencer_names:
            row_idx = name_to_row.get(fname.strip().lower())
            if row_idx is None:
                not_found.append(fname)
                continue
            cell = gspread.utils.rowcol_to_a1(row_idx, 7)
            updates.append((cell, parse_amount(match.amount)))
            written.append(fname)

    try:
        for cell, amount in updates:
            ws.update([[amount]], cell)
    except Exception as e:
        return f"Error writing to sheet: {e}"

    parent_channel = ctx.deps.thread.parent
    parent_mention = f"<#{parent_channel.id}>" if parent_channel else "the main channel"

    result_msg = f"Wrote payments for {len(written)} fencer(s): {', '.join(written)}."
    if result.possible:
        result_msg += f"\n{len(result.possible)} possible match(es) skipped — re-run with hints or fix manually."
    if not_found:
        result_msg += f"\nNot found in sheet: {', '.join(not_found)}."
    result_msg += f"\n\nContinue in {parent_mention}."
    return result_msg


@payment_agent.tool
def tool_store_hint(ctx: RunContext[PaymentDeps], text: str) -> str:
    """Store a standing rule that affects all future payment matching runs.

    Use for persistent rules like "club X has 50% discount" or "fee for SA is 600 CZK".
    """
    _append_memory(f"[payment-hint] {text}")
    return f"Stored: {text}"


# ── Prompt builder ────────────────────────────────────────────────────────────

async def _build_prompt(
    thread: discord.Thread,
    new_message_content: str,
) -> str:
    msgs: list[discord.Message] = []
    async for msg in thread.history(limit=MAX_HISTORY + 1):
        msgs.append(msg)
    msgs = msgs[1:]  # skip the newest (it's the one we're replying to)
    msgs.reverse()
    lines = [
        f"{'bot' if msg.author.bot else 'organiser'}: {msg.content}"
        for msg in msgs
    ]
    history = "\n".join(lines) if lines else "(no prior messages)"
    return (
        f"[Thread history — oldest first]\n{history}\n\n"
        f"[New message from organiser]\n{new_message_content}"
    )


# ── Entry point ───────────────────────────────────────────────────────────────

async def run_payment_agent(
    thread: discord.Thread,
    message_content: str,
    config: PreConfig,
) -> None:
    """Run one agent turn in the payments thread."""
    deps = PaymentDeps(thread=thread, config=config)
    prompt = await _build_prompt(thread, message_content)

    try:
        result = await payment_agent.run(prompt, deps=deps)
        output_str = result.output or ""
        log.info(
            "Payment agent result: %d chars, preview=%r",
            len(output_str), output_str[:200],
        )
        if output_str.strip():
            await send_long(thread, output_str)
    except ModelHTTPError as e:
        log.exception("Payment agent run failed")
        if e.status_code == 529:
            await thread.send("Anthropic API is overloaded — please try again in a moment.")
        else:
            await thread.send(f"Anthropic API error ({e.status_code}) — check logs.")
    except Exception:
        log.exception("Payment agent run failed")
        await thread.send("Internal error — check logs.")
