"""Step 4: Deduplicate fencers sharing the same hr_id using an LLM merge."""

import json
import logging
import textwrap

from config.tracing import observe
from pydantic import BaseModel
from pydantic_ai import Agent, ModelSettings

from config import RegConfig, Step
from models import FencerRecord
from msgs import render_msg
from utils import load_fencers_list, save_fencers_list, FENCERS_DEDUPED_FILE, FENCERS_DEDUPED_FP_FILE

logger = logging.getLogger(__name__)

FENCERS_LIKELY_GROUPS_PENDING_FILE = "fencers_likely_groups_pending.json"


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
        system_prompt=render_msg("step4_system_prompt", {"language": config.language}),
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
        system_prompt=render_msg("step4_no_id_dup_system_prompt", {"language": config.language}),
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


# ---------------------------------------------------------------------------
# Discord display helpers — table formatting for step-4 dedup results
# ---------------------------------------------------------------------------

_NOTES_WRAP = 30
_DEDUP_FIELDS = ["Name", "Nationality", "Club", "Disciplines", "HR ID", "Notes"]


def _extract_dedup_fields(f: FencerRecord) -> dict[str, str]:
    return {
        "Name": f.name,
        "Nationality": f.nationality or "",
        "Club": f.club or "",
        "Disciplines": " / ".join(d.str() for d in f.disciplines),
        "HR ID": str(f.hr_id) if f.hr_id else "",
        "Notes": f.notes or "",
    }


def _transposed_dedup_table_text(
    records: list[FencerRecord],
    col_labels: list[str],
    note: str | None = None,
) -> str:
    """Transposed dedup table: fields as rows, records as columns. Notes are line-wrapped."""
    all_data = [_extract_dedup_fields(r) for r in records]
    for d in all_data:
        if d["Notes"]:
            d["Notes"] = "\n".join(textwrap.wrap(d["Notes"], _NOTES_WRAP))

    field_col_w = max(len("Field"), max(len(fn) for fn in _DEDUP_FIELDS))
    col_widths = [field_col_w]
    for i, label in enumerate(col_labels):
        w = len(label)
        for fn in _DEDUP_FIELDS:
            for segment in all_data[i][fn].split("\n"):
                w = max(w, len(segment))
        col_widths.append(w)

    def _pad(s: str, w: int) -> str:
        return s.ljust(w)

    def _row(cells: list[str]) -> str:
        return "│ " + " │ ".join(_pad(cells[i], col_widths[i]) for i in range(len(cells))) + " │"

    def _rule(lft: str, mid: str, rgt: str) -> str:
        return lft + mid.join("─" * (w + 2) for w in col_widths) + rgt

    out: list[str] = ["```"]
    out.append(_rule("┌", "┬", "┐"))
    out.append(_row(["Field"] + col_labels))
    out.append(_rule("├", "┼", "┤"))
    for fn in _DEDUP_FIELDS:
        cell_splits = [[fn]] + [all_data[i][fn].split("\n") for i in range(len(records))]
        max_lines = max(len(s) for s in cell_splits)
        for li in range(max_lines):
            out.append(_row([s[li] if li < len(s) else "" for s in cell_splits]))
    out.append(_rule("└", "┴", "┘"))
    out.append("```")

    result = "\n".join(out)
    if note:
        result += f"\n_{note}_"
    return result


def _dedup_table_text(inputs: list[FencerRecord], merged: FencerRecord, note: str) -> str:
    labels = [f"record {i + 1}" for i in range(len(inputs))] + ["→ final"]
    return _transposed_dedup_table_text(list(inputs) + [merged], labels, note=note)


def _dedup_likely_table_text(group: list[FencerRecord]) -> str:
    labels = [f"record {i + 1}" for i in range(len(group))]
    return _transposed_dedup_table_text(group, labels)
