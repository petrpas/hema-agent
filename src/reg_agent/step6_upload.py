"""Step 6: Upload enriched fencer data to the output Google Sheet using LLM + gspread tools."""

import logging
import re
from dataclasses import dataclass

import gspread
import gspread.utils
from config.tracing import observe
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.anthropic import AnthropicModelSettings

from config import RegConfig, Step
from models import FencerRating, FencerRecord
from msgs import render_msg

logger = logging.getLogger(__name__)

FENCERS_WORKSHEET = "Fencers"


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
            f"| borrow={','.join([w for w in f.borrow]) if f.borrow else ''} | after_party={f.after_party} "
            f"| aftersparring={f.aftersparring} | accommodation={f.accommodation} | notes={f.notes}"
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
        config: RegConfig,
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


def upload_fencers(fencers: list[FencerRecord], config: RegConfig, sh: gspread.Spreadsheet):
    fencers_prompt = render_msg("step6_fencers_prompt", {})
    system_prompt = render_msg("step6_system_prompt", {"specific_task": fencers_prompt})
    data_prompt = _list_fencers(fencers)
    worksheet_name = FENCERS_WORKSHEET

    update_sheet_agent_run(config, sh, worksheet_name, system_prompt, data_prompt)


def upload_discipline(discipline_code: str, fencers: list[FencerRecord], ratings: dict[int, dict[str, FencerRating]], config: RegConfig, sh: gspread.Spreadsheet):
    ws_titles = {ws.title for ws in sh.worksheets()}
    if discipline_code not in ws_titles:
        logger.info("Creating missing worksheet '%s' by cloning an existing discipline tab", discipline_code)
        existing_discipline = next(
            (ws for ws in sh.worksheets() if ws.title in config.disciplines and ws.title != discipline_code),
            None,
        )
        if existing_discipline is None:
            raise RuntimeError(
                f"Cannot create worksheet '{discipline_code}': no existing discipline tab to clone from."
            )
        sh.duplicate_sheet(existing_discipline.id, new_sheet_name=discipline_code)

    discipline_prompt = render_msg("step6_discipline_prompt", {"discipline": discipline_code})
    system_prompt = render_msg("step6_system_prompt", {"specific_task": discipline_prompt})
    data_prompt = _build_data_prompt(discipline_code, fencers, ratings)

    update_sheet_agent_run(config, sh, discipline_code, system_prompt, data_prompt)


def setup_output_sheet(config: RegConfig) -> str:
    """Copy the output template and return the new sheet URL.

    Only copies the template — discipline worksheets are created later by
    create_discipline_worksheets() once ratings are available (step 5).
    """
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", config.output_template)
    if not m:
        raise ValueError(f"Cannot extract sheet ID from output_template: {config.output_template}")
    template_id = m.group(1)

    folder_id: str | None = None
    if config.drive_folder_url:
        fm = re.search(r"/folders/([a-zA-Z0-9_-]+)", config.drive_folder_url)
        if fm:
            folder_id = fm.group(1)
            logger.info("Target Drive folder id=%s", folder_id)
        else:
            logger.warning("Could not extract folder ID from drive_folder_url: %s", config.drive_folder_url)

    tournament_title = config.tournament_name.replace("_", " ").title()
    logger.info("Copying template sheet %s → '%s' ...", template_id, tournament_title)
    gc = gspread.service_account(filename=config.creds_path)
    sh = gc.copy(template_id, title=f"{tournament_title} Fencers", copy_permissions=False,
                 folder_id=folder_id)
    logger.info("New spreadsheet id=%s", sh.id)

    sh.share(None, perm_type="anyone", role="writer")  # type: ignore[arg-type]
    logger.info("Shared with anyone-with-link (writer)")

    url = f"https://docs.google.com/spreadsheets/d/{sh.id}/edit"
    logger.info("Output sheet created: %s", url)
    return url


_DISCIPLINE_HEADER = ["No.", "Name", "Nat.", "Club", "HR_ID", "HRating", "HRank"]
_BOLD = {"textFormat": {"bold": True}}


def create_discipline_worksheets(
    config: RegConfig,
    sh: gspread.Spreadsheet,
    fencers: list[FencerRecord],
    ratings: dict[int, dict[str, FencerRating]],
) -> None:
    """Create and fill per-discipline worksheets from scratch.

    Called after step 5 (ratings) so ratings data is available.
    Safe to call multiple times — skips disciplines that already have a tab.
    """
    existing_titles = {ws.title for ws in sh.worksheets()}
    disciplines_to_create = [code for code in config.disciplines if code not in existing_titles]
    if not disciplines_to_create:
        logger.info("All discipline worksheets already exist — nothing to create")
        return

    for code in disciplines_to_create:
        registered = [f for f in fencers if any(d.str() == code for d in f.disciplines)]
        rows = []
        for i, f in enumerate(registered, 1):
            rating = ratings.get(f.hr_id, {}).get(code) if f.hr_id else None
            rows.append([
                i,
                f.name,
                f.nationality or "",
                f.club or "",
                f.hr_id if f.hr_id is not None else "",
                rating.rating if rating and rating.rating is not None else "",
                rating.rank if rating and rating.rank is not None else "",
            ])

        logger.info("Creating worksheet '%s' (%d fencers)", code, len(rows))
        ws = sh.add_worksheet(title=code, rows=max(200, len(rows) + 10), cols=10)
        ws.update([_DISCIPLINE_HEADER], "A1")
        if rows:
            ws.update(rows, "A2")

        # Bold header row and No. column
        ws.format("A1:G1", _BOLD)
        if rows:
            ws.format(f"A1:A{len(rows) + 1}", _BOLD)

    fencers_ws = sh.worksheet(FENCERS_WORKSHEET)
    discipline_worksheets = [sh.worksheet(code) for code in config.disciplines]
    sh.reorder_worksheets([fencers_ws] + discipline_worksheets)
    logger.info("Worksheets reordered: %s", [FENCERS_WORKSHEET] + list(config.disciplines))


@observe(capture_input=False, capture_output=False)
def upload_results(
    fencers: list[FencerRecord],
    ratings: dict[int, dict[str, FencerRating]],
    config: RegConfig,
) -> None:
    """Upload enriched fencer data to the output Google Sheet."""
    if not config.output_sheet_url:
        raise ValueError("output_sheet_url is not set in user config — set it before uploading.")
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