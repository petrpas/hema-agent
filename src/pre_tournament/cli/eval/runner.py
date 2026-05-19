"""eval-run (run a step, compare vs golden) and eval-diff (compare two runs)."""

import argparse
import importlib
import json
import shutil
import statistics
from datetime import datetime

from pre_tournament.cli.errors import ArtifactMissing, EvalFailed
from pre_tournament.cli.eval import metrics as M
from pre_tournament.cli.eval._common import (
    STEP_HANDLER,
    artifact_path,
    golden_dir,
    runs_dir,
)
from pre_tournament.cli.steps._base import StepResult


def _sub_args(args, force: bool) -> argparse.Namespace:
    ns = argparse.Namespace(
        config=args.config, tournament=args.tournament, data_root=args.data_root,
        format="json", force=force, allow_remote=getattr(args, "allow_remote", False),
        verbose=args.verbose,
        csv=None, sheet_url=None, worksheet=None, worksheet_index=0,
        instructions=None, force_html=False,
        group=[], hint=None, approvals=None, name=[], confirm=False,
        discipline=None, from_state=True, num_pools=None, waves=None,
        parallel_waves=None,
    )
    return ns


def _parse_overrides(items: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for it in items:
        if "=" in it:
            k, v = it.split("=", 1)
            out[k.strip()] = float(v)
    return out


def cmd_eval_run(args, config) -> StepResult:
    step = args.step
    if step not in STEP_HANDLER:
        raise ArtifactMissing(
            f"step '{step}' not evaluable — known: {sorted(STEP_HANDLER)}"
        )
    mod_name, func_name = STEP_HANDLER[step].split(":")
    handler = getattr(importlib.import_module(mod_name), func_name)
    art = artifact_path(step, config)

    if step == "pool-solve":
        from pre_tournament.pool_alch_agent.state import load_state

        st = load_state(config)
        if st is None:
            raise ArtifactMissing("pool-solve eval needs a saved pool_alch_state.json")

    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = runs_dir(config, step) / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    original = art.read_bytes() if art.exists() else None
    per_run_metrics: list[dict[str, float]] = []
    gdir = golden_dir(config, step, args.golden)
    golden_file = next((p for p in gdir.glob("*") if p.name != "metadata.json"), None) \
        if gdir.exists() else None

    res = StepResult(step="eval-run")
    for i in range(max(1, args.repeat)):
        sub = _sub_args(args, force=args.force or i > 0)
        if step == "pool-solve":
            from pre_tournament.pool_alch_agent.state import load_state

            sub.discipline = load_state(config).discipline
        r = handler(sub, config)
        produced = artifact_path(step, config)
        if produced.exists():
            shutil.copy2(produced, out_dir / f"run{i + 1}{produced.suffix}")
        if golden_file and produced.exists():
            per_run_metrics.append(M.compute(step, golden_file, produced))
        if not r.ok:
            res.warnings.append(f"run {i + 1}: {r.summary}")

    # Restore the working artifact so eval is non-destructive
    if original is not None:
        art.write_bytes(original)

    agg: dict[str, float] = {}
    if per_run_metrics:
        keys = per_run_metrics[0].keys()
        for k in keys:
            vals = [m[k] for m in per_run_metrics]
            agg[k] = round(statistics.fmean(vals), 4)
            if len(vals) > 1:
                agg[f"{k}__stdev"] = round(statistics.pstdev(vals), 4)

    report = {
        "step": step, "repeat": args.repeat, "golden": args.golden,
        "golden_found": bool(golden_file), "metrics": agg,
    }
    (out_dir / "metrics.json").write_text(json.dumps(report, indent=2))

    res.summary = (
        f"{step} ×{args.repeat} → {out_dir.name}"
        + ("" if golden_file else " (no golden — metrics skipped)")
    )
    res.details = {**agg, "run_dir": str(out_dir)}
    res.artifact = out_dir / "metrics.json"

    if args.do_assert and golden_file:
        bad = M.breaches(step, agg, _parse_overrides(args.threshold))
        if bad:
            res.ok = False
            res.summary = f"{step}: assertion failed — " + "; ".join(bad)
            raise EvalFailed(res.summary)
    return res


def cmd_eval_diff(args, config) -> StepResult:
    step = args.step
    base = runs_dir(config, step)

    def _artifact(run_id: str):
        d = base / run_id
        if not d.exists():
            raise ArtifactMissing(f"no eval run '{run_id}' for {step}")
        cands = [p for p in d.glob("run1*") if p.suffix]
        if not cands:
            raise ArtifactMissing(f"run '{run_id}' has no captured artifact")
        return cands[0]

    a, b = _artifact(args.a), _artifact(args.b)
    m = M.compute(step, a, b)
    res = StepResult(step="eval-diff")
    res.summary = f"{step}: {args.a} vs {args.b}"
    res.details = {k: round(v, 4) for k, v in m.items()}
    return res
