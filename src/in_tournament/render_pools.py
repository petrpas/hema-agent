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

    # Stop before any reference table (Clubs/Seeds/Nats) written at A12+.
    _ref_labels = {"clubs", "seeds", "nats"}
    _fencer_end = next(
        (i for i, row in enumerate(rows[1:], 1)
         if row and row[0].strip().lower() in _ref_labels),
        len(rows),
    )
    fencer_rows = rows[1:_fencer_end]

    header = [h.strip() for h in rows[0]]
    candidates: list[tuple[int, list[str]]] = []
    for i, h in enumerate(header):
        m = re.match(r"pool\s*(\d+)", h, re.IGNORECASE)
        if not m:
            continue
        pool_no = int(m.group(1))
        names = [row[i].strip() for row in fencer_rows if i < len(row) and row[i].strip()]
        if names:
            candidates.append((pool_no, names))

    candidates.sort(key=lambda t: t[0])
    return candidates


# ── Typst rendering ────────────────────────────────────────────────────────────

def _col_count(num_pools: int) -> int:
    if num_pools <= 1:
        return 1
    if num_pools >= 10 or num_pools % 2 != 0:
        return 3
    return 2


def _escape_str(s: str) -> str:
    """Escape a string for embedding inside a Typst string literal."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("#", "\\#")


def _build_list_block(pools: list[tuple[int, list[str]]]) -> str:
    """Build the #let col_count / #let pools data block for pools_seed.typ."""
    cols = _col_count(len(pools))
    lines = [f"#let col_count = {cols}", "#let pools = ("]
    for pool_no, names in pools:
        escaped = ", ".join(f'"{_escape_str(n)}"' for n in names)
        lines.append(f"  ({pool_no}, ({escaped},)),")
    lines.append(")")
    return "\n".join(lines)


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


# ── Public entry points ────────────────────────────────────────────────────────

def read_pools_for_disc(
    disc_code: str,
    user_config_path: Path,
) -> list[tuple[int, list[str]]]:
    """Return pool assignments for one discipline as [(pool_no, [fencer_names])].

    Raises ValueError if the sheet URL is not configured or no pools are found.
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

    creds_path = load_agent_config().reg_agent.creds_path
    pools = _read_pools_from_sheet(sheet_url, creds_path)
    if not pools:
        raise ValueError(
            f"No pools found for {disc_code} in the Pool standings worksheet"
        )
    return pools


def render_pools_list_for_disc(
    disc_code: str,
    user_config_path: Path,
) -> tuple[str, bytes]:
    """Render a seeded pool list PDF using the pools_seed.typ template.

    Returns (filename, pdf_bytes) where filename is '<disc>_pools_list.pdf'.
    """
    import typst

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

    from shared.config import load_agent_config
    creds_path = load_agent_config().reg_agent.creds_path
    pools = _read_pools_from_sheet(sheet_url, creds_path)
    if not pools:
        raise ValueError(f"No pools found for {disc_code} in the Pool standings worksheet")

    template = (_TEMPLATES_DIR / "pools_seed.typ").read_text(encoding="utf-8")
    source = (
        template
        .replace("{{data}}", _build_list_block(pools))
        .replace("{{tournament_name}}", _escape_typst(tournament))
        .replace("{{discipline_name}}", _escape_typst(discipline))
    )

    with tempfile.NamedTemporaryFile(suffix=".typ", mode="w", encoding="utf-8", delete=False) as f:
        f.write(source)
        tmp = Path(f.name)
    try:
        pdf_bytes = typst.compile(str(tmp), format="pdf", font_paths=[str(_FONTS_DIR)])
    finally:
        tmp.unlink(missing_ok=True)

    filename = f"{disc_code}_pools_list.pdf"
    log.info("Rendered pool list PDF for %s: %s", disc_code, filename)
    return filename, pdf_bytes


_TYPST_MINUS = "−"  # U+2212 MINUS SIGN (used in pool results template)


def _read_pool_results_rows(sh) -> list[dict]:
    """Read 'Pool results' worksheet → list of row dicts (positional columns)."""
    import gspread
    try:
        ws = sh.worksheet("Pool results")
    except gspread.WorksheetNotFound:
        raise ValueError("'Pool results' worksheet not found in data sheet")
    rows = ws.get_all_values()
    if len(rows) < 2:
        return []
    def _col(row: list, i: int) -> str:
        return row[i].strip() if i < len(row) else ""

    result = []
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        result.append({
            "ord":     _col(row, 0),
            "name":    _col(row, 1),
            "nat":     _col(row, 2),
            "club":    _col(row, 3),
            "matches": _col(row, 4),
            "victory": _col(row, 5),
            "wm":      _col(row, 6),
            "ts":      _col(row, 7),
            "tr":      _col(row, 8),
            "index":   _col(row, 9),
        })
    return result


def _build_pool_results_table(rows: list[dict]) -> str:
    """Render pool result rows as Typst table cells for pool_results.typ."""
    lines = []
    for r in rows:
        try:
            ind = int(r["index"])
            ind_str = f"+{ind}" if ind >= 0 else f"{_TYPST_MINUS}{abs(ind)}"
        except (ValueError, TypeError):
            ind_str = str(r.get("index", "0"))
        lines.append(
            f"  [{r['ord']}], [{_escape_typst(r['name'])}], [{_escape_typst(r['club'])}], "
            f"[*{r['victory']}* / *{r['matches']}*], [*{ind_str}*], "
            f"[=], [{r['ts']}], [{_TYPST_MINUS}], [{r['tr']}],"
        )
    return "\n".join(lines)


def render_pool_results_for_disc(
    disc_code: str,
    sheet_url: str,
    creds_path: str,
    tournament: str,
    discipline: str,
) -> tuple[tuple[str, bytes], tuple[str, bytes], list[dict]]:
    """Read 'Pool results' worksheet and render it to PDF and PNG.

    Returns ((pdf_filename, pdf_bytes), (png_filename, png_bytes), rows).
    Raises ValueError if the worksheet is missing or has no data rows.
    """
    import gspread
    import typst

    gc = gspread.service_account(filename=creds_path)
    sh = gc.open_by_url(sheet_url)
    rows = _read_pool_results_rows(sh)
    if not rows:
        raise ValueError(f"No data in 'Pool results' worksheet for {disc_code} — run /calc_pools first")

    table_content = _build_pool_results_table(rows)
    template = (_TEMPLATES_DIR / "pool_results.typ").read_text(encoding="utf-8")
    source = (
        template
        .replace("{{tournament}}", _escape_typst(tournament))
        .replace("{{discipline}}", _escape_typst(discipline))
        .replace("{{table_content}}", table_content)
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
    log.info("Rendered pool results for %s: %d rows", disc_code, len(rows))
    return (f"{disc_code}_pool_results.pdf", pdf_bytes), (f"{disc_code}_pool_results.png", png_bytes), rows


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
