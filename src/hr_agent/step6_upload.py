"""Step 6: Upload enriched fencer data to the output Google Sheet using LLM + gspread tools."""

import logging
from dataclasses import dataclass

import gspread
import gspread.utils
from jinja2 import Template
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.anthropic import AnthropicModelSettings

from config import Config, Step
from models import FencerRating, FencerRecord

logger = logging.getLogger(__name__)

FENCERS_WORKSHEET = "Fencers"

SYSTEM_PROMPT = Template("""You are a Google Sheets editor for a HEMA tournament.
You have access to the following tools to read and write a Google Spreadsheet:
  - list_worksheet() → returns current worksheet content as pipe-separated rows (row 1 is the header)
  - update_cell(row: int, col: int, value: str)
    - updates a single cell at (row, col), both 1-indexed
  - update_row(index: int, values: list[str], col_offset: int = 1)
    - updates row at index starting from column col_offset+1, skipping the first col_offset columns
  - update_col(index: int, values: list[str], row_offset: int = 1)
    - updates column at index starting from row row_offset+1, skipping the first row_offset rows
  - update_block(row: int, col: int, values: list[list[str]])
    - updates a rectangular block whose top-left cell is (row, col) with a 2D list of values (list of rows)

Workflow for every task:
1. Call list_worksheet() to read the current state.
2. Compare it against the data you were given.
3. Make only the changes that are needed
   - skip cells that already have the correct value.
4. Prefer bulk tools (update_block, update_row, update_col) over update_cell where possible,
   but never sacrifice correctness for bulk size.

When finished output either:
- "DONE" if everything is finished properly
- "RERUN" if more work is needed and context is already too long and messy
- "ERROR" if work cannot be done for any reason

{{specific_task}}

""")

FENCERS_PROMPT = Template("""## Worksheet "Fencers"

Synchronize the "Fencers" worksheet with the provided list of registered fencers,
preserving their registration order (fencer [1] goes to data row 2, fencer [2] to row 3, etc.).

Column layout (col index → field: type):
  1: Reg.             – registration order number: int
  2: Name             – full name of the fencer: str
  3: Nat.             – nationality code (CZ, SK, DE, …), blank if unknown: str
  4: Club             – club name, blank if unknown: str
  5: HR_ID            – hemaratings.com numeric ID, blank if unknown: int
  6: Disciplines      – comma-separated disciplines from {{disciplines}}: str
  7: Paid             – leave blank (do not touch): str
  8: Afterparty       – Yes / No / Other, blank if not provided: str
  9: Borrow weapons   – comma-separated weapon codes the fencer wants to borrow, blank if none: str
  10: Notes           – free-text notes from the fencer: str

Rules:
1. Row 1 is the header — never overwrite it.
2. Data rows start at row 2. Fencer [1] → row 2, fencer [2] → row 3, etc.
3. Column 1 (Reg.) is managed manually — never write to it. Always use col_offset=1 (the default)
   so writes start from col 2 (Name) onwards.
4. Row order = registration order. Never reorder or delete existing rows.
5. Trust what is already in the sheet: if a cell is non-empty and differs from your data,
   the difference is likely a deliberate manual correction — leave it unchanged.
   Only write to cells that are blank or already match your data exactly.
6. To decide which fencers to append: find the last fencer in the sheet whose name matches
   your data — call their data index LAST. Append all data fencers with index > LAST.
   Data fencers with index ≤ LAST that are absent from the sheet were manually removed — skip them.
""")

