"""Step 3: Match fencers without hr_id to HEMA Ratings profiles using LLM fuzzy matching."""

import difflib
import html as html_mod
import logging
import re
import unicodedata
from pathlib import Path

import requests
from config.tracing import observe
from pydantic import BaseModel, TypeAdapter
from pydantic_ai import Agent, ModelSettings

logger = logging.getLogger(__name__)

from config import RegConfig, Step
from models import FencerRecord
from utils import load_fencers_list, save_fencers_list, to_nat_code, FENCERS_MATCHED_FILE, FENCERS_CACHE_FILE

FIGHTERS_URL = "https://hemaratings.com/fighters/"
FIGHTERS_CACHE_FILENAME = "hemaratings_fighters.html"
FIGHTERS_PARSED_FILENAME = "hemaratings_fighters.csv"
MATCH_CORRECTIONS_FILE = "match_corrections.json"

SYSTEM_PROMPT = """You are a data assistant for HEMA (Historical European Martial Arts) tournaments.
You will receive:
1. A list of registered unmatched fencers (email, name, club) that need their HEMA Ratings ID found.
2. A pre-filtered list of the most likely candidate fighters from hemaratings.com: id;name;nationality;club (one per line).
   Note: this is NOT the complete HR database — only candidates selected by a pre-filter. If no good match appears,
   the person may genuinely not be on HEMA Ratings, or the pre-filter may have missed them; set hr_id to null.

Your task: For each unmatched fencer, fuzzy-match them against the candidate fighters list using:
- Name similarity (handle transliterations, nicknames, diacritics: "Honza" ↔ "Jan", "Blažek" ↔ "Blazek")
- Club name as a secondary signal
- Nationality as a tertiary signal

Only set hr_id if you are confident (>80%) it is the same person. If no confident match exists, set hr_id to null.

Output fields per fencer:
- email: echo back unchanged — used to key results back to the registration record
- hr_id: matched HR id, or null if no confident match
- matched_name: the canonical name from the HR fighters list (used for caching), or null if unmatched
- matched_club: the resolved club name (see rules below), or null if unmatched
- nationality: resolved nationality (see rule below)

Club resolution rules (populate matched_club):
- If registration club is blank, use the club from HR.
- If registration club looks like an abbreviation or alternate spelling of the HR club, use the HR club name.
- If registration club and HR club are clearly different organizations, keep the registration club name.

Examples:
 - HR: Academy of Knight's Arts; Registration: AKA; -> Academy of Knight's Arts  (abbreviation → use HR name)
 - HR: Academy of Knight's Arts; Registration: Duelanti od sv. Rocha; -> Duelanti od sv. Rocha  (different club → keep registration)

Nationality: if provided in the registration, keep it; otherwise take it from HR.
"""

class FencerMatch(BaseModel):
    email: str
    hr_id: int | None
    matched_name: str | None
    matched_club: str | None
    nationality: str | None


class MatchResult(BaseModel):
    matches: list[FencerMatch]


class CacheEntry(BaseModel):
    full_name: str
    club: str
    nationality: str
    hr_id: int
    emails_used: list[str] = []
    alternative_names_used: list[str] = []


_CACHE = TypeAdapter(dict[str, CacheEntry])


def _parse_fighters_html(html: str) -> list[tuple[int, str, str, str]]:
    """Extract (hr_id, name, nationality, club) from the fighters page HTML."""
    # Each row: <td><a href="/fighters/details/ID/">Name</a></td>
    #           <td data-search="Country">...</td>
    #           <td>[optional: <a href="/clubs/.../">Club</a>]</td>
    # Parse row-by-row to avoid the club from the next row bleeding into a
    # fighter whose club cell is empty.
    fighter_re = re.compile(r'href="/fighters/details/(\d+)/">([^<]+)</a>', re.DOTALL)
    nat_re     = re.compile(r'data-search="([^"]+)"')
    club_re    = re.compile(r'href="/clubs/[^"]+/">([^<]+)</a>')

    fighters = []
    for row in re.split(r'(?=<tr[\s>])', html):
        fm = fighter_re.search(row)
        if not fm:
            continue
        nm = nat_re.search(row)
        cm = club_re.search(row)
        hr_id       = int(fm.group(1))
        name        = fm.group(2).strip()
        nationality = nm.group(1).strip() if nm else ""
        club        = cm.group(1).strip() if cm else ""
        fighters.append((hr_id, name, nationality, club))
    return fighters


