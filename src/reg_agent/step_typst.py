"""Typst-based PNG rendering for fencer and discipline participant lists.

Data source: the output Google Sheet (after the organiser has finalised it).

Entry point: render_all(config) -> list[Path]
  Reads Fencers + discipline worksheets via gspread, renders fencers.typ and
  disciplines.typ into PNG files under data/{tournament}/lists/.

Font requirement:
  GFS Neohellenic .ttf files must be present in src/typst/fonts/.
"""

import logging
import tempfile
from pathlib import Path

import gspread
import typst

log = logging.getLogger(__name__)

_TYPST_DIR = Path(__file__).parent.parent / "typst"   # src/typst/
_TEMPLATES_DIR = _TYPST_DIR / "templates"
_FONTS_DIR = _TYPST_DIR / "fonts"
_LISTS_SUBDIR = "lists"

# Column indices (0-indexed, matching get_all_values() rows) in the output sheet worksheets
# Fencers worksheet: Reg. | Name | Nat. | Club | HR_ID | Disciplines | Paid | ...
_F_NAME = 1
_F_NAT  = 2
_F_CLUB = 3
_F_HRID = 4
_F_DISC = 5
_F_PAID = 6

# Discipline worksheet: No. | Name | Nat. | Club | HR_ID | HRating | HRank
_D_SEED = 0
_D_NAME = 1
_D_NAT  = 2
_D_CLUB = 3
_D_HRID = 4
_D_RANK = 6


# ── Sheet reading ──────────────────────────────────────────────────────────────

def _read_worksheet(ws: gspread.Worksheet) -> list[list[str]]:
    """Return all non-empty data rows (skip header row 0, skip rows with blank name)."""
    all_rows = ws.get_all_values()
    if not all_rows:
        return []
    return [row for row in all_rows[1:] if len(row) > 1 and row[_F_NAME].strip()]


def _read_discipline_worksheet(ws: gspread.Worksheet) -> tuple[list[str], list[list[str]]]:
    """Return (header, data_rows) for a discipline worksheet.

    Data rows are non-empty (name column not blank), header is the first row.
    """
    all_rows = ws.get_all_values()
    if not all_rows:
        return [], []
    header = all_rows[0]
    rows = [row for row in all_rows[1:] if len(row) > 1 and row[_D_NAME].strip()]
    return header, rows


def _col_index(header: list[str], name: str) -> int | None:
    """Return 0-based index of a column by header name, or None if not found."""
    for i, h in enumerate(header):
        if h.strip() == name:
            return i
    return None


def _paid_map(fencers_rows: list[list[str]]) -> dict[str, str]:
    """Build name → paid-value map from the Fencers worksheet rows."""
    result: dict[str, str] = {}
    for row in fencers_rows:
        name = row[_F_NAME].strip()
        paid = row[_F_PAID].strip() if len(row) > _F_PAID else ""
        if name:
            result[name] = paid
    return result


# ── Typst source generation ────────────────────────────────────────────────────

_NNBSP = "\u202f"  # NARROW NO-BREAK SPACE — thousands separator


def _fmt_number(s: str) -> str:
    """Format a numeric string with U+202F as the thousands separator.

    Non-numeric or empty values are returned unchanged.
    """
    stripped = s.strip()
    if not stripped:
        return s
    try:
        n = int(stripped)
    except ValueError:
        return s
    return f"{n:,}".replace(",", _NNBSP)


