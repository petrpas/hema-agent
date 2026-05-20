"""Render the participant declaration form as PDF using the declaration.typ template.

Pre-tournament variant. Mirrors `in_tournament.render_declaration` —
takes a flat name list and lets Typst's 2-column page layout split a
single `{{rows}}` block automatically. The two only differ in where
the names come from (pre: Fencers tab; in: pool standings sheets).
"""

import tempfile
from pathlib import Path

import typst

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


def render_declaration_pdf(names: list[str], tournament_name: str, date: str) -> bytes:
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
