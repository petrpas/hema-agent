"""Render the participant declaration form as PDF using the declaration.typ template.

Pre-tournament variant: takes a FencerRecord list (e.g. from
fencers_deduped.json) rather than the in-tournament version's pool-sheet
name list. Otherwise mirrors `in_tournament.render_declaration` — the
template uses Typst's 2-column page layout to split a single `{{rows}}`
block automatically; no manual column split is needed.
"""

import tempfile
from pathlib import Path

import typst

from pre_tournament.reg_agent.models import FencerRecord

_TEMPLATES_DIR = Path(__file__).parent.parent / "shared" / "typst" / "templates"
_FONTS_DIR = Path(__file__).parent.parent / "shared" / "typst" / "fonts"


def _escape_typst(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("#", "\\#")


def _row(n: int, name: str) -> str:
    return f"[{n}],[{_escape_typst(name)}],[],[],"


def _surname_key(name: str) -> tuple[str, str]:
    parts = name.strip().split()
    surname = parts[-1] if len(parts) > 1 else name
    return (surname.casefold(), name.casefold())


def render_declaration_pdf(
    fencers: list[FencerRecord],
    tournament_name: str,
    date: str,
) -> bytes:
    sorted_names = sorted((f.name for f in fencers), key=_surname_key)
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
