"""Step 5 — fetch HEMA ratings/ranks for fencers with an hr_id."""

import shutil
from datetime import date

from pre_tournament.cli.steps._base import StepResult, artifacts, timed
from step5_ratings import (
    RATING_HTML_DIR_PREFIX,
    RATINGS_CACHE_PREFIX,
    fetch_ratings,
)
from utils import load_fencers_list, FENCERS_DEDUPED_FILE


def cmd_ratings(args, config) -> StepResult:
    data_dir = config.data_dir
    artifacts.require(artifacts.deduped(data_dir), "fencers_deduped.json", "run `dedup` first")
    fencers = load_fencers_list(data_dir, FENCERS_DEDUPED_FILE)

    today = date.today().strftime("%Y_%m_%d")
    if args.force:
        # Drop today's ratings cache; keep fighter HTML so we don't hammer
        # hemaratings.com unless --force-html is also given.
        artifacts.clear(data_dir / f"{RATINGS_CACHE_PREFIX}{today}.json")
    if getattr(args, "force_html", False):
        html_dir = data_dir / f"{RATING_HTML_DIR_PREFIX}{today}"
        if html_dir.exists():
            shutil.rmtree(html_dir)

    res = StepResult(step="ratings")
    with timed(res):
        ratings, not_found = fetch_ratings(fencers, config)

    total_with_id = sum(1 for f in fencers if f.hr_id is not None)
    res.summary = f"ratings fetched for {len(ratings)}/{total_with_id} fencers"
    res.details = {"rated": len(ratings), "with_hr_id": total_with_id}
    if not_found:
        id_to_name = {f.hr_id: f.name for f in fencers if f.hr_id is not None}
        desc = ", ".join(
            f"{id_to_name.get(h, '?')} (hr_id={h})" for h in sorted(not_found)
        )
        res.warnings.append(
            f"{len(not_found)} profile(s) returned 404 (likely wrong hr_id — "
            f"use `match-correct`): {desc}"
        )
    res.artifact = artifacts.latest_ratings(data_dir)
    return res
