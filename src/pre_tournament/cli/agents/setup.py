"""setup_agent core — inspect and mutate the tournament user config."""

import json

from pre_tournament.cli.context import _resolve_user_config_path
from pre_tournament.cli.errors import ArtifactMissing
from pre_tournament.cli.steps._base import StepResult

# Keys a CLI user may set; values are JSON-parsed when possible (so
# disciplines / discipline_limits can be passed as JSON objects).
_SETTABLE = {
    "tournament_name",
    "language",
    "registration_sheet_url",
    "output_sheet_url",
    "disciplines",
    "discipline_limits",
    "tournament_display_name",
}


def cmd_setup_show(args, config) -> StepResult:
    res = StepResult(step="setup-show")
    res.summary = f"config for '{config.tournament_name}'"
    res.details = {
        "tournament_name": config.tournament_name,
        "language": config.language,
        "disciplines": config.disciplines,
        "discipline_limits": config.discipline_limits,
        "registration_sheet_url": config.registration_sheet_url or "—",
        "output_sheet_url": config.output_sheet_url or "—",
        "data_dir": str(config.data_dir),
        "creds_path": config.creds_path,
        "model.parse": config.ai_models.get("parse", config.ai_models.get("default")),
        "model.match": config.ai_models.get("match", config.ai_models.get("default")),
    }
    return res


def cmd_setup_set(args, config) -> StepResult:
    if args.key not in _SETTABLE:
        return StepResult(
            step="setup-set", ok=False,
            summary=f"key '{args.key}' not settable; allowed: {sorted(_SETTABLE)}",
        )
    ucp = _resolve_user_config_path(args.config)
    if not ucp.exists():
        raise ArtifactMissing(f"user config not found: {ucp}")
    data = json.loads(ucp.read_text())

    try:
        value = json.loads(args.value)
    except json.JSONDecodeError:
        value = args.value

    old = data.get(args.key, "—")
    data[args.key] = value
    ucp.write_text(json.dumps(data, ensure_ascii=False, indent=2))

    res = StepResult(step="setup-set")
    res.summary = f"{args.key}: {old!r} → {value!r}"
    res.details = {"config": str(ucp)}
    return res