def _escape_typst(s: str) -> str:
    """Escape a value for use inside a Typst double-quoted string."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("#", "\\#")


def _typst_data_block(rows: list[list[str]]) -> str:
    """Render a list of rows as a Typst data tuple literal."""
    lines = []
    for row in rows:
        cells = ", ".join(f'"{_escape_typst(cell)}"' for cell in row)
        lines.append(f"  ({cells}),")
    return "#let data = (\n" + "\n".join(lines) + "\n)"


def _inject(template: str, data_block: str, last_in: int, title: str) -> str:
    """Substitute {{data}}, {{last_in}}, and title placeholders into a template source."""
    return (
        template
        .replace("{{data}}", data_block)
        .replace("{{last_in}}", str(last_in))
        .replace("{{tournament_name}} --- {{discipline_name}}", _escape_typst(title))
        .replace("{{tournament_name}}", _escape_typst(title))
    )


def _render_fencers_source(
    rows: list[list[str]],
    template: str,
    tournament_name: str,
) -> str:
    """Build .typ source for the overall fencer list (fencers.typ).

    Columns: No., Fencer, Nat., Club, HRID, Reg. into, Paid
    Row order is preserved from the sheet (organiser controls ordering).
    No last_in separator — set last_in = row count so it coincides with the last row.
    """
    def _col(row: list[str], idx: int) -> str:
        return row[idx].strip() if len(row) > idx else ""

    data_rows = []
    for i, row in enumerate(rows, start=1):
        data_rows.append([
            str(i),
            _col(row, _F_NAME),
            _col(row, _F_NAT),
            _col(row, _F_CLUB),
            _fmt_number(_col(row, _F_HRID)),
            _col(row, _F_DISC),
            _col(row, _F_PAID),
        ])
    return (
        template
        .replace("{{data}}", _typst_data_block(data_rows))
        .replace("{{tournament_name}}", _escape_typst(tournament_name))
    )


def _render_discipline_source(
    header: list[str],
    rows: list[list[str]],
    paid: dict[str, str],
    limit: int | None,
    template: str,
    title: str,
) -> str:
    """Build .typ source for one discipline list (disciplines.typ).

    Columns: Seed, Fencer, Nat., Club, HRID, RANK, Paid
    Rows are sorted by the Seed column (located by header name).
    last_in = min(len, limit) if limit set.
    Paid is cross-referenced from the Fencers worksheet by name.
    """
    def _col(row: list[str], idx: int) -> str:
        return row[idx].strip() if len(row) > idx else ""

    seed_idx = _col_index(header, "Seed") or _D_SEED
    rank_idx = _col_index(header, "HRank") or _D_RANK

    def _seed_key(row: list[str]) -> int:
        try:
            return int(_col(row, seed_idx))
        except ValueError:
            return 9999

    data_rows = []
    for row in sorted(rows, key=_seed_key):
        name = _col(row, _D_NAME)
        data_rows.append([
            _col(row, seed_idx),
            name,
            _col(row, _D_NAT),
            _col(row, _D_CLUB),
            _fmt_number(_col(row, _D_HRID)),
            _fmt_number(_col(row, rank_idx)),
            paid.get(name, ""),
        ])
    n = len(data_rows)
    last_in = min(n, limit) if limit is not None else n
    return _inject(template, _typst_data_block(data_rows), last_in, title)


# ── Typst compilation ──────────────────────────────────────────────────────────

def _compile_typst(source: str, out_path: Path) -> list[Path]:
    """Compile Typst source to PNG, save the .typ source, and return all written paths.

    Always writes a .typ source file alongside the PNG(s) so it can be shared on request.
    Single-page documents produce out_path. Multi-page documents produce
    out_path.stem + "-{N}.png" siblings. The .typ file is always out_path.with_suffix(".typ").
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    typ_path = out_path.with_suffix(".typ")
    typ_path.write_text(source, encoding="utf-8")

    with tempfile.NamedTemporaryFile(suffix=".typ", mode="w", encoding="utf-8", delete=False) as f:
        f.write(source)
        tmp = Path(f.name)

    try:
        result = typst.compile(str(tmp), format="png", font_paths=[str(_FONTS_DIR)])
    finally:
        tmp.unlink(missing_ok=True)

    pages: list[bytes] = result if isinstance(result, list) else [result]
    if len(pages) == 1:
        out_path.write_bytes(pages[0])
        return [out_path, typ_path]

    paths = []
    for i, page in enumerate(pages, start=1):
        p = out_path.with_name(f"{out_path.stem}-{i}.png")
        p.write_bytes(page)
        paths.append(p)
    return [*paths, typ_path]


# ── Public entry point ─────────────────────────────────────────────────────────

def render_all(config) -> list[Path]:
    """Read the output Google Sheet and render PNG participant lists.

    Renders the Fencers worksheet as fencers_list.png and each discipline worksheet
    as discipline_{code}.png. Files are saved to data/{tournament}/lists/.

    Raises ValueError if output_sheet_url is not configured or the sheet is inaccessible.
    """
    if not getattr(config, "output_sheet_url", None):
        raise ValueError("output_sheet_url is not set — configure it first with tool_set_output_sheet.")

    gc = gspread.service_account(filename=config.creds_path)
    try:
        sh = gc.open_by_url(config.output_sheet_url)
    except Exception as e:
        raise ValueError(f"Cannot open output sheet: {e}") from e

    lists_dir: Path = config.data_dir / _LISTS_SUBDIR
    lists_dir.mkdir(parents=True, exist_ok=True)

    tournament_name = config.tournament_name.replace("_", " ").title()
    discipline_limits: dict[str, int] = getattr(config, "discipline_limits", {})
    disciplines: dict[str, str] = getattr(config, "disciplines", {})

    fencers_template = (_TEMPLATES_DIR / "fencers.typ").read_text(encoding="utf-8")
    discipline_template = (_TEMPLATES_DIR / "disciplines.typ").read_text(encoding="utf-8")

    # Read Fencers worksheet
    try:
        fencers_ws = sh.worksheet("Fencers")
    except gspread.WorksheetNotFound:
        raise ValueError("'Fencers' worksheet not found in output sheet.")
    fencers_rows = _read_worksheet(fencers_ws)
    paid = _paid_map(fencers_rows)
    log.info("Read %d fencer rows from sheet", len(fencers_rows))

    outputs: list[Path] = []

    # Render overall fencer list
    source = _render_fencers_source(fencers_rows, fencers_template, tournament_name)
    outputs.extend(_compile_typst(source, lists_dir / "fencers_list.png"))
    log.info("Rendered fencers_list.png")

    # Render per-discipline lists
    for code, name in disciplines.items():
        try:
            ws = sh.worksheet(code)
        except gspread.WorksheetNotFound:
            log.warning("Worksheet '%s' not found — skipping discipline %s", code, code)
            continue
        disc_header, rows = _read_discipline_worksheet(ws)
        limit = discipline_limits.get(code)
        title = f"{tournament_name} — {name}"
        source = _render_discipline_source(disc_header, rows, paid, limit, discipline_template, title)
        outputs.extend(_compile_typst(source, lists_dir / f"discipline_{code}.png"))
        log.info("Rendered discipline_%s.png (%d rows, limit=%s)", code, len(rows), limit)

    log.info("Rendered %d PNG file(s) → %s", len(outputs), lists_dir)
    return outputs