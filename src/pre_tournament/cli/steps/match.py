"""Step 3 — fuzzy-match fencers to HEMA Ratings, plus correct / search."""

from pre_tournament.cli.steps._base import StepResult, artifacts, timed
from step3_match import (
    apply_correction,
    match_fencers,
    search_profiles,
    _categorize_fencer,
    _normalize,
)
from utils import load_fencers_list, FENCERS_PARSED_FILE


def cmd_match(args, config) -> StepResult:
    data_dir = config.data_dir
    artifacts.require(artifacts.parsed(data_dir), "fencers_parsed.json", "run `parse` first")
    fencers = load_fencers_list(data_dir, FENCERS_PARSED_FILE)
    parsed_fencers = fencers

    if args.force:
        artifacts.clear(artifacts.matched(data_dir))

    before_unmatched = sum(1 for f in fencers if f.hr_id is None)

    res = StepResult(step="match")
    with timed(res):
        fencers = match_fencers(fencers, config, args.instructions)

    after_unmatched = sum(1 for f in fencers if f.hr_id is None)
    unmatched_names = [f.name for f in fencers if f.hr_id is None]

    parsed_by_name = {_normalize(f.name): f for f in parsed_fencers}
    cats = {"confirmed": 0, "found": 0, "unmatched": 0, "rejected": 0}
    for mf in fencers:
        pf = parsed_by_name.get(_normalize(mf.reg_name or mf.name), mf)
        cats[_categorize_fencer(pf, mf)] += 1

    res.summary = (
        f"matched {before_unmatched - after_unmatched} new, "
        f"{after_unmatched} still unmatched"
    )
    res.details = {
        **cats,
        "total": len(fencers),
        "unmatched_names": ", ".join(unmatched_names) if unmatched_names else "—",
    }
    res.artifact = artifacts.matched(data_dir)
    return res


def cmd_match_correct(args, config) -> StepResult:
    data_dir = config.data_dir
    artifacts.require(artifacts.matched(data_dir), "fencers_matched.json", "run `match` first")
    hr_id = None if args.none else args.hr_id

    res = StepResult(step="match-correct")
    with timed(res):
        summary = apply_correction(data_dir, args.name, hr_id)

    res.ok = summary.startswith("Corrected:") or summary.startswith("No change")
    res.summary = summary
    res.artifact = artifacts.matched(data_dir)
    return res


def cmd_hr_search(args, config) -> StepResult:
    data_dir = config.data_dir
    res = StepResult(step="hr-search")
    with timed(res):
        out = search_profiles(data_dir, args.name)
    lines = out.splitlines()
    res.ok = not out.startswith("error") and not out.startswith("No profiles")
    res.summary = f"{len(lines)} candidate(s) for '{args.name}'" if res.ok else out
    res.details = {f"#{i + 1}": ln for i, ln in enumerate(lines)} if res.ok else {}
    return res
