"""Manual test for step7_payments.match_payments (Call 2 — Sonnet).

Run from repo root AFTER test_parse.py has produced parsed_output.json:
    python src/reg_agent/manual_testing/step7_payments/test_parse.py
    python src/reg_agent/manual_testing/step7_payments/test_match.py

Loads pre-parsed transactions from parsed_output.json so this test is independent
from the parse LLM call — only the match LLM call is exercised here.

Checks that the LLM:
  - Marks clearly identified fencers as hi-confidence matches
  - Handles tricky cases: transliterated names, empty sender, third-party payer
  - Reports Tuhaj-bej and Azja Tuhajbejowicz in unmatched_fencers (no payment in input)
  - Does NOT report anyone else as unmatched
"""

import json
import sys
from pathlib import Path

# ── Path setup ─────────────────────────────────────────────────────────────────
_FILE = Path(__file__).resolve()
_SRC = _FILE.parents[3]
_REG = _SRC / "reg_agent"
for _p in (_SRC, _REG):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from dotenv import load_dotenv
load_dotenv()

from config import RegConfig
from step7_payments import (
    match_payments,
    format_payments_report,
    ParsedTransaction,
    PaymentMatch,
    PaymentsResult,
)

# ── Fixtures ───────────────────────────────────────────────────────────────────
PARSED_OUTPUT = _FILE.parent / "parsed_output.json"
INPUT_FENCERS = _FILE.parent / "input_fencers.txt"

# These two fencers have no payment in the input file.
EXPECTED_UNMATCHED = {"tuhaj-bej", "azja tuhajbejowicz"}

# These fencers have unambiguous payments and should be hi-confidence.
EXPECTED_HI = [
    "wolodyjowski",      # line 5: sender is exactly "Wolodyjowski Michal"
    "bohun",             # line 9: sender is "Bohun"
    "longinus",          # line 8: reference names him explicitly
    "helena",            # line 11: reference names her + HR ID
    "zagłoba",           # line 17: sender is "Zagłoba"
]

# These fencers have payments but via indirect evidence — at minimum in matched or possible.
EXPECTED_PRESENT = [
    "kmicic",        # line 2 (own name) + line 20 (paid by Charłamp)
    "skrzetuski",    # line 4 (paid by Knize Wisniowiecki) + line 16 (SA+RA, no sender)
    "islam",         # line 14: no sender, name only in reference
]


# ── Config ─────────────────────────────────────────────────────────────────────
def _config() -> RegConfig:
    return RegConfig(
        tournament_name="na_duel_2025",
        ai_models={
            "default": "anthropic:claude-sonnet-4-6",
            "payments_match": "anthropic:claude-sonnet-4-6",
        },
        creds_path="src/creds.json",
    )


# ── Loader helpers ─────────────────────────────────────────────────────────────
def _load_transactions() -> list[ParsedTransaction]:
    if not PARSED_OUTPUT.exists():
        raise FileNotFoundError(
            f"{PARSED_OUTPUT.name} not found — run test_parse.py first."
        )
    data = json.loads(PARSED_OUTPUT.read_text())
    return [ParsedTransaction(**row) for row in data]


def _load_fencer_summaries() -> list[dict]:
    lines = INPUT_FENCERS.read_text().splitlines()
    # Header: Reg.;Name;Nat.;Club;HR_ID;Disciplines;Paid;Afterparty;Borrow weapons;Notes
    summaries = []
    for line in lines[1:]:
        parts = line.split(";")
        summaries.append({
            "name": parts[1].strip(),
            "disciplines": parts[5].strip().replace('"', ''),
            "afterparty": parts[7].strip() or "No",
            "borrow": parts[8].strip() or "none",
        })
    return summaries


# ── Assert helpers ─────────────────────────────────────────────────────────────
_POLISH = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")

def _norm(s: str) -> str:
    """Lowercase + strip Polish diacritics so fragment matching is accent-insensitive."""
    return s.lower().translate(_POLISH)


def _in_matched(result: PaymentsResult, fragment: str) -> bool:
    """True if fragment appears in any hi-confidence match fencer name."""
    frag = _norm(fragment)
    return any(
        any(frag in _norm(n) for n in m.fencer_names)
        for m in result.matched
    )


def _in_results(result: PaymentsResult, fragment: str) -> bool:
    """True if fragment appears in any matched or possible fencer name."""
    frag = _norm(fragment)
    return any(
        any(frag in _norm(n) for n in m.fencer_names)
        for m in result.matched + result.possible
    )


# ── Test ───────────────────────────────────────────────────────────────────────
def test_match_payments() -> None:
    transactions = _load_transactions()
    fencer_summaries = _load_fencer_summaries()
    config = _config()

    print(f"\nMatching {len(transactions)} transaction(s) against {len(fencer_summaries)} fencer(s) …\n")
    result: PaymentsResult = match_payments(
        transactions, fencer_summaries, hints=None, config=config
    )

    print(format_payments_report(result))

    # ── 1. Unmatched fencers: exactly Tuhaj-bej and Azja ─────────────────────
    unmatched_lower = {_norm(n) for n in result.unmatched_fencers}

    missing_from_unmatched = EXPECTED_UNMATCHED - unmatched_lower
    assert not missing_from_unmatched, (
        f"These fencers have no payment but were not reported as unmatched: "
        f"{missing_from_unmatched}\n  got: {result.unmatched_fencers}"
    )
    print(f"\n✅ Tuhaj-bej and Azja Tuhajbejowicz correctly in unmatched_fencers")

    # No other fencer should be unmatched (all others have payments)
    unexpected_unmatched = unmatched_lower - EXPECTED_UNMATCHED
    assert not unexpected_unmatched, (
        f"Fencers with payments were incorrectly reported as unmatched: "
        f"{unexpected_unmatched}\n  got: {result.unmatched_fencers}"
    )
    print("✅ No other fencer is in unmatched_fencers")

    # ── 2. Hi-confidence matches for unambiguous cases ────────────────────────
    for fragment in EXPECTED_HI:
        assert _in_matched(result, fragment), (
            f"Expected '{fragment}' to be a hi-confidence match, "
            f"but it is absent from matched.\n"
            f"  matched names: {[m.fencer_names for m in result.matched]}\n"
            f"  possible names: {[m.fencer_names for m in result.possible]}"
        )
    print(f"✅ All {len(EXPECTED_HI)} unambiguous fencers are hi-confidence matches")

    # ── 3. Indirect / tricky cases: present anywhere in results ──────────────
    for fragment in EXPECTED_PRESENT:
        assert _in_results(result, fragment), (
            f"Expected '{fragment}' to appear in matched or possible, "
            f"but it is absent.\n"
            f"  matched: {[m.fencer_names for m in result.matched]}\n"
            f"  possible: {[m.fencer_names for m in result.possible]}"
        )
    print(f"✅ All {len(EXPECTED_PRESENT)} tricky-case fencers present in matched or possible")

    print("\n✅✅✅ ALL ASSERTIONS PASSED ✅✅✅\n")


if __name__ == "__main__":
    test_match_payments()
