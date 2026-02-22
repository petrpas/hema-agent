"""Step 4: Deduplicate fencers sharing the same hr_id using an LLM merge."""

import json
import logging
from pathlib import Path

from pydantic_ai import Agent, ModelSettings

from config import Config, Step
from models import FencerRecord
from utils import load_fencers_list, save_fencers_list, FENCERS_DEDUPED_FILE, FENCERS_DEDUPED_FP_FILE

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a data assistant for a HEMA tournament.
You will receive multiple registration records that belong to the same person (same HEMA Ratings ID),
sorted oldest first by registration_time.

First, check the notes fields for intent. A later record may explicitly say it is a correction
(e.g. "correction of previous", "I made a mistake earlier", "updated disciplines").
If so, treat that record's fields as authoritative for the fields it mentions, overriding earlier ones.

Default merge rules (apply when no correction intent is found):
- name: use the most complete/correctly spelled form
- registration_time: keep the earliest
- nationality, email, club, hr_id: prefer non-empty/non-null values
- disciplines: union of all disciplines across records
- borrow: union of all borrow requests
- after_party: if any record says Yes use Yes; if conflicting use Oth
- notes: concatenate non-empty notes separated by " | ", omit correction meta-comments
- problems: note any inconsistencies between the records
"""


def _merge_group(records: list[FencerRecord], config: Config) -> FencerRecord:
    agent = Agent(
        model=config.model(Step.DEDUP),
        model_settings=ModelSettings(temperature=0.0),
        output_type=FencerRecord,
        system_prompt=SYSTEM_PROMPT,
        retries=3,
    )
    sorted_records = sorted(records, key=lambda r: r.registration_time)
    records_json = json.dumps([r.model_dump() for r in sorted_records], ensure_ascii=False, indent=2)
    result = agent.run_sync(f"Merge these duplicate registrations:\n\n```json\n{records_json}\n```")
    logger.info(f"Merged {len(records)} duplicates for hr_id={records[0].hr_id} → {result.output.name}")
    return result.output


def _fingerprint(fencers: list[FencerRecord]) -> str:
    """Stable fingerprint of the input list — captures count, identity, and duplicates."""
    return str(sorted((f.hr_id or 0, f.name) for f in fencers))


def deduplicate_fencers(fencers: list[FencerRecord], config: Config) -> list[FencerRecord]:
    """Merge records sharing the same hr_id. Preserves registration order (first occurrence)."""
    out_path = config.data_dir / FENCERS_DEDUPED_FILE
    fp_path = config.data_dir / FENCERS_DEDUPED_FP_FILE

    current_fp = _fingerprint(fencers)
    if out_path.exists() and fp_path.exists() and fp_path.read_text() == current_fp:
        existing = load_fencers_list(config.data_dir, FENCERS_DEDUPED_FILE)
        if existing is not None:
            logger.info(f"Input unchanged — loading {FENCERS_DEDUPED_FILE}")
            return existing

    # Group by hr_id
    groups: dict[int, list[FencerRecord]] = {}
    for fencer in fencers:
        if fencer.hr_id is not None:
            groups.setdefault(fencer.hr_id, []).append(fencer)

    duplicates = {hr_id: g for hr_id, g in groups.items() if len(g) > 1}
    if duplicates:
        logger.info(f"Found {len(duplicates)} hr_id(s) with duplicate registrations: {list(duplicates.keys())}")
    else:
        logger.info("No duplicates found")

    # Merge each duplicate group; singletons pass through unchanged
    merged: dict[int, FencerRecord] = {
        hr_id: (_merge_group(group, config) if len(group) > 1 else group[0])
        for hr_id, group in groups.items()
    }

    # Reconstruct list in original order, skipping subsequent duplicates
    result: list[FencerRecord] = []
    seen: set[int] = set()
    for fencer in fencers:
        if fencer.hr_id is None:
            result.append(fencer)
        elif fencer.hr_id not in seen:
            result.append(merged[fencer.hr_id])
            seen.add(fencer.hr_id)

    logger.info(f"Deduplicated: {len(fencers)} → {len(result)} fencers")
    save_fencers_list(result, out_path)
    fp_path.write_text(current_fp)
    return result
