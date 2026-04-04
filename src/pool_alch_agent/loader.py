"""Load fencer data from Google Sheet discipline tabs."""

import logging
import sys
from pathlib import Path

import gspread

_SRC = Path(__file__).parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from pool_alch_agent.models import PoolFencer

log = logging.getLogger(__name__)

# Column indices in discipline worksheet (0-based), matching step6_upload._DISCIPLINE_HEADER
# ["No.", "Name", "Nat.", "Club", "HR_ID", "HRating", "HRank"] + "Seed" appended by recalculate_seeds
_COL_NAME    = 1
_COL_NAT     = 2
_COL_CLUB    = 3
_COL_HRID    = 4
_COL_HRATING = 5
_COL_HRANK   = 6


def _col_index(header: list[str], name: str) -> int | None:
    for i, h in enumerate(header):
        if h.strip() == name:
            return i
    return None


def _read_discipline_tab(ws: gspread.Worksheet) -> tuple[list[str], list[list[str]]]:
    """Return (header, data_rows) — data rows have non-blank Name."""
    all_rows = ws.get_all_values()
    if not all_rows:
        return [], []
    header = all_rows[0]
    rows = [row for row in all_rows[1:] if len(row) > _COL_NAME and row[_COL_NAME].strip()]
    return header, rows


def load_discipline(
    config,
    discipline_code: str,
) -> tuple[list[PoolFencer], list[str]]:
    """Load fencers for one discipline from the output Google Sheet.

    Also reads all other discipline tabs to detect dual-discipline fencers.

    Returns (fencers, warnings) where warnings are non-fatal notices (e.g. missing Seed
    for some fencers — those are included with seed=0 so validation catches them properly).
    """
    gc = gspread.service_account(filename=config.creds_path)
    sh = gc.open_by_url(config.output_sheet_url)

    # Load target discipline tab
    try:
        target_ws = sh.worksheet(discipline_code)
    except gspread.WorksheetNotFound:
        raise ValueError(f"Worksheet '{discipline_code}' not found in output sheet.")

    header, rows = _read_discipline_tab(target_ws)
    if not rows:
        raise ValueError(f"Worksheet '{discipline_code}' has no fencer rows.")

    seed_col = _col_index(header, "Seed")
    log.info("Loaded %d rows from '%s' (Seed col=%s)", len(rows), discipline_code, seed_col)

    # Build name → other_disciplines map from all other discipline tabs
    all_ws_titles = {ws.title for ws in sh.worksheets()}
    other_discipline_codes = [
        code for code in config.disciplines
        if code != discipline_code and code in all_ws_titles
    ]
    name_to_other: dict[str, list[str]] = {}
    for other_code in other_discipline_codes:
        try:
            other_ws = sh.worksheet(other_code)
            _, other_rows = _read_discipline_tab(other_ws)
            for row in other_rows:
                name = row[_COL_NAME].strip()
                if name:
                    name_to_other.setdefault(name, []).append(other_code)
        except gspread.WorksheetNotFound:
            log.warning("Other discipline tab '%s' not found, skipping", other_code)

    # Build PoolFencer list
    warnings: list[str] = []
    fencers: list[PoolFencer] = []

    for row in rows:
        def _col(idx: int) -> str:
            return row[idx].strip() if len(row) > idx else ""

        name = _col(_COL_NAME)

        # Seed
        seed_raw = _col(seed_col) if seed_col is not None else ""
        try:
            seed = int(seed_raw)
        except (ValueError, TypeError):
            seed = 0
            warnings.append(f"{name}: missing or non-numeric Seed ('{seed_raw}')")

        # HR_ID
        hr_id_raw = _col(_COL_HRID)
        try:
            hr_id = int(hr_id_raw) if hr_id_raw else None
        except ValueError:
            hr_id = None

        # HRating
        try:
            h_rating = float(_col(_COL_HRATING)) if _col(_COL_HRATING) else None
        except ValueError:
            h_rating = None

        # HRank
        try:
            h_rank = int(_col(_COL_HRANK)) if _col(_COL_HRANK) else None
        except ValueError:
            h_rank = None

        fencers.append(PoolFencer(
            name=name,
            seed=seed,
            nationality=_col(_COL_NAT) or None,
            club=_col(_COL_CLUB) or None,
            hr_id=hr_id,
            other_disciplines=name_to_other.get(name, []),
            h_rating=h_rating,
            h_rank=h_rank,
        ))

    dual = sum(1 for f in fencers if f.other_disciplines)
    log.info(
        "Built %d PoolFencers for '%s', %d dual-discipline",
        len(fencers), discipline_code, dual,
    )
    return fencers, warnings