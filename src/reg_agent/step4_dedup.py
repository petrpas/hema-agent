"""Step 4: Deduplicate fencers sharing the same hr_id using an LLM merge."""

import json
import logging

from config.tracing import observe
from jinja2 import Template
from pydantic import BaseModel
from pydantic_ai import Agent, ModelSettings

from config import RegConfig, Step
from models import FencerRecord
from utils import load_fencers_list, save_fencers_list, FENCERS_DEDUPED_FILE, FENCERS_DEDUPED_FP_FILE

logger = logging.getLogger(__name__)

FENCERS_LIKELY_GROUPS_PENDING_FILE = "fencers_likely_groups_pending.json"

SYSTEM_PROMPT_TEMPLATE = Template("""You are a data assistant for a HEMA tournament.
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

After merging, write a short `merge_note` (1 sentence, language: {{language}}) explaining
what was different between the records and what decision was made (e.g. "dup 2 added RA discipline.
Disciplines were merged." or "dup 2 was a correction — used as authoritative.").
""")

NO_ID_DUP_SYSTEM_PROMPT_TEMPLATE = Template("""You are a data assistant for a HEMA tournament.
You will receive a list of fencer registrations that do NOT have a HEMA Ratings ID.
Your task: identify groups of registrations that likely belong to the same person.

Classify each potential duplicate group into exactly one category:

**surely**: Identical or near-identical name AND at least one matching corroborating field
(nationality, club, email, or overlapping disciplines). Extremely high confidence — safe to
auto-merge without asking the organiser.

**likely**: Same or similar name, but fewer corroborating fields. Human confirmation is warranted.

**possible**: Vaguely similar names with no corroborating evidence. Classify here rather than
"likely" to avoid false positives. These will be silently discarded.

If a pair/group does not fit any category, do not include it.
Every fencer name may appear in at most one group across all categories.
Input is a JSON array. Output the three lists of name groups in JSON.
Language for your internal reasoning: {{language}}
""")


class FencerMergeResult(BaseModel):
    fencer: FencerRecord
    merge_note: str


class NoIdDuplicateGroups(BaseModel):
    surely: list[list[str]]    # auto-merge safe
    likely: list[list[str]]    # ask user
    possible: list[list[str]]  # silently discard


def merge_group(records: list[FencerRecord], config: RegConfig, hint: str | None = None) -> FencerMergeResult:
    agent = Agent(
        model=config.model(Step.DEDUP),
        model_settings=ModelSettings(temperature=0.0),
        output_type=FencerMergeResult,
        system_prompt=SYSTEM_PROMPT_TEMPLATE.render(language=config.language),
        retries=3,
    )
    sorted_records = sorted(records, key=lambda r: r.registration_time)
    records_json = json.dumps([r.model_dump() for r in sorted_records], ensure_ascii=False, indent=2)
    prompt = f"Merge these duplicate registrations:\n\n```json\n{records_json}\n```"
    if hint:
        prompt += f'\n\nAdditional instruction from organiser: "{hint}"'
    result = agent.run_sync(prompt)
    logger.info(f"Merged {len(records)} duplicates for hr_id={records[0].hr_id} → {result.output.fencer.name}")
    return result.output


@observe(capture_input=False, capture_output=False)
def find_no_id_duplicates_llm(
    fencers: list[FencerRecord], config: RegConfig
) -> NoIdDuplicateGroups:
    """Call LLM to find likely duplicates among fencers with hr_id=None."""
    if not fencers:
        return NoIdDuplicateGroups(surely=[], likely=[], possible=[])

    agent = Agent(
        model=config.model(Step.DEDUP),
        model_settings=ModelSettings(temperature=0.0),
        output_type=NoIdDuplicateGroups,
        system_prompt=NO_ID_DUP_SYSTEM_PROMPT_TEMPLATE.render(language=config.language),
        retries=3,
    )
    fencer_data = [
        {
            "name": f.name,
            "nationality": f.nationality,
            "club": f.club,
            "email": f.email,
            "disciplines": [d.str() for d in f.disciplines],
        }
        for f in fencers
    ]
    records_json = json.dumps(fencer_data, ensure_ascii=False, indent=2)
    result = agent.run_sync(
        f"Find duplicate registrations among these fencers (none have a HEMA Ratings ID):\n\n"
        f"```json\n{records_json}\n```"
    )
    n_surely = len(result.output.surely)
    n_likely = len(result.output.likely)
    n_possible = len(result.output.possible)
    logger.info(f"No-id duplicates: {n_surely} surely, {n_likely} likely, {n_possible} possible groups")
    return result.output


