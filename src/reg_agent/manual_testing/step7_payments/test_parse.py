"""Manual test for step7_payments.parse_transactions (Call 1 — Haiku).

Run from repo root:
    python src/reg_agent/manual_testing/step7_payments/test_parse.py

Saves parsed output to parsed_output.json in the same directory for use by test_match.py.

Checks that the LLM:
  - Filters out all outgoing / irrelevant payments
  - Retains every plausible incoming HEMA registration payment
  - Extracts sender names and amounts correctly for key entries

Line numbers in the input file (1 = header row):
  Outgoing (must be filtered): 3, 7, 10, 13, 15, 18, 20, 22
  Required incoming:           2, 4, 5, 8, 9, 11, 14, 16, 19
  Fencers with NO payment: Azja Tuhajbejowicz, Tuhaj-bej (lines removed from input)
"""

import json
import sys
from pathlib import Path

# ── Path setup ─────────────────────────────────────────────────────────────────
_FILE = Path(__file__).resolve()
_SRC = _FILE.parents[3]         # hema-agent/src/
_REG = _SRC / "reg_agent"
for _p in (_SRC, _REG):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from dotenv import load_dotenv
load_dotenv()

from config import RegConfig
from step7_payments import parse_transactions, ParsedTransaction

# ── Fixtures ───────────────────────────────────────────────────────────────────
INPUT_FILE = _FILE.parent / "input_payments.txt"
OUTPUT_FILE = _FILE.parent / "parsed_output.json"

# Lines with negative amounts → outgoing, must never appear in output.
OUTGOING_LINES = {3, 7, 10, 13, 15, 18, 20, 22}

# Core incoming registration-fee lines that must always be included.
REQUIRED_INCOMING_LINES = {
    2,   # Kmicic SA, 750 CZK
    4,   # Skrzetuski via "Jego Oświecenie Knize Wisniowiecki", 751,25 CZK (international)
    5,   # Wolodyjowski SA, 750 CZK
    8,   # Longinus Podbipięta / Three Heads Brotherhood, 750 CZK
    9,   # Bohun, 756,50 CZK (international from Ukraine)
    11,  # Helena Kurcewiczowna / Dzikie Pola, 750 CZK
    14,  # Islam Girej, 752,10 CZK (no sender name, name only in reference)
    16,  # Zagłoba SA, 750 CZK
    19,  # Charłamp paying for Kmicic, 751,25 CZK (third-party payer)
}


# ── Config ─────────────────────────────────────────────────────────────────────
def _config() -> RegConfig:
    return RegConfig(
        tournament_name="na_duel_2025",
        ai_models={
            "default": "anthropic:claude-sonnet-4-6",
            "payments_parse": "anthropic:claude-haiku-4-5-20251001",
        },
        creds_path="src/creds.json",
    )


# ── Test ───────────────────────────────────────────────────────────────────────
def test_parse_transactions() -> None:
    raw = INPUT_FILE.read_text()
    config = _config()

    txns: list[ParsedTransaction] = parse_transactions(raw, config)
    by_line: dict[int, ParsedTransaction] = {t.line_no: t for t in txns}
    line_nos = set(by_line)

    # Save immediately so test_match.py can use this output even if assertions below fail.
    OUTPUT_FILE.write_text(
        json.dumps([t.model_dump() for t in txns], ensure_ascii=False, indent=2)
    )
    print(f"\nSaved {len(txns)} transaction(s) → {OUTPUT_FILE.name}")

    print(f"\nParsed {len(txns)} transaction(s):")
    for t in sorted(txns, key=lambda t: t.line_no):
        print(f"  line {t.line_no:2d}: {t.sender_name!r:32s} | {t.amount:>10s} | {t.reference[:55]!r}")

    # ── Negative: outgoing payments must be filtered ───────────────────────────
    bad = OUTGOING_LINES & line_nos
    assert not bad, f"Outgoing lines should have been filtered out: {sorted(bad)}"
    print("\n✅ All outgoing lines filtered")

    # ── Positive: required incoming payments must be present ──────────────────
    missing = REQUIRED_INCOMING_LINES - line_nos
    assert not missing, f"Required incoming lines are missing: {sorted(missing)}"
    print("✅ All required incoming lines present")

    # ── Spot-check sender names / references ──────────────────────────────────
    t2 = by_line[2]
    assert "kmicic" in t2.sender_name.lower() or "kmicic" in t2.reference.lower(), (
        f"Line 2 should reference Kmicic; got sender={t2.sender_name!r} ref={t2.reference!r}"
    )

    t5 = by_line[5]
    assert "wolodyjowski" in t5.sender_name.lower() or "wolodyjowski" in t5.reference.lower(), (
        f"Line 5 should reference Wolodyjowski; got sender={t5.sender_name!r} ref={t5.reference!r}"
    )

    t9 = by_line[9]
    assert "bohun" in t9.sender_name.lower() or "bohun" in t9.reference.lower(), (
        f"Line 9 should reference Bohun; got sender={t9.sender_name!r} ref={t9.reference!r}"
    )

    # Line 14: empty sender — name lives only in the reference
    t14 = by_line[14]
    assert "islam" in t14.reference.lower() or "girej" in t14.reference.lower(), (
        f"Line 14 reference should contain Islam Girej; got ref={t14.reference!r}"
    )
    print("✅ Spot-check sender names / references pass")

    # ── Spot-check amounts ────────────────────────────────────────────────────
    # Line 4: international SEPA transfer with fractional amount
    t4 = by_line[4]
    assert "751" in t4.amount, (
        f"Line 4 amount should contain 751; got {t4.amount!r}"
    )

    # Line 9: international transfer from Ukraine with fractional amount
    t9 = by_line[9]
    assert "756" in t9.amount, (
        f"Line 9 amount should contain 756; got {t9.amount!r}"
    )
    print("✅ Spot-check amounts pass")

    print("\n✅✅✅ ALL ASSERTIONS PASSED ✅✅✅\n")


if __name__ == "__main__":
    test_parse_transactions()