def _get_fighters_compact(data_dir: Path) -> str:
    """Return a compact line-per-fighter text, parsing and caching from HTML if needed."""
    csv_path = data_dir / FIGHTERS_PARSED_FILENAME
    if csv_path.exists():
        return csv_path.read_text(encoding="utf-8")

    html_path = data_dir / FIGHTERS_CACHE_FILENAME
    if not html_path.exists():
        logger.info(f"Downloading {FIGHTERS_URL} ...")
        resp = requests.get(FIGHTERS_URL, timeout=30)
        resp.raise_for_status()
        html_path.write_text(resp.text, encoding="utf-8")
        logger.info("Downloaded fighters page")

    logger.info("Parsing fighters list ...")
    fighters = _parse_fighters_html(html_path.read_text(encoding="utf-8"))
    lines = [f"{hr_id};{html_mod.unescape(name).strip()};{html_mod.unescape(nat).strip()};{html_mod.unescape(club).strip()}"
             for hr_id, name, nat, club in fighters]
    text = "\n".join(lines)
    csv_path.write_text(text, encoding="utf-8")
    logger.info(f"Parsed {len(fighters)} fighters")
    return text


def _build_hr_index(fighters_text: str) -> dict[int, tuple[str, str, str]]:
    """Return {hr_id: (name, nationality, club)} from the compact fighters text."""
    index: dict[int, tuple[str, str, str]] = {}
    for line in fighters_text.splitlines():
        parts = line.split(";", 3)
        if len(parts) == 4:
            try:
                index[int(parts[0])] = (parts[1], to_nat_code(parts[2]) or "", parts[3])
            except ValueError:
                pass
    return index


