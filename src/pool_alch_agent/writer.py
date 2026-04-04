"""Write pool assignment to a new Google Sheet worksheet."""

import logging

import gspread
import gspread.utils

from pool_alch_agent.models import Assignment, PoolConfig, PoolFencer

log = logging.getLogger(__name__)

_POOL_TABLE_START_COL = 10   # column J (1-based)
_MIN_ROWS_PER_WAVE = 7
_WARN_POOL_SIZE = 7
_MAX_POOL_SIZE = 10

_THICK = {"style": "SOLID_MEDIUM"}
_BOLD = {"textFormat": {"bold": True}}


def _col_letter(n: int) -> str:
    """Convert 1-based column number to A1 letter (e.g. 10 → J)."""
    result = ""
    while n:
        n, r = divmod(n - 1, 26)
        result = chr(65 + r) + result
    return result


def _a1(row: int, col: int) -> str:
    return f"{_col_letter(col)}{row}"


def write_pools_sheet(
    config,
    discipline_code: str,
    fencers: list[PoolFencer],
    assignment: Assignment,
    pool_config: PoolConfig,
) -> tuple[str, list[str]]:
    """Create (or overwrite) a '{discipline}_Pools' worksheet in the output sheet.

    Returns (worksheet_url, warnings).
    Warnings are issued for pools exceeding _WARN_POOL_SIZE fencers.
    """
    gc = gspread.service_account(filename=config.creds_path)
    sh = gc.open_by_url(config.output_sheet_url)

    ws_title = f"{discipline_code}_Pools"
    try:
        ws = sh.worksheet(ws_title)
        ws.clear()
        log.info("Cleared existing worksheet '%s'", ws_title)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=ws_title, rows=200, cols=30)
        log.info("Created worksheet '%s'", ws_title)

    warnings: list[str] = []

    # ── Section 1: fencer list ────────────────────────────────────────────────

    fencer_header = ["Seed", "Name", "Club", "Nat.", "HR_ID", "HRating", "HRank"]
    sorted_fencers = sorted(fencers, key=lambda f: f.seed)

    fencer_rows: list[list] = [fencer_header]
    for f in sorted_fencers:
        fencer_rows.append([
            f.seed,
            f.name,
            f.club or "",
            f.nationality or "",
            f.hr_id if f.hr_id is not None else "",
            f.h_rating if f.h_rating is not None else "",
            f.h_rank if f.h_rank is not None else "",
        ])

    ws.update(fencer_rows, "A1")

    # Bold header, thick bottom border
    ws.format("A1:G1", {**_BOLD, "borders": {"bottom": _THICK}})
    # Thick right border on col G (separator edge)
    ws.format(f"G1:G{len(fencer_rows)}", {"borders": {"right": _THICK}})

    # ── Section 2: pool assignment table ─────────────────────────────────────

    current_row = 1  # 1-based, same row grid as fencer list

    for wave_idx, wave_size in enumerate(pool_config.wave_sizes):
        wave_start = pool_config.wave_start(wave_idx)
        wave_pools = assignment[wave_start : wave_start + wave_size]

        # Pool size checks
        for pool_offset, pool in enumerate(wave_pools):
            pool_no = wave_start + pool_offset + 1
            if len(pool) > _MAX_POOL_SIZE:
                warnings.append(
                    f"Pool {pool_no} has {len(pool)} fencers — exceeds hard maximum of {_MAX_POOL_SIZE}"
                )
            elif len(pool) > _WARN_POOL_SIZE:
                warnings.append(
                    f"Pool {pool_no} has {len(pool)} fencers (>{_WARN_POOL_SIZE}) — "
                    f"{len(pool) - 1} bouts per fencer"
                )

        max_fencers = max((len(p) for p in wave_pools), default=0)
        rows_in_wave = max(_MIN_ROWS_PER_WAVE, max_fencers)

        # Header row: "Pool 1", "Pool 2", ...
        header = [f"Pool {wave_start + i + 1}" for i in range(wave_size)]
        ws.update([header], _a1(current_row, _POOL_TABLE_START_COL))
        ws.format(
            f"{_a1(current_row, _POOL_TABLE_START_COL)}:"
            f"{_a1(current_row, _POOL_TABLE_START_COL + wave_size - 1)}",
            {**_BOLD, "borders": {"bottom": _THICK}},
        )
        current_row += 1

        # Data rows
        data: list[list] = []
        for row_i in range(rows_in_wave):
            row = []
            for pool in wave_pools:
                sorted_pool = sorted(pool, key=lambda f: f.seed)
                row.append(sorted_pool[row_i].name if row_i < len(sorted_pool) else "")
            data.append(row)

        ws.update(data, _a1(current_row, _POOL_TABLE_START_COL))
        current_row += rows_in_wave

        # Empty separator row between waves
        if wave_idx < len(pool_config.wave_sizes) - 1:
            current_row += 1

    sheet_id = sh.id
    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={ws.id}"
    log.info("Wrote pools sheet '%s': %d fencers, %d pools, %d waves",
             ws_title, len(fencers), pool_config.num_pools, pool_config.num_waves)
    return url, warnings
