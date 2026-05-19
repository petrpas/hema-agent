"""Step 4 — deduplicate, list likely groups, apply confirmed merges."""

import json
from pathlib import Path

from pre_tournament.cli.errors import ArtifactMissing
from pre_tournament.cli.steps._base import StepResult, artifacts, timed
from step4_dedup import (
    FENCERS_LIKELY_GROUPS_PENDING_FILE,
    apply_confirmed_merges,
    deduplicate_fencers,
    _dedup_likely_table_text,
)
from utils import load_fencers_list, FENCERS_MATCHED_FILE


def cmd_dedup(args, config) -> StepResult:
    data_dir = config.data_dir
    artifacts.require(artifacts.matched(data_dir), "fencers_matched.json", "run `match` first")
    fencers = load_fencers_list(data_dir, FENCERS_MATCHED_FILE)

    # --force: deleting the fingerprint defeats step4's unchanged-input cache.
    if args.force:
        artifacts.clear(artifacts.deduped_fp(data_dir))

    before = len(fencers)
    res = StepResult(step="dedup")
    with timed(res):
        fencers, report, likely_groups = deduplicate_fencers(fencers, config)

    # Persist likely groups so `dedup-confirm` can act on them later
    # (mirrors reg_agent.tool_find_likely_duplicates).
    if likely_groups:
        groups_data = {
            str(i + 1): [r.model_dump() for r in group]
            for i, group in enumerate(likely_groups)
        }
        artifacts.likely_pending(data_dir).write_text(
            json.dumps(groups_data, ensure_ascii=False, indent=2)
        )

    res.summary = f"{before} → {len(fencers)} fencers ({before - len(fencers)} merged)"
    res.details = {
        "before": before,
        "after": len(fencers),
        "merged_groups": len(report),
        "likely_pending": len(likely_groups),
    }
    if likely_groups:
        res.warnings.append(
            f"{len(likely_groups)} likely group(s) need confirmation — "
            f"see `dedup-likely`, then `dedup-confirm`"
        )
    res.artifact = artifacts.deduped(data_dir)
    return res


def cmd_dedup_likely(args, config) -> StepResult:
    data_dir = config.data_dir
    path = artifacts.likely_pending(data_dir)
    res = StepResult(step="dedup-likely")
    if not path.exists():
        res.summary = "no pending likely-duplicate groups"
        return res
    groups: dict[str, list[dict]] = json.loads(path.read_text())

    from models import FencerRecord

    res.summary = f"{len(groups)} pending likely group(s)"
    for num, records in groups.items():
        recs = [FencerRecord(**r) for r in records]
        res.details[f"group {num}"] = "\n" + _dedup_likely_table_text(recs)
    return res


def _load_approvals(args) -> dict[str, str | None]:
    if args.approvals:
        p = Path(args.approvals)
        if not p.exists():
            raise ArtifactMissing(f"approvals file not found: {p}")
        raw = json.loads(p.read_text())
        return {str(k): v for k, v in raw.items()}
    return {str(g): args.hint for g in args.group}


def cmd_dedup_confirm(args, config) -> StepResult:
    data_dir = config.data_dir
    approvals = _load_approvals(args)
    if not approvals:
        res = StepResult(step="dedup-confirm", ok=False,
                         summary="no groups given — use --group N or --approvals FILE")
        return res

    res = StepResult(step="dedup-confirm")
    with timed(res):
        out = apply_confirmed_merges(data_dir, config, approvals)

    if out["error"]:
        res.ok = False
        res.summary = out["error"]
        return res
    res.summary = f"applied {out['count']} merge(s): {', '.join(out['merged'])}"
    res.details = {"count": out["count"], "approved": list(approvals.keys())}
    res.artifact = artifacts.deduped(data_dir)
    return res
