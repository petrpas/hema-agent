"""Elimination bracket PDF/PNG renderer.

Reads the ordered pool-stage standings from the 'Pool results' worksheet and
renders a bracket PDF (and optionally PNG) using the elimination_N.typ template.

Entry point:
    render_elim_bracket(disc, sheet_url, creds_path, tournament, discipline, size)
    -> tuple[(pdf_name, pdf_bytes), (png_name, png_bytes)]
"""

import logging
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

_SRC_DIR       = Path(__file__).parent.parent        # src/
_TEMPLATES_DIR = _SRC_DIR / "shared" / "typst" / "templates"
_FONTS_DIR     = _SRC_DIR / "shared" / "typst" / "fonts"

_BYE = "[ #h(1em) ---]"

_SUPPORTED_SIZES = (8, 16, 32, 64)


def _escape_str(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("#", "\\#")


def _build_fencers_block(names: list[str], size: int) -> str:
    """Generate #let fencers and #let is_bye Typst blocks for *size* slots.

    Slots 0..len(names)-1 hold quoted fencer names (content); remaining slots
    are bye content blocks. is_bye mirrors which slots are byes so templates
    can branch on it (Typst cannot compare content blocks with ==).
    """
    fencer_entries: list[str] = []
    bye_entries: list[str] = []
    for name in names:
        fencer_entries.append(f'  "{_escape_str(name)}"')
        bye_entries.append("  false")
    for _ in range(size - len(names)):
        fencer_entries.append(f"  {_BYE}")
        bye_entries.append("  true")
    fencers = "#let fencers = (\n" + ",\n".join(fencer_entries) + ",\n)"
    is_bye = "#let is_bye = (\n" + ",\n".join(bye_entries) + ",\n)"
    return fencers + "\n" + is_bye


def _read_pool_results_ordered(sheet_url: str, creds_path: str) -> list[str]:
    """Return fencer names in pool-stage rank order (1st, 2nd, …) from the sheet."""
    import gspread
    gc = gspread.service_account(filename=creds_path)
    sh = gc.open_by_url(sheet_url)
    try:
        ws = sh.worksheet("Pool results")
    except gspread.WorksheetNotFound:
        raise ValueError("'Pool results' worksheet not found — run /calc_pools first")
    rows = ws.get_all_values()
    if len(rows) < 2:
        raise ValueError("'Pool results' worksheet has no data rows — run /calc_pools first")
    # Column 1 (index 1) is Name; column 0 is the ordinal rank.
    names: list[str] = []
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        name = row[1].strip() if len(row) > 1 else ""
        if name:
            names.append(name)
    return names


def _bracket_size(n: int) -> int:
    """Return the smallest supported bracket size >= n."""
    for size in _SUPPORTED_SIZES:
        if n <= size:
            return size
    raise ValueError(
        f"{n} fencers exceeds the largest supported bracket size ({max(_SUPPORTED_SIZES)})"
    )


def render_elim_bracket(
    disc: str,
    sheet_url: str,
    creds_path: str,
    tournament: str,
    discipline: str,
    size: int | None = None,
) -> tuple[tuple[str, bytes], tuple[str, bytes]]:
    """Render an elimination bracket PDF and PNG.

    Reads pool-stage standings from *sheet_url*, picks the right template for
    the entry count (or the explicitly given *size*), fills in the fencer names,
    and compiles with Typst.

    Returns ((pdf_name, pdf_bytes), (png_name, png_bytes)).
    """
    import typst

    names = _read_pool_results_ordered(sheet_url, creds_path)
    if not names:
        raise ValueError("No ranked fencers found in 'Pool results' worksheet")

    bracket_size = size if size is not None else _bracket_size(len(names))
    if bracket_size not in _SUPPORTED_SIZES:
        raise ValueError(
            f"Bracket size {bracket_size} is not supported. "
            f"Supported sizes: {', '.join(str(s) for s in _SUPPORTED_SIZES)}"
        )
    if len(names) > bracket_size:
        raise ValueError(
            f"{len(names)} fencers exceed the requested bracket size of {bracket_size}"
        )

    template_path = _TEMPLATES_DIR / f"elimination_{bracket_size}.typ"
    if not template_path.exists():
        raise ValueError(f"Template not found: {template_path.name}")

    fencers_block = _build_fencers_block(names, bracket_size)

    source = template_path.read_text(encoding="utf-8")
    source = (
        source
        .replace("{{fencers}}", fencers_block)
        .replace("{{tournament}}", _escape_str(tournament))
        .replace("{{discipline}}", _escape_str(discipline))
    )

    with tempfile.NamedTemporaryFile(suffix=".typ", mode="w", encoding="utf-8", delete=False) as f:
        f.write(source)
        tmp = Path(f.name)
    try:
        pdf_bytes = typst.compile(str(tmp), format="pdf", font_paths=[str(_FONTS_DIR)])
        png_result = typst.compile(str(tmp), format="png", font_paths=[str(_FONTS_DIR)])
    finally:
        tmp.unlink(missing_ok=True)

    png_bytes: bytes = png_result[0] if isinstance(png_result, list) else png_result

    pdf_name = f"{disc}_elimination_{bracket_size}.pdf"
    png_name = f"{disc}_elimination_{bracket_size}.png"
    log.info(
        "Rendered elimination bracket for %s: %d fencers in %d-slot bracket",
        disc, len(names), bracket_size,
    )
    return (pdf_name, pdf_bytes), (png_name, png_bytes)
