"""Read pool assignments back from the Google Sheet (source of truth after user edits)."""

import logging
import re
from dataclasses import dataclass, field

import gspread

from pool_alch_agent.models import Assignment, PoolConfig, PoolFencer

log = logging.getLogger(__name__)

# Must match writer.py layout constants.
_POOL_TABLE_START_COL = 9  # 0-based index = column J
_POOL_HEADER_RE = re.compile(r"^Pool\s+(\d+)$")  # capital P — skips diagnostic tables ("pool N")


@dataclass
class SheetPoolData:
    """Result of reading pools back from the sheet."""
    assignment: Assignment
    pool_numbers: list[int]          # pool number per pool (1-based, from headers)
    warnings: list[str] = field(default_factory=list)


def _read_fencer_list(rows: list[list[str]]) -> dict[str, PoolFencer]:
    """Read fencer list from cols A-G (header in row 0), return name→PoolFencer lookup."""
    lookup: dict[str, PoolFencer] = {}
    for row in rows[1:]:  # skip header
        if len(row) < 2 or not row[1].strip():
            continue
        name = row[1].strip()

        def _int(val: str) -> int | None:
            try:
                return int(val)
            except (ValueError, TypeError):
                return None

        def _float(val: str) -> float | None:
            try:
                return float(val)
            except (ValueError, TypeError):
                return None

        seed = _int(row[0]) or 0
        club = row[2].strip() if len(row) > 2 and row[2].strip() else None
        nat = row[3].strip() if len(row) > 3 and row[3].strip() else None
        hr_id = _int(row[4]) if len(row) > 4 else None
        h_rating = _float(row[5]) if len(row) > 5 else None
        h_rank = _int(row[6]) if len(row) > 6 else None

        lookup[name] = PoolFencer(
            name=name,
            seed=seed,
            nationality=nat,
            club=club,
            hr_id=hr_id,
            other_disciplines=[],
            h_rating=h_rating,
            h_rank=h_rank,
        )
    return lookup


def _find_pool_columns(header_row: list[str], start_col: int) -> list[tuple[int, int]]:
    """Find (column_index, pool_number) pairs for Pool headers starting at start_col.

    Stops at the first empty column after finding at least one pool header
    (the gap before the seeds table).
    """
    pools: list[tuple[int, int]] = []
    found_any = False
    for col_idx in range(start_col, len(header_row)):
        cell = header_row[col_idx].strip()
        m = _POOL_HEADER_RE.match(cell)
        if m:
            pools.append((col_idx, int(m.group(1))))
            found_any = True
        elif found_any and not cell:
            # Empty column after pool headers → gap before seeds table
            break
    return pools


def _read_pool_tables(all_values: list[list[str]]) -> list[tuple[int, list[str]]]:
    """Read pool name tables from the sheet.

    Returns list of (pool_number, [fencer_names]) tuples.
    Scans all rows for "Pool N" headers in columns starting at J,
    handling multiple waves separated by empty rows.
    """
    pools: list[tuple[int, list[str]]] = []

    row_idx = 0
    while row_idx < len(all_values):
        row = all_values[row_idx]
        # Check if this row contains pool headers
        pool_cols = _find_pool_columns(row, _POOL_TABLE_START_COL)
        if not pool_cols:
            row_idx += 1
            continue

        # Found a header row — read names below for each pool column
        wave_pools: dict[int, list[str]] = {pn: [] for _, pn in pool_cols}
        col_map = {col: pn for col, pn in pool_cols}

        data_row = row_idx + 1
        while data_row < len(all_values):
            data = all_values[data_row]
            # Check if ANY of the pool columns has a non-empty value
            has_data = False
            for col, pn in pool_cols:
                val = data[col].strip() if col < len(data) else ""
                if val:
                    has_data = True
                    break
            if not has_data:
                break  # empty row → end of this wave's data
            # Also check if this row is itself a new pool header
            if _find_pool_columns(data, _POOL_TABLE_START_COL):
                break

            for col, pn in pool_cols:
                val = data[col].strip() if col < len(data) else ""
                if val:
                    wave_pools[pn].append(val)
            data_row += 1

        for _, pn in pool_cols:
            pools.append((pn, wave_pools[pn]))

        row_idx = data_row + 1  # skip past data + separator

    # Sort by pool number
    pools.sort(key=lambda x: x[0])
    return pools


def read_pools_from_sheet(config, discipline_code: str) -> SheetPoolData:
    """Read pool assignments from the {discipline}_Pools worksheet.

    Returns SheetPoolData with the reconstructed assignment and validation warnings.
    """
    gc = gspread.service_account(filename=config.creds_path)
    sh = gc.open_by_url(config.output_sheet_url)
    ws_title = f"{discipline_code}_Pools"
    ws = sh.worksheet(ws_title)

    all_values = ws.get_all_values()
    if not all_values:
        return SheetPoolData(assignment=[], pool_numbers=[], warnings=["Sheet is empty."])

    # Build fencer lookup from cols A-G
    fencer_lookup = _read_fencer_list(all_values)
    log.info("Read %d fencers from fencer list in '%s'", len(fencer_lookup), ws_title)

    # Read pool tables
    raw_pools = _read_pool_tables(all_values)
    if not raw_pools:
        return SheetPoolData(
            assignment=[], pool_numbers=[],
            warnings=["No pool tables found in the sheet."],
        )

    # Build a case-insensitive lookup for fuzzy matching
    ci_lookup: dict[str, PoolFencer] = {
        name.lower().strip(): fencer for name, fencer in fencer_lookup.items()
    }

    # Match names to fencers
    warnings: list[str] = []
    assignment: Assignment = []
    pool_numbers: list[int] = []
    matched_names: set[str] = set()

    for pool_no, names in raw_pools:
        pool: list[PoolFencer] = []
        for name in names:
            fencer = fencer_lookup.get(name)
            if fencer is None:
                # Try case-insensitive
                fencer = ci_lookup.get(name.lower().strip())
            if fencer is None:
                warnings.append(f"'{name}' in Pool {pool_no} not found in fencer list")
                # Create a minimal placeholder
                fencer = PoolFencer(
                    name=name, seed=0, nationality=None, club=None,
                    hr_id=None, other_disciplines=[],
                )
            else:
                matched_names.add(fencer.name)
            pool.append(fencer)
        assignment.append(pool)
        pool_numbers.append(pool_no)

    # Check for fencers in list but not in any pool
    for name, fencer in fencer_lookup.items():
        if name not in matched_names:
            warnings.append(
                f"'{name}' (seed {fencer.seed}) is in the fencer list but not in any pool — withdrawn?"
            )

    total = sum(len(p) for p in assignment)
    log.info("Read %d pools (%d fencers) from '%s', %d warnings",
             len(assignment), total, ws_title, len(warnings))

    return SheetPoolData(
        assignment=assignment,
        pool_numbers=pool_numbers,
        warnings=warnings,
    )