def _normalize(s: str) -> str:
    """Lowercase + strip diacritics for fuzzy comparison."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s.lower())
        if unicodedata.category(c) != "Mn"
    )


def _prefilter_candidates(
    need_llm: list[FencerRecord],
    fighters_text: str,
    top_n: int = 30,
) -> str:
    """Return a fighters_text containing only the top_n candidates per unmatched fencer."""
    all_lines = fighters_text.splitlines()
    # Build (normalized_name, line) index
    index = []
    for line in all_lines:
        parts = line.split(";", 3)
        if len(parts) >= 2:
            index.append((_normalize(parts[1]), line))

    all_names_norm = [item[0] for item in index]
    seen: set[str] = set()
    candidate_lines: list[str] = []

    for fencer in need_llm:
        query = _normalize(fencer.name)
        # Also try individual tokens for partial matches
        tokens = query.split()
        close = difflib.get_close_matches(query, all_names_norm, n=top_n, cutoff=0.3)
        # Also add any line containing the last name token
        if tokens:
            surname_norm = tokens[-1]
            for norm, _line in index:
                if surname_norm in norm and norm not in close:
                    close.append(norm)
        for norm_name in close[:top_n]:
            # Find the original line
            for n, line in index:
                if n == norm_name and line not in seen:
                    seen.add(line)
                    candidate_lines.append(line)
                    break

    return "\n".join(candidate_lines)


def _call_llm(
    need_llm: list[FencerRecord],
    fighters_text: str,
    config: RegConfig,
    instructions: str | None = None,
) -> dict[str, dict]:
    """Return match info keyed by email."""
    candidates_text = _prefilter_candidates(need_llm, fighters_text)
    logger.info(f"Sending {len(candidates_text.splitlines())} candidate fighters to LLM for {len(need_llm)} fencers ...")

    unmatched_text = "\n".join(
        f"- email={f.email}, name={f.name}, club={f.club or ''}"
        for f in need_llm
    )
    agent = Agent(
        model=config.model(Step.MATCH),
        model_settings=ModelSettings(temperature=0.0),
        output_type=MatchResult,
        system_prompt=SYSTEM_PROMPT,
        retries=3,
    )
    user_prompt = (
        f"Unmatched fencers:\n{unmatched_text}\n\n"
        f"Candidate fighters (id;name;nationality;club):\n{candidates_text}"
    )
    if instructions:
        user_prompt += f"\n\nAdditional organiser instructions:\n{instructions}"
    result = agent.run_sync(user_prompt)
    logger.info("LLM matching complete")
    return {m.email: m.model_dump() for m in result.output.matches}


def load_corrections(data_dir: Path) -> dict[str, int | None]:
    import json as _json
    path = data_dir / MATCH_CORRECTIONS_FILE
    if not path.exists():
        return {}
    return _json.loads(path.read_text(encoding="utf-8"))


def save_corrections(corrections: dict[str, int | None], data_dir: Path) -> None:
    import json as _json
    path = data_dir / MATCH_CORRECTIONS_FILE
    path.write_text(_json.dumps(corrections, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_cache(cache_path: Path) -> dict[str, CacheEntry]:
    if not cache_path.exists():
        return {}
    return _CACHE.validate_json(cache_path.read_bytes())


def _save_cache(cache: dict[str, CacheEntry], cache_path: Path) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(_CACHE.dump_json(cache, indent=2))


def _cache_lookup_by_email_and_name(cache: dict[str, CacheEntry], email: str, name: str) -> CacheEntry | None:
    """Match only when both email and name agree — guards against one email registering multiple fencers."""
    if not email or not name:
        return None
    email_lower = email.lower()
    name_norm = _normalize(name)
    for entry in cache.values():
        if email_lower not in {e.lower() for e in entry.emails_used}:
            continue
        known_names = {entry.full_name} | set(entry.alternative_names_used)
        if any(_normalize(n) == name_norm for n in known_names):
            return entry
    return None


def _cache_lookup_by_name(cache: dict[str, CacheEntry], full_name: str) -> CacheEntry | None:
    name_lower = full_name.strip().lower()
    for entry in cache.values():
        if entry.full_name.strip().lower() == name_lower:
            return entry
        if name_lower in {n.strip().lower() for n in entry.alternative_names_used}:
            return entry
    return None


def _email_owned_by_other(cache: dict[str, CacheEntry], email: str, exclude_key: str) -> bool:
    """Return True if email already appears in a different cache entry (proxy registration guard)."""
    email_lower = email.lower()
    return any(
        k != exclude_key and email_lower in {e.lower() for e in v.emails_used}
        for k, v in cache.items()
    )


def _upsert_cache_entry(
    cache: dict[str, CacheEntry],
    hr_id: int,
    full_name: str,
    club: str,
    email: str,
    nationality: str | None,
    alt_name: str | None,
) -> None:
    key = str(hr_id)
    if key not in cache:
        owned_elsewhere = email and _email_owned_by_other(cache, email, key)
        if owned_elsewhere:
            logger.warning(f"Email {email} already owned by another entry — not adding to hr_id={hr_id} ({full_name})")
        cache[key] = CacheEntry(
            full_name=full_name,
            club=club,
            nationality=to_nat_code(nationality) or "",
            hr_id=hr_id,
            emails_used=[email] if email and not owned_elsewhere else [],
            alternative_names_used=[alt_name] if alt_name else [],
        )
    else:
        entry = cache[key]
        if email and email not in entry.emails_used:
            if _email_owned_by_other(cache, email, key):
                logger.warning(f"Email {email} already owned by another entry — not adding to hr_id={hr_id} ({full_name})")
            else:
                entry.emails_used.append(email)
        if alt_name and alt_name not in entry.alternative_names_used:
            entry.alternative_names_used.append(alt_name)


@observe(capture_input=False, capture_output=False)
def match_fencers(
    fencers: list[FencerRecord],
    config: RegConfig,
    instructions: str | None = None,
) -> list[FencerRecord]:
    """Enrich fencers with hr_id via cache lookup and LLM fuzzy matching."""
    data_dir = config.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    cache_path = data_dir / FENCERS_CACHE_FILE
    cache = _load_cache(cache_path)

    fighters_text = _get_fighters_compact(data_dir)
    hr_index = _build_hr_index(fighters_text)

    need_llm: list[FencerRecord] = []
    updated_fencers: list[FencerRecord] = []

    for fencer in fencers:
        if fencer.hr_id is not None:
            hr_name, hr_nat, hr_club = hr_index.get(fencer.hr_id, (None, None, None))
            # Validate self-reported hr_id against the HR profile name.
            # If names share no tokens and similarity is very low, the id is likely fake/wrong.
            if hr_name and _normalize(fencer.name) != _normalize(hr_name):
                ratio = difflib.SequenceMatcher(None, _normalize(fencer.name), _normalize(hr_name)).ratio()
                fencer_tokens = set(_normalize(fencer.name).split())
                hr_tokens = set(_normalize(hr_name).split())
                if ratio < 0.4 and not fencer_tokens & hr_tokens:
                    logger.warning(
                        f"Rejecting self-reported hr_id={fencer.hr_id} for '{fencer.name}': "
                        f"HR profile is '{hr_name}' (ratio={ratio:.2f}) — routing to LLM"
                    )
                    problems_note = (
                        f"Self-reported hr_id {fencer.hr_id} ({hr_name}) rejected: "
                        f"name mismatch (similarity={ratio:.2f})"
                    )
                    cleared = fencer.model_copy(update={
                        "hr_id": None,
                        "problems": (fencer.problems + " | " if fencer.problems else "") + problems_note,
                    })
                    need_llm.append(cleared)
                    updated_fencers.append(cleared)
                    continue
            _upsert_cache_entry(
                cache, fencer.hr_id,
                hr_name or fencer.name,
                hr_club or fencer.club or "",
                fencer.email or "",
                hr_nat or None,
                fencer.name if hr_name and _normalize(fencer.name) != _normalize(hr_name) else None,
            )
            updated_fencers.append(fencer.model_copy(update={
                "nationality": fencer.nationality or hr_nat or "",
                "club": hr_club or fencer.club,
            }))
            continue

        entry = _cache_lookup_by_email_and_name(cache, fencer.email or "", fencer.name) or _cache_lookup_by_name(cache, fencer.name)
        if entry:
            matched = fencer.model_copy(update={
                "hr_id": entry.hr_id,
                "nationality": fencer.nationality or to_nat_code(entry.nationality) or "",
                "club": entry.club or fencer.club or None,
            })
            _upsert_cache_entry(
                cache, entry.hr_id,
                entry.full_name, entry.club, fencer.email or "",
                entry.nationality or None,
                fencer.name if fencer.name != entry.full_name else None,
            )
            updated_fencers.append(matched)
            logger.info(f"Cache hit: {fencer.name} → hr_id={entry.hr_id}")
        else:
            need_llm.append(fencer)
            updated_fencers.append(fencer)  # placeholder

    if need_llm:
        logger.info(f"LLM matching {len(need_llm)} unmatched fencers ...")
        match_by_email = _call_llm(need_llm, fighters_text, config, instructions)

        for i, fencer in enumerate(updated_fencers):
            if fencer not in need_llm:
                continue
            m = match_by_email.get(fencer.email or "")
            if m and m.get("hr_id"):
                updated_fencers[i] = fencer.model_copy(update={
                    "hr_id": m["hr_id"],
                    "nationality": fencer.nationality or to_nat_code(m.get("nationality")) or "",
                    "club": m.get("matched_club") or fencer.club,
                })
                _upsert_cache_entry(
                    cache, m["hr_id"],
                    m.get("matched_name") or fencer.name,
                    m.get("matched_club") or fencer.club or "",
                    fencer.email or "",
                    m.get("nationality"),
                    fencer.name if m.get("matched_name") and fencer.name != m["matched_name"] else None,
                )
                logger.info(f"LLM matched: {fencer.name} → hr_id={m['hr_id']}")
            else:
                logger.warning(f"No match found for: {fencer.name}")

    # Apply persisted corrections — always wins over cache/LLM result
    corrections = load_corrections(data_dir)
    if corrections:
        corrections_lower = {k.lower(): (k, v) for k, v in corrections.items()}
        for i, fencer in enumerate(updated_fencers):
            key = fencer.name.lower()
            if key not in corrections_lower:
                continue
            _, correct_hr_id = corrections_lower[key]
            old_hr_id = fencer.hr_id
            if old_hr_id == correct_hr_id:
                continue
            logger.info(f"Applying correction: {fencer.name} → hr_id={correct_hr_id} (was {old_hr_id})")
            # Clean up old cache entry
            if old_hr_id is not None:
                old_key = str(old_hr_id)
                if old_key in cache:
                    entry = cache[old_key]
                    name_lower = fencer.name.lower()
                    entry.alternative_names_used = [
                        n for n in entry.alternative_names_used if n.lower() != name_lower
                    ]
                    if fencer.email:
                        correct_key = str(correct_hr_id) if correct_hr_id is not None else None
                        email_in_correct = (
                            correct_key is not None
                            and correct_key in cache
                            and fencer.email.lower() in {e.lower() for e in cache[correct_key].emails_used}
                        )
                        if not email_in_correct:
                            entry.emails_used = [
                                e for e in entry.emails_used if e.lower() != fencer.email.lower()
                            ]
            # Apply correction
            updated_fencers[i] = fencer.model_copy(update={"hr_id": correct_hr_id})
            # Add new cache entry
            if correct_hr_id is not None:
                hr_name, hr_nat, hr_club = hr_index.get(correct_hr_id, (None, None, None))
                _upsert_cache_entry(
                    cache, correct_hr_id,
                    hr_name or fencer.name,
                    hr_club or fencer.club or "",
                    fencer.email or "",
                    hr_nat or fencer.nationality or None,
                    fencer.name if hr_name and _normalize(fencer.name) != _normalize(hr_name) else None,
                )

    _save_cache(cache, cache_path)

    out_path = data_dir / FENCERS_MATCHED_FILE
    _save_matched(updated_fencers, out_path)
    return updated_fencers


def _load_matched_fencers(data_dir: Path) -> list[FencerRecord] | None:
    return load_fencers_list(data_dir, filename=FENCERS_MATCHED_FILE)


def _save_matched(fencers: list[FencerRecord], path: Path) -> None:
    save_fencers_list(fencers, path)
