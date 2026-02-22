"""Step 1: Download registration Google Sheet as a versioned local CSV."""

import csv
import logging
import re
from pathlib import Path

import gspread

from config import Config
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


def download_registrations(config: Config) -> Path:
    """Download worksheet 0 from the registration sheet as a versioned CSV.

    Returns the path to the newly downloaded file.
    """
    data_dir = config.data_dir / REG_VER_DIR
    data_dir.mkdir(parents=True, exist_ok=True)

    gc = gspread.service_account(filename=config.creds_path)
    sh = gc.open_by_url(config.registration_sheet_url)
    ws = sh.get_worksheet(0)
    logger.info("Opened registration sheet")

    new_path, _ = _next_version_path(data_dir)
    rows = ws.get_all_values()
    with new_path.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    logger.info(f"Downloaded {len(rows) - 1} rows → {new_path.name}")
    return new_path