# DeduplicationReport: list of (input_records_sorted_oldest_first, merged_result) for each merged group
DeduplicationReport = list[tuple[list[FencerRecord], FencerMergeResult]]


def _fingerprint(fencers: list[FencerRecord]) -> str:
    """Stable fingerprint of the input list — captures count, identity, and duplicates."""
    return str(sorted((f.hr_id or 0, f.name) for f in fencers))


@observe(capture_input=False, capture_output=False)
def deduplicate_fencers(
    fencers: list[FencerRecord], config: RegConfig
) -> tuple[list[FencerRecord], DeduplicationReport, list[list[FencerRecord]]]:
    """Merge records sharing the same hr_id and auto-merge surely-identical no-hr_id records.

    Returns (result_list, report, likely_groups) where:
    - report: one entry per merged group (input_records_sorted_oldest_first, FencerMergeResult)
    - likely_groups: no-hr_id groups that need organiser confirmation before merging
    On cache hit the report and likely_groups are empty.
    """
    out_path = config.data_dir / FENCERS_DEDUPED_FILE
    fp_path = config.data_dir / FENCERS_DEDUPED_FP_FILE

    current_fp = _fingerprint(fencers)
    if out_path.exists() and fp_path.exists() and fp_path.read_text() == current_fp:
        existing = load_fencers_list(config.data_dir, FENCERS_DEDUPED_FILE)
        if existing is not None:
            logger.info(f"Input unchanged — loading {FENCERS_DEDUPED_FILE}")
            return existing, [], []

    # Group by hr_id
    groups: dict[int, list[FencerRecord]] = {}
    for fencer in fencers:
        if fencer.hr_id is not None:
            groups.setdefault(fencer.hr_id, []).append(fencer)

    dup_groups = {hr_id: g for hr_id, g in groups.items() if len(g) > 1}
    if dup_groups:
        logger.info(f"Found {len(dup_groups)} hr_id(s) with duplicate registrations: {list(dup_groups.keys())}")
    else:
        logger.info("No hr_id duplicates found")

    # Merge each hr_id duplicate group; singletons pass through unchanged
    merged: dict[int, FencerRecord] = {}
    report: DeduplicationReport = []
    for hr_id, group in groups.items():
        if len(group) > 1:
            sorted_group = sorted(group, key=lambda r: r.registration_time)
            merge_result = merge_group(sorted_group, config)
            merged[hr_id] = merge_result.fencer
            report.append((sorted_group, merge_result))
        else:
            merged[hr_id] = group[0]

    # Find no-hr_id duplicates via LLM
    no_id_fencers = [f for f in fencers if f.hr_id is None]
    likely_groups: list[list[FencerRecord]] = []

    # Maps from Python object id() for surely-group reconstruction
    superseded_ids: set[int] = set()           # skip these records in output
    leader_to_merged: dict[int, FencerRecord] = {}  # id(first) → merged record

    if no_id_fencers:
        no_id_dups = find_no_id_duplicates_llm(no_id_fencers, config)

        # Auto-merge surely groups
        for group_names in no_id_dups.surely:
            group = [f for f in no_id_fencers if f.name in group_names]
            if len(group) < 2:
                logger.warning(f"Surely group has < 2 records after name lookup: {group_names}")
                continue
            sorted_group = sorted(group, key=lambda r: r.registration_time)
            merge_result = merge_group(sorted_group, config)
            report.append((sorted_group, merge_result))
            leader_to_merged[id(sorted_group[0])] = merge_result.fencer
            for r in sorted_group[1:]:
                superseded_ids.add(id(r))
            logger.info(f"Auto-merged surely no-id group: {[f.name for f in sorted_group]}")

        # Collect likely groups for user confirmation (possible groups silently discarded)
        for group_names in no_id_dups.likely:
            group = [f for f in no_id_fencers if f.name in group_names]
            if len(group) >= 2:
                likely_groups.append(sorted(group, key=lambda r: r.registration_time))

    # Reconstruct list in original order
    result: list[FencerRecord] = []
    seen: set[int] = set()
    for fencer in fencers:
        if fencer.hr_id is None:
            fencer_id = id(fencer)
            if fencer_id in superseded_ids:
                continue  # superseded by a surely merge
            elif fencer_id in leader_to_merged:
                result.append(leader_to_merged[fencer_id])  # replaced by merged record
            else:
                result.append(fencer)
        elif fencer.hr_id not in seen:
            result.append(merged[fencer.hr_id])
            seen.add(fencer.hr_id)

    logger.info(f"Deduplicated: {len(fencers)} → {len(result)} fencers")
    save_fencers_list(result, out_path)
    fp_path.write_text(current_fp)
    return result, report, likely_groups
