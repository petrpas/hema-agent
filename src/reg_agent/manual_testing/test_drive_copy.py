"""Manual test: verify the service account can create a blank sheet in the Drive folder.

Run from repo root:
    python src/reg_agent/manual_testing/test_drive_copy.py

Uses the Drive API directly (not gspread) to place the file in the target folder in a
single API call.  gspread's create/copy internally create the file first then move it,
briefly touching the service account's own storage and triggering a quota error.

Checks:
  - Drive API files.create succeeds (SA has write access to the folder)
  - The new sheet lands in the configured Drive folder (folder_id matches)
  - Cleanup: the sheet is deleted immediately after the check
"""

import re
import sys
from pathlib import Path

# ── Path setup ─────────────────────────────────────────────────────────────────
_FILE = Path(__file__).resolve()
_SRC = _FILE.parents[2]         # hema-agent/src/
_REG = _SRC / "reg_agent"
for _p in (_SRC, _REG):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build as _build

from config import load_agent_config

# ── Config ─────────────────────────────────────────────────────────────────────
_REPO_ROOT = _FILE.parents[3]
_AGENT_CONFIG_PATH = _REPO_ROOT / "src" / "config" / "agent_config.json"

_SCOPES = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]


def _load() -> tuple[str, str]:
    """Return (folder_id, creds_path) from agent_config.json."""
    cfg = load_agent_config(_AGENT_CONFIG_PATH).reg_agent

    assert cfg.drive_folder_url, "drive_folder_url is not set in agent_config.json"
    fm = re.search(r"/folders/([a-zA-Z0-9_-]+)", cfg.drive_folder_url)
    assert fm, f"Cannot extract folder ID from drive_folder_url: {cfg.drive_folder_url!r}"

    return fm.group(1), str(_REPO_ROOT / cfg.creds_path)


# ── Test ───────────────────────────────────────────────────────────────────────
def test_drive_create() -> None:
    folder_id, creds_path = _load()
    print(f"folder_id  : {folder_id}")
    print(f"creds_path : {creds_path}")

    creds = Credentials.from_service_account_file(creds_path, scopes=_SCOPES)
    drive = _build("drive", "v3", credentials=creds)

    print("\nCreating blank sheet via Drive API …")
    f = drive.files().create(
        body={
            "name": "_test_drive_create_DELETE_ME",
            "mimeType": "application/vnd.google-apps.spreadsheet",
            "parents": [folder_id],
        },
        fields="id,parents",
        supportsAllDrives=True,
    ).execute()
    sheet_id = f["id"]
    print(f"Sheet created: id={sheet_id}")

    try:
        parents = f.get("parents", [])
        assert folder_id in parents, (
            f"Sheet is not in the expected folder.\n"
            f"  expected folder_id : {folder_id}\n"
            f"  actual parents     : {parents}"
        )
        print(f"✅ Sheet is in the correct Drive folder ({folder_id})")

    finally:
        print(f"\nDeleting sheet {sheet_id} …")
        drive.files().delete(fileId=sheet_id, supportsAllDrives=True).execute()
        print("✅ Sheet deleted")

    print("\n✅✅✅ ALL ASSERTIONS PASSED ✅✅✅\n")


if __name__ == "__main__":
    test_drive_create()