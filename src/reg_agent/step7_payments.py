"""Step 7: Payment matching — parse bank export, match to registered fencers, report."""

import json
import logging
import unicodedata
from pathlib import Path
from typing import Literal

from pydantic import BaseModel
from pydantic_ai import Agent, ModelSettings

from config import RegConfig, Step
from msgs import read_msg, render_msg

logger = logging.getLogger(__name__)


def _vlen(s: str) -> int:
    """Visual column width of s in a monospace font (wide/emoji chars count as 2)."""
    w = 0
    i = 0
    while i < len(s):
        ch = s[i]
        # U+FE0F variation selector forces the preceding char to render as an emoji (2 wide)
        if i + 1 < len(s) and s[i + 1] == "\ufe0f":
            w += 2
            i += 2
            continue
        ea = unicodedata.east_asian_width(ch)
        w += 2 if ea in ("W", "F") else 1
        i += 1
    return w


PAYMENTS_DIR = "payments"
PAYMENTS_RAW_DIR = "payments/raw"
PAYMENTS_PARSED_DIR = "payments/parsed"
PAYMENTS_MATCHED_FILE = "payments/matched.json"


# ── Data models ────────────────────────────────────────────────────────────────

class ParsedTransaction(BaseModel):
    line_no: int
    date: str
    sender_name: str
    reference: str
    amount: str   # e.g. "€150.00"
    notes: str


class ParsedTransactionList(BaseModel):
    transactions: list[ParsedTransaction]


class PaymentMatch(BaseModel):
    payment_line_no: int
    fencer_names: list[str]   # ≥1; multiple if one payer covers several fencers
    confidence: Literal["hi", "low"]
    amount: str
    remark: str               # reasoning for the match


class PaymentsResult(BaseModel):
    matched: list[PaymentMatch]
    possible: list[PaymentMatch]
    unmatched_payments: list[ParsedTransaction]
    unmatched_fencers: list[str]


# ── Prompts loaded from msgs/ ──────────────────────────────────────────────────


# ── LLM calls ─────────────────────────────────────────────────────────────────

def parse_transactions(raw_content: str, config: RegConfig) -> list[ParsedTransaction]:
    """Call 1 (Haiku): Parse raw bank export → list[ParsedTransaction]."""
    agent = Agent(
        model=config.model(Step.PAYMENTS_PARSE),
        model_settings=ModelSettings(temperature=0.0),
        output_type=ParsedTransactionList,
        system_prompt=read_msg("step7_parse_system"),
        retries=3,
    )
    result = agent.run_sync(raw_content)
    txns = result.output.transactions
    logger.info("Parsed %d transactions from raw content", len(txns))
    return txns


def match_payments(
    transactions: list[ParsedTransaction],
    fencer_summaries: list[dict],
    hints: str | None,
    config: RegConfig,
) -> PaymentsResult:
    """Call 2 (Sonnet): Match parsed transactions to fencers → PaymentsResult."""
    summary_lines = "\n".join(
        "{name} | {club} | {disciplines} | afterparty={afterparty} | borrow={borrow}".format(**s)
        for s in fencer_summaries
    )
    agent = Agent(
        model=config.model(Step.PAYMENTS_MATCH),
        model_settings=ModelSettings(temperature=0.0),
        output_type=PaymentsResult,
        system_prompt=render_msg("step7_match_system", {"fencer_summaries": summary_lines, "hints": hints}),
        retries=3,
    )
    txn_json = json.dumps([t.model_dump() for t in transactions], ensure_ascii=False, indent=2)
    result = agent.run_sync(
        f"Match these {len(transactions)} transactions to the fencer list:\n\n"
        f"```json\n{txn_json}\n```"
    )
    pr = result.output
    logger.info(
        "Match result: %d matched, %d possible, %d unmatched payments, %d unmatched fencers",
        len(pr.matched), len(pr.possible), len(pr.unmatched_payments), len(pr.unmatched_fencers),
    )
    return pr


# ── Formatting ─────────────────────────────────────────────────────────────────

def parse_and_store(raw_content: str, filename: str, data_dir: Path, config: RegConfig) -> list[ParsedTransaction]:
    """Parse one raw bank export file and persist both raw and parsed versions."""
    raw_dir = data_dir / PAYMENTS_RAW_DIR
    parsed_dir = data_dir / PAYMENTS_PARSED_DIR
    raw_dir.mkdir(parents=True, exist_ok=True)
    parsed_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / filename).write_text(raw_content, encoding="utf-8")
    transactions = parse_transactions(raw_content, config)
    stem = Path(filename).stem
    (parsed_dir / f"{stem}.json").write_text(
        ParsedTransactionList(transactions=transactions).model_dump_json(indent=2),
        encoding="utf-8",
    )
    return transactions


def load_all_parsed(data_dir: Path) -> list[ParsedTransaction]:
    """Aggregate transactions from all parsed/*.json files."""
    parsed_dir = data_dir / PAYMENTS_PARSED_DIR
    if not parsed_dir.exists():
        return []
    all_txns: list[ParsedTransaction] = []
    for f in sorted(parsed_dir.glob("*.json")):
        all_txns.extend(ParsedTransactionList.model_validate_json(f.read_text()).transactions)
    return all_txns


def _report_header(result: "PaymentsResult") -> str:
    n_m = len(result.matched)
    n_p = len(result.possible)
    n_up = len(result.unmatched_payments)
    n_uf = len(result.unmatched_fencers)
    return (
        "## 💰 Payment matching\n"
        f"✅ {n_m} matched   ⚠️ {n_p} possible   "
        f"❌ {n_up} unmatched payment{'s' if n_up != 1 else ''}   👤 {n_uf} no payment"
    )


