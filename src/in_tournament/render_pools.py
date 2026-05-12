"""Pool table PDF renderer for the in-tournament phase.

Reads pool assignments from the per-discipline Google Sheet (one worksheet
per pool, each with a 'Name' column), fills the pool_table_N.typ Typst
template, compiles each pool to a PDF, and concatenates them into one file.

Entry point:
    render_pools_for_disc(disc_code, user_config_path) -> tuple[str, bytes]
    Returns (filename, pdf_bytes) for the merged PDF of all pools.
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


def _abbrev(name: str) -> str:
    """Return initials for the score-grid column header (e.g. 'John Smith' → 'J.S.')."""
    parts = name.strip().split()
    return "".join(p[0].upper() for p in parts if p) if parts else name


def _merge_pdfs(pdf_list: list[bytes]) -> bytes:
    import io
    from pypdf import PdfWriter
    writer = PdfWriter()
    for pdf_bytes in pdf_list:
        writer.append(io.BytesIO(pdf_bytes))
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()



# ── Sheet reading ──────────────────────────────────────────────────────────────

def _read_pools_from_sheet(sheet_url: str, creds_path: str) -> list[tuple[int, list[str]]]:
    """Return [(pool_no, [fencer_names])] from the 'Pool standings' worksheet.

    The worksheet has columns: Fencers | Pool 1 | Pool 2 | …
    Each 'Pool N' column lists fencers in that pool. Pool number is parsed from
    the column header; columns are returned in ascending pool-number order.
    """
    import gspread
    gc = gspread.service_account(filename=creds_path)
    sh = gc.open_by_url(sheet_url)

    pool_ws = next(
        (ws for ws in sh.worksheets() if ws.title.strip().lower() == "pool standings"),
        None,
    )
    if pool_ws is None:
        return []

    rows = pool_ws.get_all_values()
    if not rows:
        return []

    header = [h.strip() for h in rows[0]]
    candidates: list[tuple[int, list[str]]] = []
    for i, h in enumerate(header):
        m = re.match(r"pool\s*(\d+)", h, re.IGNORECASE)
        if not m:
            continue
        pool_no = int(m.group(1))
        names = [row[i].strip() for row in rows[1:] if i < len(row) and row[i].strip()]
        if names:
            candidates.append((pool_no, names))

    candidates.sort(key=lambda t: t[0])
    return candidates


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
) -> tuple[str, bytes]:
    """Render pool table PDFs for one discipline and merge them into one file.

    Reads pool assignments from the configured Google Sheet, renders one PDF
    per pool using the matching pool_table_N.typ template, concatenates them,
    and returns (filename, pdf_bytes) for the merged PDF.

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

    rendered: list[bytes] = []
    for pool_no, names in pools:
        try:
            pdf = _render_one_pdf(pool_no, names, tournament, discipline)
            rendered.append(pdf)
            log.info("Rendered %s pool %d (%d fencers)", disc_code, pool_no, len(names))
        except ValueError as e:
            log.warning("Skipping pool %d of %s: %s", pool_no, disc_code, e)

    if not rendered:
        raise ValueError(
            f"No pools could be rendered for {disc_code} — "
            "check that each pool has 4–8 fencers"
        )

    merged = _merge_pdfs(rendered)
    filename = f"{disc_code}_pools.pdf"
    log.info("Merged %d pool(s) into %s", len(rendered), filename)
    return filename, merged
