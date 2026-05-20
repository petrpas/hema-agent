"""Typst renderers — `render-lists` and `render-declaration` (both remote).

Both source data from the Fencers tab of the output sheet so manual
edits there propagate to the rendered artifacts. Thin wrappers around
the same functions the Discord bot calls.
"""

from pre_tournament.cli.errors import ArtifactMissing, StepFailed
from pre_tournament.cli.steps._base import StepResult, require_remote, timed
from step_typst import render_all


def _open_output_sheet(config):
    """Open the configured output sheet (caller has already gated remote)."""
    import gspread

    gc = gspread.service_account(filename=config.creds_path)
    return gc.open_by_url(config.output_sheet_url)


def _read_fencer_names_from_sheet(sh) -> list[str]:
    """Return every non-empty value in the Name column of the Fencers tab."""
    ws = sh.worksheet("Fencers")
    rows = ws.get_all_values()
    if len(rows) < 2:
        return []
    header = [c.strip() for c in rows[0]]
    try:
        i_name = header.index("Name")
    except ValueError as e:
        raise StepFailed(
            f"Fencers tab is missing a 'Name' column (header: {header})"
        ) from e
    return [
        r[i_name].strip() for r in rows[1:]
        if i_name < len(r) and r[i_name].strip()
    ]


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
    res.artifact = next(
        (p for p in paths if p.name == "fencers_list.png"),
        paths[0] if paths else None,
    )
    return res


def cmd_render_declaration(args, config) -> StepResult:
    """Render the participant declaration PDF from the Fencers tab."""
    if not getattr(config, "output_sheet_url", None):
        raise ArtifactMissing(
            "no output_sheet_url — run `sheet-set-url URL` (or `sheet-create`) first"
        )
    require_remote(args, "render-declaration (Google Sheets read)")

    # Lazy import: keep the renderer's dependency chain off unrelated commands.
    from pre_tournament.render_declaration import render_declaration_pdf

    res = StepResult(step="render-declaration")
    with timed(res):
        sh = _open_output_sheet(config)
        names = _read_fencer_names_from_sheet(sh)
        if not names:
            raise StepFailed("Fencers tab is empty — nothing to put on the declaration")
        pdf_bytes = render_declaration_pdf(names, config.tournament_name, args.date)
        out = config.data_dir / "lists" / "declaration.pdf"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(pdf_bytes)

    res.summary = f"rendered declaration.pdf ({len(names)} fencers, {len(pdf_bytes)} bytes)"
    res.details = {"date": args.date, "fencers": len(names), "source": "Fencers tab"}
    res.artifact = out
    return res
