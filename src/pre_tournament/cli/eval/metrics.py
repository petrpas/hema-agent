"""Pure per-step comparison metrics over two artifact JSON files.

Each `*_metrics(golden_path, actual_path) -> dict[str, float]`. No LLM /
Google needed — eval runs fully offline against cached artifacts.

DEFAULT_THRESHOLDS[step][metric] = minimum acceptable value (used by
`eval-run --assert`).
"""

import json
import unicodedata
from pathlib import Path

DEFAULT_THRESHOLDS: dict[str, dict[str, float]] = {
    "parse": {"count_ratio": 1.0, "field_match_rate": 0.95},
    "match": {"identity_jaccard": 0.9, "hr_id_agreement": 0.9},
    "dedup": {"count_ratio": 1.0, "identity_jaccard": 0.95},
    "ratings": {"coverage": 0.9, "value_agreement": 0.95},
    "pay-match": {"matched_ratio": 0.9, "line_set_agreement": 0.85},
    "pool-solve": {"score_ratio": 0.0},  # informational unless overridden
}


def _norm(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", (s or "").lower())
        if unicodedata.category(c) != "Mn"
    )


def _load(path: Path):
    return json.loads(Path(path).read_text())


def _identity(f: dict) -> str:
    return f"hr:{f['hr_id']}" if f.get("hr_id") is not None else f"nm:{_norm(f.get('name', ''))}"


def _weapons(f: dict) -> set[str]:
    return {d.get("weapon") for d in f.get("disciplines", [])}


def fencer_metrics(golden_path: Path, actual_path: Path) -> dict[str, float]:
    g = _load(golden_path)
    a = _load(actual_path)
    gi = {_identity(f): f for f in g}
    ai = {_identity(f): f for f in a}
    common = set(gi) & set(ai)
    union = set(gi) | set(ai)

    fields = ("name", "club", "nationality", "hr_id")
    field_hits = field_total = 0
    hr_hits = 0
    for k in common:
        for fld in fields:
            field_total += 1
            if (gi[k].get(fld) or None) == (ai[k].get(fld) or None):
                field_hits += 1
        if gi[k].get("hr_id") == ai[k].get("hr_id"):
            hr_hits += 1
        if _weapons(gi[k]) == _weapons(ai[k]):
            field_hits += 1
        field_total += 1

    return {
        "golden_count": float(len(g)),
        "actual_count": float(len(a)),
        "count_ratio": (min(len(g), len(a)) / max(len(g), len(a))) if g or a else 1.0,
        "identity_jaccard": (len(common) / len(union)) if union else 1.0,
        "field_match_rate": (field_hits / field_total) if field_total else 1.0,
        "hr_id_agreement": (hr_hits / len(common)) if common else 1.0,
    }


def ratings_metrics(golden_path: Path, actual_path: Path) -> dict[str, float]:
    g = _load(golden_path)
    a = _load(actual_path)
    g_cells, a_cells = {}, {}
    for hr_id, weap in g.items():
        for w, r in weap.items():
            g_cells[(hr_id, w)] = (r.get("rating"), r.get("rank"))
    for hr_id, weap in a.items():
        for w, r in weap.items():
            a_cells[(hr_id, w)] = (r.get("rating"), r.get("rank"))
    common = set(g_cells) & set(a_cells)
    agree = sum(1 for k in common if g_cells[k] == a_cells[k])
    return {
        "golden_fighters": float(len(g)),
        "actual_fighters": float(len(a)),
        "coverage": (len(a) / len(g)) if g else 1.0,
        "value_agreement": (agree / len(common)) if common else 1.0,
    }


def payments_metrics(golden_path: Path, actual_path: Path) -> dict[str, float]:
    g = _load(golden_path)
    a = _load(actual_path)

    def line_map(d):
        m = {}
        for grp in ("matched", "possible"):
            for it in d.get(grp, []):
                m[it["payment_line_no"]] = frozenset(it.get("fencer_names", []))
        return m

    gm, am = line_map(g), line_map(a)
    common = set(gm) & set(am)
    agree = sum(1 for k in common if gm[k] == am[k])
    gn = len(g.get("matched", []))
    an = len(a.get("matched", []))
    return {
        "golden_matched": float(gn),
        "actual_matched": float(an),
        "matched_ratio": (min(gn, an) / max(gn, an)) if (gn or an) else 1.0,
        "line_set_agreement": (agree / len(common)) if common else 1.0,
    }


def pool_metrics(golden_path: Path, actual_path: Path) -> dict[str, float]:
    g = _load(golden_path).get("last_score") or {}
    a = _load(actual_path).get("last_score") or {}

    def total(s):
        return sum(float(s.get(k, 0.0)) for k in ("snake_deviation", "club", "nationality", "wave"))

    gt, at = total(g), total(a)
    return {
        "golden_score": gt,
        "actual_score": at,
        # 1.0 when actual ≤ golden (as good or better); shrinks as it worsens
        "score_ratio": (gt / at) if at else 1.0,
    }


METRIC_FN = {
    "parse": fencer_metrics,
    "match": fencer_metrics,
    "dedup": fencer_metrics,
    "ratings": ratings_metrics,
    "pay-match": payments_metrics,
    "pool-solve": pool_metrics,
}


def compute(step: str, golden_path: Path, actual_path: Path) -> dict[str, float]:
    return METRIC_FN[step](golden_path, actual_path)


def breaches(step: str, metrics: dict[str, float], overrides: dict[str, float]) -> list[str]:
    thresholds = {**DEFAULT_THRESHOLDS.get(step, {}), **overrides}
    out = []
    for name, minimum in thresholds.items():
        if name in metrics and metrics[name] < minimum:
            out.append(f"{name}={metrics[name]:.3f} < {minimum}")
    return out