def _matches_table(matches: "list[PaymentMatch]") -> str:
    if not matches:
        return "(none)"
    W_LINE, W_FENCER, W_AMOUNT, W_CONF = 4, 28, 10, 2

    def _cell(s: str, w: int, align: str = "left") -> str:
        vl = _vlen(s)
        if vl > w:
            s = s[:w - 1] + "…"
            vl = w
        pad = w - vl
        if align == "right":
            body = " " * pad + s
        elif align == "center":
            body = " " * (pad // 2) + s + " " * (pad - pad // 2)
        else:
            body = s + " " * pad
        return f" {body} "

    def _row(line: str, fencer: str, amount: str, conf: str) -> str:
        return (
            "│" + _cell(line, W_LINE, "right")
            + "│" + _cell(fencer, W_FENCER)
            + "│" + _cell(amount, W_AMOUNT, "right")
            + "│" + _cell(conf, W_CONF, "center")
            + "│"
        )

    def _rule(L: str, M: str, R: str) -> str:
        segs = [W_LINE + 2, W_FENCER + 2, W_AMOUNT + 2, W_CONF + 2]
        return L + M.join("─" * w for w in segs) + R

    rows = [
        _rule("┌", "┬", "┐"),
        _row("#", "Fencer(s)", "Amount", ""),
        _rule("├", "┼", "┤"),
    ]
    for m in matches:
        names = ", ".join(m.fencer_names)
        conf = "✅" if m.confidence == "hi" else "⚠️"
        rows.append(_row(str(m.payment_line_no), names, m.amount, conf))
    rows.append(_rule("└", "┴", "┘"))
    return "```\n" + "\n".join(rows) + "\n```"


def _match_notes(possible: "list[PaymentMatch]") -> str:
    if not possible:
        return ""
    lines = ["**Notes on low-confidence matches:**"]
    for m in possible:
        lines.append(f"⚠️ **line {m.payment_line_no}** — {m.remark}")
    return "\n".join(lines)


def _unmatched_payments_table(txns: "list[ParsedTransaction]") -> str:
    if not txns:
        return "(none)"
    W_LINE, W_SENDER, W_AMOUNT, W_REF = 4, 22, 10, 24

    def _cell(s: str, w: int, align: str = "left") -> str:
        vl = _vlen(s)
        if vl > w:
            s = s[:w - 1] + "…"
            vl = w
        pad = w - vl
        if align == "right":
            body = " " * pad + s
        elif align == "center":
            body = " " * (pad // 2) + s + " " * (pad - pad // 2)
        else:
            body = s + " " * pad
        return f" {body} "

    def _row(line: str, sender: str, amount: str, ref: str) -> str:
        return (
            "│" + _cell(line, W_LINE, "right")
            + "│" + _cell(sender, W_SENDER)
            + "│" + _cell(amount, W_AMOUNT, "right")
            + "│" + _cell(ref, W_REF)
            + "│"
        )

    def _rule(L: str, M: str, R: str) -> str:
        segs = [W_LINE + 2, W_SENDER + 2, W_AMOUNT + 2, W_REF + 2]
        return L + M.join("─" * w for w in segs) + R

    rows = [
        _rule("┌", "┬", "┐"),
        _row("#", "Sender", "Amount", "Reference"),
        _rule("├", "┼", "┤"),
    ]
    for t in txns:
        rows.append(_row(str(t.line_no), t.sender_name, t.amount, t.reference))
    rows.append(_rule("└", "┴", "┘"))
    return "```\n" + "\n".join(rows) + "\n```"


def _no_payment_table(names: "list[str]", fencer_disciplines: dict[str, str]) -> str:
    if not names:
        return "(none)"
    W_NAME, W_DISC = 32, 16

    def _cell(s: str, w: int, align: str = "left") -> str:
        vl = _vlen(s)
        if vl > w:
            s = s[:w - 1] + "…"
            vl = w
        pad = w - vl
        if align == "right":
            body = " " * pad + s
        elif align == "center":
            body = " " * (pad // 2) + s + " " * (pad - pad // 2)
        else:
            body = s + " " * pad
        return f" {body} "

    def _row(name: str, disc: str) -> str:
        return "│" + _cell(name, W_NAME) + "│" + _cell(disc, W_DISC) + "│"

    def _rule(L: str, M: str, R: str) -> str:
        segs = [W_NAME + 2, W_DISC + 2]
        return L + M.join("─" * w for w in segs) + R

    rows = [
        _rule("┌", "┬", "┐"),
        _row("Fencer", "Disciplines"),
        _rule("├", "┼", "┤"),
    ]
    for name in names:
        disc = fencer_disciplines.get(name, "—")
        rows.append(_row(name, disc))
    rows.append(_rule("└", "┴", "┘"))
    return "```\n" + "\n".join(rows) + "\n```"


def format_payments_report(
    result: "PaymentsResult",
    fencer_disciplines: dict[str, str] | None = None,
) -> str:
    """Render a PaymentsResult as a human-readable Discord message."""
    parts = [_report_header(result)]
    parts.append("### Payments")
    parts.append(_matches_table(result.matched + result.possible))
    notes = _match_notes(result.possible)
    if notes:
        parts.append(notes)
    parts.append("### Unmatched payments")
    parts.append(_unmatched_payments_table(result.unmatched_payments))
    n = len(result.unmatched_fencers)
    parts.append(f"### No payment found ({n})")
    parts.append(_no_payment_table(result.unmatched_fencers, fencer_disciplines or {}))
    return "\n\n".join(parts)
