"""Typst renderers — `render-lists` (remote) and `render-declaration` (offline).

Both are thin wrappers around the same functions the Discord bot calls.
"""

from pre_tournament.cli.errors import ArtifactMissing
from pre_tournament.cli.steps._base import StepResult, artifacts, require_remote, timed
from utils import FENCERS_DEDUPED_FILE, load_fencers_list
from step_typst import render_all


def cmd_render_lists(args, config) -> StepResult:
    """Render Fencers + per-discipline PNGs from the output sheet."""
    if not getattr(config, "output_sheet_url", None):
        raise ArtifactMissing(
            "no output_sheet_url — run `sheet-set-url URL` (or `sheet-create`) first"
        )
    require_remote(args, "render-lists (Google Sheets read)")

    res = StepResult(step="render-lists")
    with timed(res):
        paths = render_all(config)

    res.summary = f"rendered {len(paths)} file(s) → {config.data_dir / 'lists'}"
    res.details = {"files": ", ".join(p.name for p in paths)}
    # Prefer fencers_list.png as the "primary" artifact; fall back to the
    # first output (e.g. if only discipline tabs rendered).
    res.artifact = next(
        (p for p in paths if p.name == "fencers_list.png"),
        paths[0] if paths else None,
    )
    return res


def cmd_render_declaration(args, config) -> StepResult:
    """Render the participant declaration PDF from `fencers_deduped.json`."""
    data_dir = config.data_dir
    artifacts.require(
        artifacts.deduped(data_dir),
        FENCERS_DEDUPED_FILE,
        "run `dedup` first",
    )
    fencers = load_fencers_list(data_dir, FENCERS_DEDUPED_FILE) or []

    # Lazy import: keeps the (untracked, optional) renderer's dependency
    # chain off unrelated commands.
    from pre_tournament.render_declaration import render_declaration_pdf

    res = StepResult(step="render-declaration")
    with timed(res):
        pdf_bytes = render_declaration_pdf(fencers, config.tournament_name, args.date)
        out = data_dir / "lists" / "declaration.pdf"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(pdf_bytes)

    res.summary = f"rendered declaration.pdf ({len(fencers)} fencers, {len(pdf_bytes)} bytes)"
    res.details = {"date": args.date, "fencers": len(fencers)}
    res.artifact = out
    return res
