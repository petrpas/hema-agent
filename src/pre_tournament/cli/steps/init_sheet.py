"""Step 4.5 — initialize the Fencers worksheet (remote)."""

from pre_tournament.cli.errors import StepFailed
from pre_tournament.cli.steps._base import StepResult, artifacts, require_remote, timed
from step4_5_init import init_fencers_sheet
from utils import load_fencers_list, FENCERS_DEDUPED_FILE


def cmd_init_sheet(args, config) -> StepResult:
    data_dir = config.data_dir
    artifacts.require(artifacts.deduped(data_dir), "fencers_deduped.json", "run `dedup` first")
    fencers = load_fencers_list(data_dir, FENCERS_DEDUPED_FILE)

    require_remote(args, "init-sheet (Google Sheets write)")
    if not config.output_sheet_url:
        raise StepFailed(
            "no output_sheet_url — run `sheet-create`, share the copy, "
            "then `sheet-set-url URL`"
        )

    res = StepResult(step="init-sheet")
    with timed(res):
        init_fencers_sheet(fencers, config)
    res.summary = f"Fencers worksheet initialized with {len(fencers)} fencers"
    res.details = {"fencers": len(fencers), "sheet": config.output_sheet_url}
    return res
