"""Render the participant declaration form as PDF using the declaration.typ template."""

import tempfile
from pathlib import Path

import typst

from in_tournament.render_pools import _read_pools_from_sheet

_SRC_DIR       = Path(__file__).parent.parent
_TEMPLATES_DIR = _SRC_DIR / "shared" / "typst" / "templates"
_FONTS_DIR     = _SRC_DIR / "shared" / "typst" / "fonts"


def load_fencer_names_from_sheets(
    data_sheet_urls: dict[str, str],
    creds_path: str,
) -> list[str]:
    """Collect unique fencer names across all discipline pool-standings sheets."""
    seen: set[str] = set()
    names: list[str] = []
    for sheet_url in data_sheet_urls.values():
        pools = _read_pools_from_sheet(sheet_url, creds_path)
        for _, pool_names in pools:
            for name in pool_names:
                if name and name not in seen:
                    seen.add(name)
                    names.append(name)
    return names


def _surname_key(name: str) -> tuple[str, str]:
    parts = name.strip().split()
    surname = parts[-1] if len(parts) > 1 else name
    return (surname.casefold(), name.casefold())


def _escape_typst(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("#", "\\#")


def _row(n: int, name: str) -> str:
    return f"[{n}],[{_escape_typst(name)}],[],[],"


def render_declaration_pdf(names: list[str], tournament_name: str, date: str) -> bytes:
    """Render a signed declaration form PDF for the given list of participant names.

    Names are sorted alphabetically by surname. The 2-column page layout in the
    template causes Typst to split the table naturally across both columns.
    """
    sorted_names = sorted(names, key=_surname_key)
    rows = "\n".join(_row(i + 1, name) for i, name in enumerate(sorted_names))

    template = (_TEMPLATES_DIR / "declaration.typ").read_text(encoding="utf-8")
    source = (
        template
        .replace("{{tournament}}", _escape_typst(tournament_name))
        .replace("{{date}}", _escape_typst(date))
        .replace("{{rows}}", rows)
    )

    with tempfile.NamedTemporaryFile(suffix=".typ", mode="w", encoding="utf-8", delete=False) as f:
        f.write(source)
        tmp = Path(f.name)
    try:
        return typst.compile(str(tmp), format="pdf", font_paths=[str(_FONTS_DIR)])
    finally:
        tmp.unlink(missing_ok=True)
