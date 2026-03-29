import logging
from pathlib import Path

# ── Shared data file / directory name constants ───────────────────────────────
REG_VER_DIR = "registration_csv"
REG_VER_FILE_PREF = "registrations_v"
REG_VER_FILE_PTN = f"{REG_VER_FILE_PREF}*.csv"
REG_VER_FILE_REG = rf"{REG_VER_FILE_PREF}(\d+)\.csv"
REG_VER_FILE_FMT = REG_VER_FILE_PREF+"{}.csv"   # format with version int

FENCERS_PARSED_FILE = "fencers_parsed.json"
FENCERS_MATCHED_FILE = "fencers_matched.json"
FENCERS_CACHE_FILE = "fencers_cache.json"
FENCERS_DEDUPED_FILE = "fencers_deduped.json"
FENCERS_DEDUPED_FP_FILE = "fencers_deduped.fingerprint"

_NATIONALITY_CODES: dict[str, str] = {
    "Czech Republic": "CZ", "Czechia": "CZ",
    "Slovakia": "SK",
    "Germany": "DE",
    "Poland": "PL",
    "Hungary": "HU",
    "Austria": "AT",
    "United Kingdom": "GB", "Great Britain": "GB", "England": "GB",
    "United States": "US", "United States of America": "US",
    "France": "FR",
    "Netherlands": "NL",
    "Belgium": "BE",
    "Italy": "IT",
    "Sweden": "SE",
    "Finland": "FI",
    "Norway": "NO",
    "Denmark": "DK",
    "Spain": "ES",
    "Portugal": "PT",
    "Russia": "RU",
    "Ukraine": "UA",
    "Estonia": "EE",
    "Latvia": "LV",
    "Lithuania": "LT",
    "Romania": "RO",
    "Bulgaria": "BG",
    "Croatia": "HR",
    "Slovenia": "SI",
    "Serbia": "RS",
    "Switzerland": "CH",
    "Ireland": "IE",
    "Canada": "CA",
    "Australia": "AU",
    "Brazil": "BR",
    "Japan": "JP",
    "Israel": "IL",
    "Belarus": "BY",
}


def to_nat_code(nat: str | None) -> str | None:
    """Convert a full country name to a 2-letter ISO code; pass through if already a code or unknown.
    Returns None for empty values and HTML entities like &nbsp; that indicate missing nationality.
    """
    if not nat or nat.strip() in ("", "&nbsp;"):
        return None
    return _NATIONALITY_CODES.get(nat.strip(), nat.strip())

import unicodedata
from difflib import SequenceMatcher

from pydantic import BaseModel, RootModel

from models import FencerRecord, FencerRating

logger = logging.getLogger(__name__)

class FencersList(RootModel):
    root: list[FencerRecord]

def load_fencers_list(data_dir: Path, filename: str) -> list[FencerRecord] | None:
    path = data_dir / filename
    if not path.exists():
        return None
    fencers = FencersList.model_validate_json(path.read_text()).root
    logger.info(f"Loaded {len(fencers)} fencers from {filename}")
    return fencers


def save_fencers_list(fencers: list[FencerRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(FencersList(root=fencers).model_dump_json(indent=4))
    logger.info(f"Saved {len(fencers)} fencers → {path.name}")


RatingsDict = dict[int, dict[str, FencerRating]]


class RatingsCache(RootModel):
    root: RatingsDict


def load_ratings(data_dir: Path, filename: str) -> RatingsDict | None:
    path = data_dir / filename
    if not path.exists():
        return None
    ratings = RatingsCache.model_validate_json(path.read_text()).root
    logger.info(f"Loaded ratings from {filename}")
    return ratings


def save_ratings(ratings: RatingsDict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(RatingsCache(root=ratings).model_dump_json(indent=4))
    logger.info(f"Saved ratings → {path.name}")


# ── Withdrawn fencers ─────────────────────────────────────────────────────────

WITHDRAWN_FILE = "withdrawn.json"


class WithdrawnEntry(BaseModel):
    name: str
    hr_id: int | None = None


class WithdrawnList(RootModel):
    root: list[WithdrawnEntry] = []


def load_withdrawn(data_dir: Path) -> list[WithdrawnEntry]:
    path = data_dir / WITHDRAWN_FILE
    if not path.exists():
        return []
    return WithdrawnList.model_validate_json(path.read_text()).root


def save_withdrawn(entries: list[WithdrawnEntry], data_dir: Path) -> None:
    path = data_dir / WITHDRAWN_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(WithdrawnList(root=entries).model_dump_json(indent=2))
    logger.info("Saved %d withdrawn fencer(s) → %s", len(entries), path.name)


# ── Fuzzy name matching ───────────────────────────────────────────────────────

def normalize_name(s: str) -> str:
    """Lowercase and strip diacritics for comparison (e.g. 'Böhm' → 'bohm')."""
    return unicodedata.normalize("NFD", s.lower()).encode("ascii", "ignore").decode()


def fuzzy_match_fencers(query: str, fencers: list[FencerRecord], threshold: float = 0.6) -> list[FencerRecord]:
    """Return fencers whose names closely match the query string.

    Comparison is diacritic-insensitive, so 'Bohm' matches 'Böhm' and
    a surname-only query like 'Medvid' matches 'Miroslav Medvid'.
    """
    q = normalize_name(query)
    results = []
    for f in fencers:
        n = normalize_name(f.name)
        if q in n or n in q:
            results.append(f)
        elif SequenceMatcher(None, q, n).ratio() >= threshold:
            results.append(f)
    return results