DISCIPLINE_PROMPT = Template("""## Worksheet "{{discipline}}"

Synchronize the "{{discipline}}" worksheet with the list of fencers registered for {{discipline}},
preserving their registration order (fencer [1] goes to data row 2, fencer [2] to row 3, etc.).

Column layout (col index → field: type):
  1: No.              – table row number, do not modify: int
  2: Name             – full name of the fencer: str
  3: Nat.             – nationality code (CZ, SK, DE, …), blank if unknown: str
  4: Club             – club name, blank if unknown: str
  5: HR_ID            – hemaratings.com numeric ID, blank if unknown: int
  6: HRating          – current weighted rating in {{discipline}}, blank if unavailable: float
  7: HRank            – current rank in {{discipline}}, blank if unavailable: int

Rules:
1. Row 1 is the header — never overwrite it.
2. Column 1 is table index — never overwrite it, if missing fill it so it keeps the sequence.
3. Data rows start at row 2. Fencer [1] → row 2, fencer [2] → row 3, etc.
4. Row order = registration order. Never reorder or delete existing rows.
5. Always overwrite HRating (col 6) and HRank (col 7) with the values from your data —
   these are refreshed from HEMA Ratings on every run and must reflect the latest values.
6. For all other columns (Name, Nat., Club, HR_ID): if a cell is non-empty and differs from
   your data, treat it as a deliberate manual correction and leave it unchanged.
   Only write to those cells if they are blank or already match your data exactly.
7. To decide which fencers to append: find the last fencer in the sheet whose name matches
   your data — call their data index LAST. Append all data fencers with index > LAST.
   Data fencers with index ≤ LAST that are absent from the sheet were manually removed — skip them.
""")


@dataclass
class SheetDeps:
    spreadsheet: gspread.Spreadsheet
    worksheet: str


def _get_or_open_worksheet(sh: gspread.Spreadsheet, name: str) -> gspread.Worksheet | None:
    try:
        return sh.worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        return None


def _list_fencers(fencers: list[FencerRecord]):
    lines = ["=== FENCER DATA ==="]
    for i, f in enumerate(fencers, 1):
        lines.append(
            f"[{i}] {f.name} | nat={f.nationality if f.nationality else ''} | club={f.club if f.club else ''} | hr_id={f.hr_id if f.hr_id else ''} "
            f"| disciplines={','.join([d.str() for d in f.disciplines])} "
            f"| borrow={','.join([w for w in f.borrow]) if f.borrow else ''} | after_party={f.after_party} | notes={f.notes}"
        )
    return "\n".join(lines)


def _build_data_prompt(
    discipline_code: str,
    fencers: list[FencerRecord],
    ratings: dict[int, dict[str, FencerRating]],
) -> str:
    registered = [f for f in fencers if any(d.str() == discipline_code for d in f.disciplines)]
    lines = ["=== FENCER DATA ==="]
    for i, f in enumerate(registered, 1):
        rating = ratings.get(f.hr_id, {}).get(discipline_code) if f.hr_id else None
        lines.append(
            f"[{i}] {f.name} | nat={f.nationality} | club={f.club} | hr_id={f.hr_id} "
            f"| rating={rating.rating if rating else ''} | rank={rating.rank if rating else ''}"
        )
    return "\n".join(lines)




