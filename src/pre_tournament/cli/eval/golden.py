"""Freeze / list golden artifacts under data/{tournament}/eval/golden/."""

import json
import shutil
import subprocess
from datetime import datetime, UTC

from pre_tournament.cli.eval._common import artifact_path, eval_root, golden_dir
from pre_tournament.cli.errors import ArtifactMissing
from pre_tournament.cli.steps._base import StepResult


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], text=True
        ).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def cmd_eval_golden_save(args, config) -> StepResult:
    src = artifact_path(args.step, config)
    if not src.exists():
        raise ArtifactMissing(f"{args.step} artifact not found: {src} — run it first")

    gdir = golden_dir(config, args.step, args.tag)
    gdir.mkdir(parents=True, exist_ok=True)
    dst = gdir / src.name
    shutil.copy2(src, dst)

    meta = {
        "step": args.step,
        "tag": args.tag,
        "artifact": src.name,
        "saved_at": datetime.now(UTC).isoformat(),
        "git_sha": _git_sha(),
        "model_default": config.ai_models.get("default"),
        "tournament": config.tournament_name,
    }
    (gdir / "metadata.json").write_text(json.dumps(meta, indent=2))

    res = StepResult(step="eval-golden-save")
    res.summary = f"saved golden {args.step}/{args.tag}"
    res.details = {"path": str(dst), "git_sha": meta["git_sha"]}
    res.artifact = dst
    return res


def cmd_eval_golden_list(args, config) -> StepResult:
    root = eval_root(config) / "golden"
    res = StepResult(step="eval-golden-list")
    if not root.exists():
        res.summary = "no goldens saved yet"
        return res
    found = 0
    for step_dir in sorted(root.iterdir()):
        if not step_dir.is_dir():
            continue
        for tag_dir in sorted(step_dir.iterdir()):
            mp = tag_dir / "metadata.json"
            info = json.loads(mp.read_text()) if mp.exists() else {}
            res.details[f"{step_dir.name}/{tag_dir.name}"] = (
                f"{info.get('artifact', '?')} @ {info.get('git_sha', '?')} "
                f"({info.get('saved_at', '?')})"
            )
            found += 1
    res.summary = f"{found} golden(s)"
    return res
