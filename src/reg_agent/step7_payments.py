"""Step 7: Payment matching — parse bank export, match to registered fencers, report."""

import json
import logging
from typing import Literal

from jinja2 import Template
from pydantic import BaseModel
from pydantic_ai import Agent, ModelSettings

from config import RegConfig, Step

logger = logging.getLogger(__name__)

PAYMENTS_RAW_FILE = "payments_raw.txt"
PAYMENTS_PARSED_FILE = "payments_parsed.json"
PAYMENTS_MATCHED_FILE = "payments_matched.json"


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


# ── Prompts ────────────────────────────────────────────────────────────────────

_PARSE_SYSTEM = Template("""\
You are a payment parser for a HEMA tournament.
You receive a raw bank statement export and must extract only the incoming payments
that look like tournament registration fees.
Return a ParsedTransactionList.

For each plausible incoming payment include:
  - line_no: the 1-based line number of the original entry in the input
  - date: transaction date as found (keep original format)
  - sender_name: name of the sender
  - reference: payment reference / message as found
  - amount: amount including currency symbol, e.g. "€150.00"
  - notes: any other relevant detail (bank name, account, etc.)

Filter OUT:
  - Outgoing payments (debits / charges / fees paid by the account holder)
  - Card / POS transactions
  - Entries with clearly irrelevant references (utilities, rent, salaries, etc.)
  - Header / footer / summary lines that are not individual transactions

When in doubt, include the entry — false positives are cheaper than false negatives.
""")

_MATCH_SYSTEM = Template("""\
You are a payment matcher for a HEMA tournament.
Match the provided parsed bank transactions to registered fencers.

Fencer list (name | disciplines | afterparty | borrow weapons):
{{ fencer_summaries }}

{% if hints %}
Organiser hints:
{{ hints }}

{% endif %}
Rules:
- One payment can cover multiple fencers (family member, club group paying together).
- Use sender_name AND reference for fuzzy name matching — typos and transliterations are common.
- A match is "hi" confidence if the name is unambiguous and amount is plausible.
- A match is "low" confidence if there is uncertainty about the name or amount.
- List every fencer for whom no plausible payment was found in unmatched_fencers.
- List every transaction that could not be matched to any fencer in unmatched_payments.
- Your remark should briefly explain the reasoning for each match or non-match.
""")


# ── LLM calls ─────────────────────────────────────────────────────────────────

def parse_transactions(raw_content: str, config: RegConfig) -> list[ParsedTransaction]:
    """Call 1 (Haiku): Parse raw bank export → list[ParsedTransaction]."""
    agent = Agent(
        model=config.model(Step.PAYMENTS_PARSE),
        model_settings=ModelSettings(temperature=0.0),
        output_type=ParsedTransactionList,
        system_prompt=_PARSE_SYSTEM.render(),
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
        "{name} | {disciplines} | afterparty={afterparty} | borrow={borrow}".format(**s)
        for s in fencer_summaries
    )
    agent = Agent(
        model=config.model(Step.PAYMENTS_MATCH),
        model_settings=ModelSettings(temperature=0.0),
        output_type=PaymentsResult,
        system_prompt=_MATCH_SYSTEM.render(fencer_summaries=summary_lines, hints=hints),
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

def format_payments_report(result: PaymentsResult) -> str:
    """Render a PaymentsResult as a human-readable Discord message."""
    lines: list[str] = []

    def _match_line(m: PaymentMatch) -> str:
        names = ", ".join(m.fencer_names)
        return f"  • line {m.payment_line_no} → **{names}** ({m.amount}) — {m.remark}"

    lines.append(f"✅ **Matched ({len(result.matched)})**")
    lines.extend(_match_line(m) for m in result.matched)

    lines.append("")
    lines.append(f"⚠️ **Possible — low confidence ({len(result.possible)})**")
    lines.extend(_match_line(m) for m in result.possible)

    lines.append("")
    lines.append(f"❌ **Unmatched payments ({len(result.unmatched_payments)})**")
    for t in result.unmatched_payments:
        lines.append(f"  • line {t.line_no}: {t.sender_name} — {t.amount} ({t.reference})")

    lines.append("")
    lines.append(f"👤 **No payment found ({len(result.unmatched_fencers)})**")
    for name in result.unmatched_fencers:
        lines.append(f"  • {name}")

    return "\n".join(lines)
