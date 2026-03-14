"""Step 1: Download registration Google Sheet as a versioned local CSV."""

import csv
import logging
import re
from pathlib import Path

import gspread
from config.tracing import observe

from config import RegConfig
from utils import REG_VER_DIR, REG_VER_FILE_PTN, REG_VER_FILE_REG, REG_VER_FILE_FMT

logger = logging.getLogger(__name__)


def _next_version_path(data_dir: Path) -> tuple[Path, Path | None]:
    """Return (new_path, previous_path). new_path has the next available version number."""
    existing = sorted(data_dir.glob(REG_VER_FILE_PTN))
    if not existing:
        return data_dir / REG_VER_FILE_FMT.format(0), None

    # Parse highest version number
    def _ver(p: Path) -> int:
        m = re.search(REG_VER_FILE_REG, p.name)
        return int(m.group(1)) if m else -1

    latest = max(existing, key=_ver)
    return data_dir / REG_VER_FILE_FMT.format(_ver(latest) + 1), latest


def save_registration_csv(config: RegConfig, data: bytes) -> Path:
    """Save raw CSV bytes as the next versioned registration file.

    Used when the organiser uploads a CSV directly to Discord instead of sharing a Google Sheet.
    Returns the path to the saved file.
    """
    data_dir = config.data_dir / REG_VER_DIR
    data_dir.mkdir(parents=True, exist_ok=True)
    new_path, _ = _next_version_path(data_dir)
    new_path.write_bytes(data)
    logger.info("Saved uploaded CSV → %s", new_path.name)
    return new_path


@observe
def download_registrations(
    config: RegConfig,
    sheet_url: str,
    worksheet_index: int = 0,
    worksheet_name: str | None = None,
) -> Path:
    """Download a worksheet from the registration sheet as a versioned CSV.

    worksheet_index: 0-based index of the worksheet (default 0).
    worksheet_name: name of the worksheet; if provided, takes precedence.
    Raises ValueError if both worksheet_index (non-default) and worksheet_name are given.
    Returns the path to the newly downloaded file.
    """
    if worksheet_name is not None and worksheet_index != 0:
        raise ValueError("Specify either worksheet_index or worksheet_name, not both.")

    data_dir = config.data_dir / REG_VER_DIR
    data_dir.mkdir(parents=True, exist_ok=True)

    gc = gspread.service_account(filename=config.creds_path)
    sh = gc.open_by_url(sheet_url)
    ws = sh.worksheet(worksheet_name) if worksheet_name is not None else sh.get_worksheet(worksheet_index)
    logger.info("Opened registration sheet")

    new_path, _ = _next_version_path(data_dir)
    rows = ws.get_all_values()
    with new_path.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    logger.info(f"Downloaded {len(rows) - 1} rows → {new_path.name}")
    return new_path
