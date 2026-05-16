"""Google Sheets I/O for the pool-results pipeline.

All public functions are blocking — call them via asyncio.to_thread().
"""

import json
import logging
import time
from pathlib import Path

from in_tournament.results_agent.models import BoutOutcome, PoolResult
from in_tournament.render_pools import _read_pools_from_sheet

log = logging.getLogger(__name__)

_HEADER = ["Pool", "Fencer1", "Fencer2", "Score1", "Score2", "Confidence", "Note"]


def _open_sheet(sheet_url: str, creds_path: str, retries: int = 3, backoff: float = 2.0):
    import gspread
    gc = gspread.service_account(filename=creds_path)
    for attempt in range(retries):
        try:
            return gc.open_by_url(sheet_url)
        except Exception as exc:
            if attempt == retries - 1:
                raise
            log.warning("Sheet open failed (attempt %d/%d): %s — retrying in %.0fs",
                        attempt + 1, retries, exc, backoff)
            time.sleep(backoff)
            backoff *= 2


def _ensure_ws(sh, name: str):
    """Return worksheet by name; create it with a bold/centred header row if missing."""
    import gspread
    try:
        return sh.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=name, rows=1000, cols=len(_HEADER))
        ws.append_row(_HEADER)
        end_col = chr(ord("A") + len(_HEADER) - 1)
        ws.format(f"A1:{end_col}1", {
            "textFormat": {"bold": True},
            "horizontalAlignment": "CENTER",
        })
        return ws


def ensure_worksheets(sh):
    """Ensure both worksheets exist in tab order: upload first, verified second."""
    upload = _ensure_ws(sh, "Pool upload")
    verified = _ensure_ws(sh, "Pool verified")
    return upload, verified


def write_pool_bouts(sheet_url: str, creds_path: str, result: PoolResult) -> None:
    """Append all bouts from *result* to the '{disc} Pools Upload' worksheet."""
    sh = _open_sheet(sheet_url, creds_path)
    ws, _ = ensure_worksheets(sh)
    rows = [
        [
            result.pool_no,
            b.fencer1,
            b.fencer2,
            b.score1,
            b.score2,
            result.confidence,
            b.note or "",
        ]
        for b in result.bouts
    ]
    if rows:
        ws.append_rows(rows)
    log.info("Wrote %d bouts for %s to Upload sheet", len(rows), result.pool_id)


def read_verified_bouts(sheet_url: str, creds_path: str) -> list[dict]:
    """Return all rows from '{disc} Pools Verified' as list-of-dicts."""
    sh = _open_sheet(sheet_url, creds_path)
    _, ws = ensure_worksheets(sh)
    return ws.get_all_records()


def get_pool_composition(sheet_url: str, creds_path: str, disc: str) -> dict[str, list[str]]:
    """Return {"DISC-N": [fencer_names, …]} from the 'Pool standings' worksheet."""
    raw = _read_pools_from_sheet(sheet_url, creds_path)
    return {f"{disc}-{pool_no}": names for pool_no, names in raw}


def load_published_pools(data_dir: Path) -> set[str]:
    """Return the persisted set of published pool-IDs (and ranking sentinels)."""
    p = data_dir / "published_pools.json"
    if not p.exists():
        return set()
    return set(json.loads(p.read_text()))


def save_published_pools(data_dir: Path, published: set[str]) -> None:
    """Persist *published* to data_dir/published_pools.json."""
    p = data_dir / "published_pools.json"
    p.write_text(json.dumps(sorted(published), indent=2))
