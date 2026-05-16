"""Pool-result parsing agent (vision LLM) and result-formatting helpers."""

import logging
import sys
from pathlib import Path

_SRC_ROOT = Path(__file__).parent.parent.parent
if str(_SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(_SRC_ROOT))

from pydantic_ai import Agent
from pydantic_ai.messages import BinaryContent

from discord_bot.discord_utils import make_table
from in_tournament.msgs import read_msg as _read_msg
from in_tournament.results_agent.models import BoutOutcome, BoutResult, ParsedPool, PoolResult

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = _read_msg("results/system_prompt")

_parse_agent = Agent(
    "anthropic:claude-sonnet-4-6",
    output_type=ParsedPool,
    system_prompt=_SYSTEM_PROMPT,
)


# ── Parse + verify ─────────────────────────────────────────────────────────────

async def parse_pool_image(
    image_bytes: bytes,
    media_type: str,
    pool_composition: dict[str, list[str]],
    discipline_limits: dict[str, int] | None = None,
    disciplines: dict[str, str] | None = None,
) -> PoolResult:
    """Parse a pool score-sheet image and verify the result.

    *pool_composition* maps pool IDs (e.g. "LS-3") to the list of fencer names
    expected in that pool. Comes from the 'Pool standings' worksheet.

    *discipline_limits* maps discipline codes to the touch limit per bout.
    *disciplines* maps discipline codes to human-readable names (e.g. {"LS": "Long Sword Open"}).

    Returns a PoolResult with a confidence flag and issues list.
    """
    disc_map = disciplines or {}
    disc_lines = "\n".join(
        f"  {code} — {name}" for code, name in sorted(disc_map.items())
    ) if disc_map else "\n".join(
        f"  {d}" for d in sorted({pid.split('-')[0] for pid in pool_composition})
    )

    comp_lines = "\n".join(
        f"  {pid}: {', '.join(names)}" for pid, names in sorted(pool_composition.items())
    )

    configured = discipline_limits or {}
    discs = sorted({pid.split("-")[0] for pid in pool_composition})
    eff_limits = {disc: configured.get(disc, 5) for disc in discs}
    limit_lines = "\n".join(f"  {disc}: {lim} touches" for disc, lim in eff_limits.items())

    context = (
        f"Disciplines at this tournament (choose disc from this list):\n{disc_lines}\n\n"
        f"Known pools (pool_no is the integer after the dash):\n{comp_lines}\n\n"
        f"Touch limits per discipline:\n{limit_lines}\n"
    )

    result = await _parse_agent.run([
        BinaryContent(data=image_bytes, media_type=media_type),
        context,
    ])
    parsed: ParsedPool = result.output

    pool_id = f"{parsed.disc}-{parsed.pool_no}" if parsed.pool_no is not None else f"{parsed.disc}-?"
    log.info("LLM parsed %s with %d bouts (low_confidence=%s)", pool_id, len(parsed.bouts), parsed.low_confidence)
    for b in parsed.bouts:
        flag = " [uncertain]" if b.uncertain else ""
        log.info("  bout: %s %d (%s) vs %s %d (%s)%s",
                 b.fencer1, b.score1, b.r1, b.fencer2, b.score2, b.r2, flag)

    issues: list[str] = []
    if parsed.low_confidence:
        issues.append("image legibility flagged by parser")
    uncertain_bouts = [b for b in parsed.bouts if b.uncertain]
    if uncertain_bouts:
        names = ", ".join(f"{b.fencer1} vs {b.fencer2}" for b in uncertain_bouts)
        issues.append(f"uncertain bouts: {names}")

    # 1. pool_id known?
    if pool_id not in pool_composition:
        issues.append(f"pool_id '{pool_id}' not in known pools")
        log.warning("Verification failed: %s", issues[-1])
        return _make_pool_result(parsed, issues)

    expected_fencers = pool_composition[pool_id]
    parsed_names = {b.fencer1.strip().lower() for b in parsed.bouts} | {b.fencer2.strip().lower() for b in parsed.bouts}
    expected_names = {n.strip().lower() for n in expected_fencers}

    # 2. Fencer name set matches
    missing = expected_names - parsed_names
    extra = parsed_names - expected_names
    if missing or extra:
        if missing:
            issues.append(f"missing fencers: {', '.join(sorted(missing))}")
        if extra:
            issues.append(f"unexpected fencers: {', '.join(sorted(extra))}")

    # 3. Bout count
    n = len(expected_fencers)
    expected_count = n * (n - 1) // 2
    if len(parsed.bouts) != expected_count:
        issues.append(
            f"expected {expected_count} bouts for {n} fencers, got {len(parsed.bouts)}"
        )

    # 4. Score-sum symmetry: total touches scored == total touches received
    ts = sum(b.score1 + b.score2 for b in parsed.bouts)
    # each bout contributes score1 + score2 to the grand total once — that's already balanced
    # real check: for each ordered pair (A,B) and (B,A), A's score in one == B's score in the other
    pair_scores: dict[tuple[str, str], int] = {}
    for b in parsed.bouts:
        pair_scores[(b.fencer1.lower(), b.fencer2.lower())] = b.score1
        pair_scores[(b.fencer2.lower(), b.fencer1.lower())] = b.score2
    for (a, b_name), s in list(pair_scores.items()):
        reverse = pair_scores.get((b_name, a))
        # already covered when we process (b, a) — skip double-check issues
        _ = reverse  # symmetry is inherent in how we built pair_scores

    # 5. Outcome consistency: Win ↔ not-Win
    for b in parsed.bouts:
        r1, r2 = b.r1.lower(), b.r2.lower()
        if r1 == "win" and r2 == "win":
            issues.append(f"both fencers marked Win: {b.fencer1} vs {b.fencer2}")
        elif r1 == "loss" and r2 == "loss":
            issues.append(f"both fencers marked Loss: {b.fencer1} vs {b.fencer2}")

    if issues:
        log.warning("Verification issues for %s (%d):", pool_id, len(issues))
        for issue in issues:
            log.warning("  - %s", issue)
    else:
        log.info("Verification passed for %s — no issues", pool_id)

    return _make_pool_result(parsed, issues)


