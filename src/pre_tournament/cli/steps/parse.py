"""Step 2 — LLM-parse the latest registration CSV.

Before handing the CSV to the LLM, a cheap header check rejects files that
are clearly *not* a registration sheet (most commonly a bank-transaction
export misplaced under `registration_csv/`). Without this guard the parser
silently turns a bank statement into nonsense "fencers" and `run-all`
propagates that through match→dedup, corrupting the whole data dir.
"""

from pathlib import Path

from pre_tournament.cli.errors import InvalidArtifact
from pre_tournament.cli.steps._base import StepResult, artifacts, timed
from step2_parse import parse_registrations

# Columns that uniquely identify a Czech-bank CSV export (the format step7
# payments parses). Lower-cased, BOM/quote/space stripped before matching.
_BANK_COLS = {
    "datum provedení",
    "datum zaúčtování",
    "číslo účtu",
    "zaúčtovaná částka",
    "číslo protiúčtu",
    "id transakce",
    "měna účtu",
}

# Substrings that should appear *somewhere* in a real registration header
# (tournament headers vary wildly in EN/CS wording, so match loosely).
_REG_TOKENS = (
    "mail", "jméno", "jmeno", "name", "příjmení", "prijmeni", "surname",
    "surename", "klub", "club", "zbran", "weapon", "kategor",
    "časová značka", "casova znacka", "timestamp",
)


def _split_header(line: str) -> list[str]:
    line = line.lstrip("﻿").rstrip("\r\n")
    sep = ";" if line.count(";") > line.count(",") else ","
    return [c.strip().strip('"').strip().lower() for c in line.split(sep)]


def _validate_registration_csv(path: Path) -> None:
    """Raise InvalidArtifact (exit 2) if `path` is not a registration CSV."""
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            header_line = fh.readline()
    except OSError as e:
        raise InvalidArtifact(f"cannot read {path.name}: {e}") from e

    cols = _split_header(header_line)
    preview = ", ".join(cols[:6]) + (" …" if len(cols) > 6 else "")

    bank_hits = _BANK_COLS.intersection(cols)
    if len(bank_hits) >= 3:
        raise InvalidArtifact(
            f"{path.name} looks like a bank-transaction export, not a "
            f"registration sheet (columns: {preview}). "
            f"It must not live under registration_csv/ — remove/rename it, "
            f"or pass --csv PATH to the correct registration CSV."
        )

    blob = " ".join(cols)
    if not any(tok in blob for tok in _REG_TOKENS):
        raise InvalidArtifact(
            f"{path.name} has no recognizable registration columns "
            f"(name / e-mail / club / weapon). Header: {preview}. "
            f"Pass --csv PATH to the correct registration CSV."
        )


def cmd_parse(args, config) -> StepResult:
    data_dir = config.data_dir
    if args.csv:
        csv_path = artifacts.require(Path(args.csv), "registration CSV", args.csv)
    else:
        csv_path = artifacts.require(
            artifacts.latest_registration_csv(data_dir),
            "registration CSV",
            "run `download` (or pass --csv)",
        )

    # Sanity-gate the CSV *before* the LLM and before --force clears the
    # previous good output, so a wrong file can't corrupt the data dir.
    _validate_registration_csv(csv_path)

    # --force: deleting fencers_parsed.json defeats step2's _csv_unchanged
    # short-circuit (it only reloads when the parsed file exists).
    if args.force:
        artifacts.clear(artifacts.parsed(data_dir))

    res = StepResult(step="parse")
    with timed(res):
        fencers = parse_registrations(csv_path, config)

    weapon_counts: dict[str, int] = {}
    for f in fencers:
        for d in f.disciplines:
            weapon_counts[str(d.weapon)] = weapon_counts.get(str(d.weapon), 0) + 1
    no_id = sum(1 for f in fencers if f.hr_id is None)

    res.summary = f"parsed {len(fencers)} fencers"
    res.details = {
        "fencers": len(fencers),
        "without_hr_id": no_id,
        "weapons": ", ".join(f"{w}×{c}" for w, c in sorted(weapon_counts.items())),
        "source_csv": csv_path.name,
    }
    res.artifact = artifacts.parsed(data_dir)
    return res