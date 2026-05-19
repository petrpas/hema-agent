"""Output-sheet lifecycle: create a blank sheet, set + persist its URL (remote)."""

from pre_tournament.cli.errors import StepFailed
from pre_tournament.cli.steps._base import StepResult, require_remote, timed
from step6_upload import setup_output_sheet


def cmd_sheet_create(args, config) -> StepResult:
    require_remote(args, "sheet-create (Google Drive write)")
    res = StepResult(step="sheet-create")
    with timed(res):
        url = setup_output_sheet(config)
    res.summary = f"created output sheet: {url}"
    res.details = {
        "url": url,
        "next": "share your copy with the bot, then `sheet-set-url URL`",
    }
    return res


def cmd_sheet_set_url(args, config) -> StepResult:
    require_remote(args, "sheet-set-url (Google Sheets read to verify)")

    import gspread

    from pre_tournament.cli.context import _resolve_user_config_path
    from pre_tournament.config import PreUserConfig, save_pre_config

    try:
        gc = gspread.service_account(filename=config.creds_path)
        gc.open_by_url(args.url)
    except Exception as e:  # noqa: BLE001
        raise StepFailed(f"cannot access the sheet: {e}") from e

    config.output_sheet_url = args.url
    ucp = _resolve_user_config_path(args.config)
    save_pre_config(
        PreUserConfig(
            tournament_name=config.tournament_name,
            language=config.language,
            registration_sheet_url=config.registration_sheet_url,
            output_sheet_url=args.url,
            disciplines=config.disciplines,
            discipline_limits=config.discipline_limits,
        ),
        ucp,
    )
    res = StepResult(step="sheet-set-url")
    res.summary = f"output sheet URL set + persisted → {ucp}"
    res.details = {"url": args.url, "config": str(ucp)}
    return res
