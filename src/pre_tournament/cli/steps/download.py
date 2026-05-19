"""Step 1 — download the registration sheet (remote) or ingest a local CSV."""

from pathlib import Path

from pre_tournament.cli.errors import StepFailed
from pre_tournament.cli.steps._base import StepResult, artifacts, require_remote, timed
from step1_download import download_registrations, save_registration_csv


def cmd_download(args, config) -> StepResult:
    res = StepResult(step="download")

    if args.csv:
        src = artifacts.require(Path(args.csv), "local CSV", args.csv)
        with timed(res):
            path = save_registration_csv(config, src.read_bytes())
        res.summary = f"ingested local CSV → {path.name}"
        res.details = {"file": path.name}
        res.artifact = path
        return res

    require_remote(args, "download (Google Sheets read)")
    sheet_url = args.sheet_url or config.registration_sheet_url
    if not sheet_url:
        raise StepFailed("no sheet URL — pass --sheet-url or set registration_sheet_url")

    with timed(res):
        path = download_registrations(
            config, sheet_url, args.worksheet_index, args.worksheet
        )
    rows = max(0, len(path.read_text().splitlines()) - 1)
    res.summary = f"downloaded {rows} rows → {path.name}"
    res.details = {"file": path.name, "rows": rows}
    res.artifact = path
    return res
