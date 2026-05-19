"""Locate / validate / clear the per-step JSON & CSV artifacts.

Never hardcodes filenames — imports the constants from reg_agent.utils and
step7_payments so the CLI stays in lockstep with the bot.
"""

import re
from pathlib import Path

import pre_tournament.cli.context  # noqa: F401  (runs the sys.path shim)
from utils import (  # noqa: E402
    FENCERS_CACHE_FILE,
    FENCERS_DEDUPED_FILE,
    FENCERS_DEDUPED_FP_FILE,
    FENCERS_MATCHED_FILE,
    FENCERS_PARSED_FILE,
    REG_VER_DIR,
    REG_VER_FILE_PTN,
    REG_VER_FILE_REG,
)
from step3_match import MATCH_CORRECTIONS_FILE  # noqa: E402
from step4_dedup import FENCERS_LIKELY_GROUPS_PENDING_FILE  # noqa: E402
from step7_payments import (  # noqa: E402
    PAYMENTS_MATCHED_FILE,
    PAYMENTS_PARSED_DIR,
    PAYMENTS_RAW_DIR,
)

from pre_tournament.cli.errors import ArtifactMissing


def parsed(data_dir: Path) -> Path:
    return data_dir / FENCERS_PARSED_FILE


def matched(data_dir: Path) -> Path:
    return data_dir / FENCERS_MATCHED_FILE


def deduped(data_dir: Path) -> Path:
    return data_dir / FENCERS_DEDUPED_FILE


def deduped_fp(data_dir: Path) -> Path:
    return data_dir / FENCERS_DEDUPED_FP_FILE


def cache(data_dir: Path) -> Path:
    return data_dir / FENCERS_CACHE_FILE


def corrections(data_dir: Path) -> Path:
    return data_dir / MATCH_CORRECTIONS_FILE


def likely_pending(data_dir: Path) -> Path:
    return data_dir / FENCERS_LIKELY_GROUPS_PENDING_FILE


def payments_matched(data_dir: Path) -> Path:
    return data_dir / PAYMENTS_MATCHED_FILE


def payments_raw_dir(data_dir: Path) -> Path:
    return data_dir / PAYMENTS_RAW_DIR


def payments_parsed_dir(data_dir: Path) -> Path:
    return data_dir / PAYMENTS_PARSED_DIR


def _ver(p: Path) -> int:
    m = re.search(REG_VER_FILE_REG, p.name)
    return int(m.group(1)) if m else -1


def latest_registration_csv(data_dir: Path) -> Path | None:
    d = data_dir / REG_VER_DIR
    if not d.exists():
        return None
    csvs = sorted(d.glob(REG_VER_FILE_PTN))
    return max(csvs, key=_ver) if csvs else None


def latest_ratings(data_dir: Path) -> Path | None:
    files = sorted(data_dir.glob("ratings_*.json"))
    return files[-1] if files else None


def require(path: Path | None, what: str, hint: str = "") -> Path:
    """Return path or raise ArtifactMissing (exit 2)."""
    if path is None or not Path(path).exists():
        msg = f"missing required artifact: {what}"
        if hint:
            msg += f" — {hint}"
        raise ArtifactMissing(msg)
    return Path(path)


def clear(path: Path) -> bool:
    """Delete an artifact if present (for --force). Returns True if removed."""
    p = Path(path)
    if p.exists():
        p.unlink()
        return True
    return False