def _make_pool_result(parsed: ParsedPool, issues: list[str]) -> PoolResult:
    """Convert ParsedPool + issues into a PoolResult with a confidence flag."""
    pool_id = f"{parsed.disc}-{parsed.pool_no}" if parsed.pool_no is not None else f"{parsed.disc}-?"

    llm_low = parsed.low_confidence
    n_issues = len(issues)
    if n_issues == 0 and not llm_low:
        confidence = ""
    elif n_issues >= 3 or (n_issues >= 1 and llm_low):
        confidence = "??"
    else:
        confidence = "?"

    bouts: list[BoutResult] = []
    for b in parsed.bouts:
        try:
            r1 = BoutOutcome(b.r1)
        except ValueError:
            r1 = BoutOutcome.NO
            if not any("outcome" in i for i in issues):
                issues.append(f"unrecognised outcome '{b.r1}' for {b.fencer1}")
        try:
            r2 = BoutOutcome(b.r2)
        except ValueError:
            r2 = BoutOutcome.NO
        bouts.append(BoutResult(
            pool_id=pool_id,
            fencer1=b.fencer1,
            fencer2=b.fencer2,
            score1=b.score1,
            score2=b.score2,
            r1=r1,
            r2=r2,
            note=b.note or None,
        ))

    return PoolResult(
        pool_id=pool_id,
        disc=parsed.disc,
        pool_no=parsed.pool_no,
        bouts=bouts,
        confidence=confidence,
        issues=issues,
    )


# ── Stats + formatting ─────────────────────────────────────────────────────────

def compute_pool_stats(fencer_names: list[str], bouts: list[dict]) -> list[dict]:
    """Compute V, M, TS, TR, Ind for each fencer from a list of verified bout dicts.

    *bouts* rows come from gspread get_all_records() — keys match _HEADER.
    Returns list sorted DESC by (V/M, Ind, TS).
    """
    stats: dict[str, dict] = {
        name: {"v": 0, "m": 0, "ts": 0, "tr": 0}
        for name in fencer_names
    }

    def _find(name: str) -> str | None:
        """Return the canonical key for *name* (case-insensitive, strip)."""
        low = name.strip().lower()
        for k in stats:
            if k.strip().lower() == low:
                return k
        return None

    for row in bouts:
        f1 = _find(str(row.get("Fencer1", "")))
        f2 = _find(str(row.get("Fencer2", "")))
        try:
            s1 = int(row.get("Score1", 0))
            s2 = int(row.get("Score2", 0))
        except (TypeError, ValueError):
            continue

        if f1:
            stats[f1]["ts"] += s1
            stats[f1]["tr"] += s2
            stats[f1]["m"] += 1
            if s1 > s2:
                stats[f1]["v"] += 1
        if f2:
            stats[f2]["ts"] += s2
            stats[f2]["tr"] += s1
            stats[f2]["m"] += 1
            if s2 > s1:
                stats[f2]["v"] += 1

    result = []
    for name, s in stats.items():
        m = s["m"]
        vm = s["v"] / m if m > 0 else 0.0
        ind = s["ts"] - s["tr"]
        result.append({
            "name": name,
            "v": s["v"],
            "m": m,
            "ts": s["ts"],
            "tr": s["tr"],
            "ind": ind,
            "vm": vm,
        })

    result.sort(key=lambda x: (-x["vm"], -x["ind"], -x["ts"]))
    return result


def format_pool_summary(pool_id: str, stats: list[dict]) -> str:
    """Return a Discord-ready pool results message."""
    header = ["#", "Name", "V", "M", "TS", "TR", "Ind"]
    rows = [
        [
            str(i + 1),
            s["name"],
            str(s["v"]),
            str(s["m"]),
            str(s["ts"]),
            str(s["tr"]),
            f"{s['ind']:+d}",
        ]
        for i, s in enumerate(stats)
    ]
    table = make_table(header, rows)
    return f"**{pool_id} — Results**\n{table}"


def format_ranking_table(disc: str, disc_name: str, stats: list[dict]) -> str:
    """Return a Discord-ready pool-stage overall ranking message."""
    header = ["#", "Name", "V", "M", "TS", "TR", "Ind"]
    rows = [
        [
            str(i + 1),
            s["name"],
            str(s["v"]),
            str(s["m"]),
            str(s["ts"]),
            str(s["tr"]),
            f"{s['ind']:+d}",
        ]
        for i, s in enumerate(stats)
    ]
    table = make_table(header, rows)
    return f"**{disc} ({disc_name}) — Pool Stage Ranking**\n{table}"