def update_sheet_agent_run(
        config: Config,
        sh: gspread.Spreadsheet,
        work_sheet_name: str,
        system_prompt: str,
        data_prompt: str,
        rerun_cnt = 5
):

    thinking = (
        {"type": "enabled", "budget_tokens": config.upload_thinking_tokens}
        if config.upload_thinking_tokens > 0
        else {"type": "disabled"}
    )
    agent = Agent(
        model=config.model(Step.UPLOAD),
        model_settings=AnthropicModelSettings(thinking=thinking),
        deps_type=SheetDeps,
        system_prompt=system_prompt,
    )

    @agent.tool
    def list_worksheet(ctx: RunContext[SheetDeps]) -> str:
        """Return the whole worksheet."""
        ws = _get_or_open_worksheet(ctx.deps.spreadsheet, ctx.deps.worksheet)
        if ws is None:
            raise RuntimeError(f"Worksheet '{ctx.deps.worksheet}' not found.")
        rows = ws.get_all_values()
        return "\n".join(["|".join([str(cell) for cell in row]) for row in rows])

    @agent.tool
    def update_row(ctx: RunContext[SheetDeps], index: int, values: list[str | int | float], col_offset: int = 1):
        """
        Update an existing row with values
        Params:
            index – Index of the row to be updated (from 1)
            values – Values to be updated
            col_offset – Columns to skip before inserting values, default 1 (skip first column)
        """
        ws = _get_or_open_worksheet(ctx.deps.spreadsheet, ctx.deps.worksheet)
        start = gspread.utils.rowcol_to_a1(index, col_offset + 1)
        ws.update([values], start)

    @agent.tool
    def update_col(ctx: RunContext[SheetDeps], index: int, values: list[str | int | float], row_offset: int = 1):
        """
        Update an existing col with values
        Params:
            index – Index of the column to be updated (from 1)
            values – Values to be updated
            row_offset – Rows to skip before inserting values, default 1 (skip header row)
        """
        ws = _get_or_open_worksheet(ctx.deps.spreadsheet, ctx.deps.worksheet)
        start = gspread.utils.rowcol_to_a1(row_offset + 1, index)
        ws.update([[v] for v in values], start)

    @agent.tool
    def update_block(ctx: RunContext[SheetDeps], row: int, col: int, values: list[list[str | int | float]]):
        """
        Update a rectangular block of cells starting at (row, col) with the given 2D values.
        Params:
            row – Row index of the top-left cell (from 1)
            col – Column index of the top-left cell (from 1)
            values – 2D list of values to write (list of rows, each row a list of cell values)
        """
        ws = _get_or_open_worksheet(ctx.deps.spreadsheet, ctx.deps.worksheet)
        start = gspread.utils.rowcol_to_a1(row, col)
        ws.update(values, start)

    @agent.tool
    def update_cell(ctx: RunContext[SheetDeps], row: int, col: int, value: str | int | float):
        """
        Update a single cell.
        Params:
            row – Row index (from 1)
            col – Column index (from 1)
            value – Value to write
        """
        ws = _get_or_open_worksheet(ctx.deps.spreadsheet, ctx.deps.worksheet)
        ws.update_cell(row, col, value)

    deps = SheetDeps(
        spreadsheet=sh,
        worksheet=work_sheet_name
    )

    for i in range(rerun_cnt):
        logger.info(f"Run no. {i+1}")
        result = agent.run_sync(data_prompt, deps=deps)
        logger.info(result.output)
        if "RERUN" not in result.output:
            break


def upload_fencers(fencers: list[FencerRecord], config: Config, sh: gspread.Spreadsheet):
    discipline_codes = set(config.disciplines.keys())
    fencers_prompt = FENCERS_PROMPT.render(disciplines=",".join(discipline_codes))
    system_prompt = SYSTEM_PROMPT.render(specific_task=fencers_prompt)
    data_prompt = _list_fencers(fencers)
    worksheet_name = FENCERS_WORKSHEET

    update_sheet_agent_run(config, sh, worksheet_name, system_prompt, data_prompt)


def upload_discipline(discipline_code: str, fencers: list[FencerRecord], ratings: dict[int, dict[str, FencerRating]], config: Config, sh: gspread.Spreadsheet):

    discipline_prompt = DISCIPLINE_PROMPT.render(discipline=discipline_code)
    system_prompt = SYSTEM_PROMPT.render(specific_task=discipline_prompt)
    data_prompt = _build_data_prompt(discipline_code, fencers, ratings)

    update_sheet_agent_run(config, sh, discipline_code, system_prompt, data_prompt)


def upload_results(
    fencers: list[FencerRecord],
    ratings: dict[int, dict[str, FencerRating]],
    config: Config,
) -> None:
    """Upload enriched fencer data to the output Google Sheet."""
    logger.info("Authorizing and opening output sheet ...")
    gc = gspread.service_account(filename=config.creds_path)
    sh = gc.open_by_url(config.output_sheet_url)
    logger.info("Opened output sheet")

    logger.info("Uploading Fencers ...")
    upload_fencers(fencers, config, sh)
    logger.info("Fencers done")

    for discipline in config.disciplines:
        logger.info(f"Uploading {discipline} ...")
        upload_discipline(discipline, fencers, ratings, config, sh)
        logger.info(f"{discipline} done")

    logger.info("Upload complete")