"""Pool table PDF renderer for the in-tournament phase.

Reads pool assignments from the per-discipline Google Sheet (one worksheet
per pool, each with a 'Name' column), fills the pool_table_N.typ Typst
template, and compiles it to a PDF.

Entry point:
    render_pools_for_disc(disc_code, user_config_path) -> list[tuple[str, bytes]]
    Returns (filename, pdf_bytes) pairs, one per pool, sorted by pool number.
"""

import json
import logging
import re
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

_SRC_DIR       = Path(__file__).parent.parent          # src/
_TEMPLATES_DIR = _SRC_DIR / "shared" / "typst" / "templates"
_FONTS_DIR     = _SRC_DIR / "shared" / "typst" / "fonts"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _escape_typst(s: str) -> str:
    """Escape a string for safe use in Typst markup content."""
    for ch in ("\\", "#", "[", "]", "@", "*", "_", "`", "~", "$"):
        s = s.replace(ch, f"\\{ch}")
    return s


def _abbrev(name: str, max_len: int = 12) -> str:
    """Return the last name, truncated, for the score-grid column header."""
    parts = name.strip().split()
    return (parts[-1] if parts else name)[:max_len]


def _pool_number(title: str, fallback: int) -> int:
    """Extract the first integer from a worksheet title, or use fallback."""
    m = re.search(r"\d+", title)
    return int(m.group()) if m else fallback


# ── Sheet reading ──────────────────────────────────────────────────────────────

def _read_pools_from_sheet(sheet_url: str, creds_path: str) -> list[tuple[int, list[str]]]:
    """Return [(pool_no, [fencer_names])] from the discipline's Google Sheet.

    A worksheet is treated as a pool if it has a 'Name' column header in row 1
    and at least one non-empty fencer row beneath it. Worksheets are sorted by
    title; pool number is parsed from the title (first digit run) or assigned
    sequentially.
    """
    import gspread
    gc = gspread.service_account(filename=creds_path)
    sh = gc.open_by_url(sheet_url)

    candidates: list[tuple[str, list[str]]] = []
    for ws in sh.worksheets():
        rows = ws.get_all_values()
        if not rows:
            continue
        header = [h.strip().lower() for h in rows[0]]
        if "name" not in header:
            continue
        col = header.index("name")
        names = [r[col].strip() for r in rows[1:] if col < len(r) and r[col].strip()]
        if names:
            candidates.append((ws.title, names))

    # Sort worksheets by their numeric component first, then alphabetically
    candidates.sort(key=lambda t: (_pool_number(t[0], 9999), t[0]))

    return [
        (_pool_number(title, i), names)
        for i, (title, names) in enumerate(candidates, start=1)
    ]


# ── Typst rendering ────────────────────────────────────────────────────────────

def _render_one_pdf(
    pool_no: int,
    names: list[str],
    tournament: str,
    discipline: str,
) -> bytes:
    """Fill in the pool_table_N.typ template and compile it to PDF bytes."""
    import typst

    n = len(names)
    template_path = _TEMPLATES_DIR / f"pool_table_{n}.typ"
    if not template_path.exists():
        raise ValueError(f"No template for pool size {n} — supported sizes: 4–8")

    source = template_path.read_text(encoding="utf-8")
    source = source.replace("{{tournament}}", _escape_typst(tournament))
    source = source.replace("{{discipline}}", _escape_typst(discipline))
    source = source.replace("{{pool_no}}", str(pool_no))
    for i, name in enumerate(names, start=1):
        source = source.replace(f"{{{{fencer_{i}}}}}", _escape_typst(name))
        source = source.replace(f"{{{{f{i}}}}}", _escape_typst(_abbrev(name)))

    with tempfile.NamedTemporaryFile(
        suffix=".typ", mode="w", encoding="utf-8", delete=False
    ) as f:
        f.write(source)
        tmp = Path(f.name)
    try:
        return typst.compile(str(tmp), format="pdf", font_paths=[str(_FONTS_DIR)])
    finally:
        tmp.unlink(missing_ok=True)


# ── Public entry point ─────────────────────────────────────────────────────────

def render_pools_for_disc(
    disc_code: str,
    user_config_path: Path,
) -> list[tuple[str, bytes]]:
    """Render pool table PDFs for one discipline.

    Reads pool assignments from the configured Google Sheet, renders one PDF
    per pool using the matching pool_table_N.typ template, and returns a list
    of (filename, pdf_bytes) pairs ordered by pool number.

    Raises ValueError if the sheet URL is not configured, no pools are found,
    or no pools could be rendered (e.g. unsupported sizes).
    """
    from shared.config import load_agent_config

    user_cfg: dict = {}
    if user_config_path.exists():
        with open(user_config_path) as f:
            user_cfg = json.load(f)

    sheet_url: str | None = user_cfg.get("data_sheet_urls", {}).get(disc_code)
    if not sheet_url:
        raise ValueError(
            f"No data sheet URL configured for {disc_code} — "
            "run `create_data_sheets` in #setup first"
        )

    tournament: str = (
        user_cfg.get("tournament_display_name")
        or user_cfg.get("tournament_name", "Tournament")
    )
    discipline: str = user_cfg.get("disciplines", {}).get(disc_code, disc_code)

    creds_path = load_agent_config().reg_agent.creds_path

    log.info("Reading pools for %s from sheet", disc_code)
    pools = _read_pools_from_sheet(sheet_url, creds_path)
    if not pools:
        raise ValueError(
            f"No pool worksheets found for {disc_code} — "
            "each pool must be a separate worksheet with a 'Name' column"
        )

    results: list[tuple[str, bytes]] = []
    for pool_no, names in pools:
        try:
            pdf = _render_one_pdf(pool_no, names, tournament, discipline)
            filename = f"{disc_code}_pool_{pool_no}.pdf"
            results.append((filename, pdf))
            log.info("Rendered %s (%d fencers)", filename, len(names))
        except ValueError as e:
            log.warning("Skipping pool %d of %s: %s", pool_no, disc_code, e)

    if not results:
        raise ValueError(
            f"No pools could be rendered for {disc_code} — "
            "check that each pool has 4–8 fencers"
        )
    return results
