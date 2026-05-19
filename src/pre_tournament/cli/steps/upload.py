"""Step 6 — upload to output sheet, recalc seeds, withdraw fencers (remote)."""

from pre_tournament.cli.errors import StepFailed
from pre_tournament.cli.steps._base import StepResult, artifacts, require_remote, timed
from step6_upload import recalculate_seeds, remove_fencers_from_sheets, upload_results
from utils import (
    load_fencers_list,
    load_ratings,
    load_withdrawn,
    save_withdrawn,
    fuzzy_match_fencers,
    normalize_name,
    WithdrawnEntry,
    FENCERS_DEDUPED_FILE,
)


def cmd_upload(args, config) -> StepResult:
    data_dir = config.data_dir
    artifacts.require(artifacts.deduped(data_dir), "fencers_deduped.json", "run `dedup` first")
    fencers = load_fencers_list(data_dir, FENCERS_DEDUPED_FILE)
    rpath = artifacts.require(artifacts.latest_ratings(data_dir), "ratings_*.json",
                              "run `ratings` first")
    require_remote(args, "upload (Google Sheets write)")
    ratings = load_ratings(data_dir, rpath.name)
    if ratings is None:
        raise StepFailed("could not load ratings file")
    if not config.output_sheet_url:
        raise StepFailed("no output_sheet_url — run `sheet-set-url URL` first")
    res = StepResult(step="upload")
    with timed(res):
        upload_results(fencers, ratings, config)
    res.summary = "upload complete"
    res.details = {"fencers": len(fencers), "sheet": config.output_sheet_url}
    return res


def cmd_seeds_recalc(args, config) -> StepResult:
    if not config.output_sheet_url:
        raise StepFailed("no output_sheet_url set")
    require_remote(args, "seeds-recalc (Google Sheets write)")

    import gspread

    res = StepResult(step="seeds-recalc")
    with timed(res):
        gc = gspread.service_account(filename=config.creds_path)
        sh = gc.open_by_url(config.output_sheet_url)
        done = []
        for code in config.disciplines:
            try:
                recalculate_seeds(sh.worksheet(code))
                done.append(f"{code}✓")
            except Exception as e:  # noqa: BLE001
                done.append(f"{code}✗{e}")
                res.ok = False
    res.summary = "seeds recalculated: " + ", ".join(done)
    return res


def cmd_remove_fencers(args, config) -> StepResult:
    data_dir = config.data_dir
    artifacts.require(artifacts.deduped(data_dir), "fencers_deduped.json",
                      "run pipeline through `dedup` first")
    fencers = load_fencers_list(data_dir, FENCERS_DEDUPED_FILE)
    names = args.name

    if not args.confirm:
        res = StepResult(step="remove-fencers")
        for query in names:
            matches = fuzzy_match_fencers(query, fencers)
            res.details[query] = (
                ", ".join(f"{f.name} (HR_ID={f.hr_id})" for f in matches[:3])
                if matches else "no match"
            )
        res.summary = "preview — re-run with exact names and --confirm"
        return res

    require_remote(args, "remove-fencers (Google Sheets write)")
    withdrawn = load_withdrawn(data_dir)
    existing = {w.name.lower() for w in withdrawn}
    newly: list[WithdrawnEntry] = []
    not_in_data: list[str] = []
    for name in names:
        m = next((f for f in fencers if normalize_name(f.name) == normalize_name(name)), None)
        if m is None:
            not_in_data.append(name)
        elif m.name.lower() not in existing:
            newly.append(WithdrawnEntry(name=m.name, hr_id=m.hr_id))
    save_withdrawn(withdrawn + newly, data_dir)

    res = StepResult(step="remove-fencers")
    with timed(res):
        sheet_result = {"removed": [], "not_found": names}
        if config.output_sheet_url:
            sheet_result = remove_fencers_from_sheets(names, config)
    res.summary = (
        f"withdrawn list +{len(newly)}; "
        f"removed from sheets: {', '.join(sheet_result['removed']) or '—'}"
    )
    res.details = {
        "withdrawn_total": len(withdrawn) + len(newly),
        "not_found_in_sheets": ", ".join(sheet_result["not_found"]) or "—",
        "not_in_data": ", ".join(not_in_data) or "—",
    }
    return res
