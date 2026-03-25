"""Step 4.5: Initialize the Fencers worksheet in the output sheet.

Runs immediately after dedup (step 4). Writes the header row with dynamically
detected optional columns, then batch-writes all fencer data rows.
"""

import logging

import gspread

from config import RegConfig
from models import FencerRecord

logger = logging.getLogger(__name__)

FENCERS_WORKSHEET = "Fencers"

# Fixed columns — always present, in this order.
_FIXED_HEADERS = ["Reg.", "Name", "Nat.", "Club", "HR_ID", "Disciplines", "Paid"]

# Optional columns — included only if ≥1 fencer has a non-None/non-empty value.
# Ordered as desired in the sheet.
_OPTIONAL_COLUMNS: list[tuple[str, str]] = [
    ("Afterparty",      "after_party"),
    ("Borrow weapons",  "borrow"),
    ("Aftersparring",   "aftersparring"),
    ("Accommodation",   "accommodation"),
]

# Notes is always last.
_NOTES_HEADER = "Notes"


def _detect_optional_columns(fencers: list[FencerRecord]) -> list[tuple[str, str]]:
    """Return the subset of _OPTIONAL_COLUMNS that have at least one non-empty value."""
    present = []
    for header, field_name in _OPTIONAL_COLUMNS:
        for f in fencers:
            val = getattr(f, field_name)
            if field_name == "borrow":
                if val:  # non-empty list
                    present.append((header, field_name))
                    break
            else:
                if val is not None:
                    present.append((header, field_name))
                    break
    return present


def _fencer_row(f: FencerRecord, optional_cols: list[tuple[str, str]]) -> list:
    """Build one data row for a fencer (skipping Reg. col — written manually)."""
    row: list = [
        f.name,
        f.nationality or "",
        f.club or "",
        f.hr_id if f.hr_id is not None else "",
        ",".join(d.str() for d in f.disciplines),
        "",  # Paid — never written by agent
    ]
    for _, field_name in optional_cols:
        val = getattr(f, field_name)
        if field_name == "borrow":
            row.append(",".join(str(w) for w in val) if val else "")
        else:
            row.append(val if val is not None else "")
    row.append(f.notes or "")
    return row


def init_fencers_sheet(fencers: list[FencerRecord], config: RegConfig) -> None:
    """Write the Fencers worksheet with a dynamic header and all fencer rows."""
    gc = gspread.service_account(filename=config.creds_path)
    sh = gc.open_by_url(config.output_sheet_url)
    ws = sh.worksheet(FENCERS_WORKSHEET)

    optional_cols = _detect_optional_columns(fencers)
    logger.info(
        "Optional columns detected: %s",
        [h for h, _ in optional_cols] if optional_cols else "(none)",
    )

    header = _FIXED_HEADERS + [h for h, _ in optional_cols] + [_NOTES_HEADER]
    ws.update([header], "A1")
    logger.info("Header written: %s", header)

    rows = [_fencer_row(f, optional_cols) for f in fencers]
    if rows:
        ws.update(rows, "B2")
    logger.info("Fencers worksheet initialized: %d rows, %d columns", len(rows), len(header))
