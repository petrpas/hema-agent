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
from utils import load_withdrawn, normalize_name

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
        model_settings=AnthropicModelSettings(thinking=thinking, max_tokens=16384),
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
    fencers_prompt = render_msg("reg/step6_fencers_prompt", {})
    system_prompt = render_msg("reg/step6_system_prompt", {"specific_task": fencers_prompt})
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

    discipline_prompt = render_msg("reg/step6_discipline_prompt", {"discipline": discipline_code})
    system_prompt = render_msg("reg/step6_system_prompt", {"specific_task": discipline_prompt})
    data_prompt = _build_data_prompt(discipline_code, fencers, ratings)

    update_sheet_agent_run(config, sh, discipline_code, system_prompt, data_prompt)


def setup_output_sheet(config: RegConfig) -> str:
    """Create a blank output sheet in the configured Drive folder and return its URL.

    Uses the Drive API directly (not gspread.create/copy) to place the file in the
    target folder in a single API call.  gspread's create() internally creates the
    file first then moves it, briefly touching the service account's own storage and
    triggering a quota error even on a quota-less SA.

    Discipline worksheets are added later by create_discipline_worksheets().
    """
    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build as _build

    folder_id: str | None = None
    if config.drive_folder_url:
        fm = re.search(r"/folders/([a-zA-Z0-9_-]+)", config.drive_folder_url)
        if fm:
            folder_id = fm.group(1)
            logger.info("Target Drive folder id=%s", folder_id)
        else:
            logger.warning("Could not extract folder ID from drive_folder_url: %s", config.drive_folder_url)

    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(config.creds_path, scopes=scopes)
    drive = _build("drive", "v3", credentials=creds)

    tournament_title = config.tournament_name.replace("_", " ").title()
    title = f"{tournament_title} Fencers"
    logger.info("Creating blank sheet '%s' in folder %s ...", title, folder_id)
    metadata: dict = {
        "name": title,
        "mimeType": "application/vnd.google-apps.spreadsheet",
    }
    if folder_id:
        metadata["parents"] = [folder_id]
    f = drive.files().create(body=metadata, fields="id", supportsAllDrives=True).execute()
    sheet_id = f["id"]
    logger.info("New spreadsheet id=%s", sheet_id)

    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)

    # Rename the default tab so init_fencers_sheet() can find it by name.
    sh.get_worksheet(0).update_title("Fencers")

    sh.share(None, perm_type="anyone", role="writer")  # type: ignore[arg-type]
    logger.info("Shared with anyone-with-link (writer)")

    url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
    logger.info("Output sheet created: %s", url)
    return url


_DISCIPLINE_HEADER = ["No.", "Name", "Nat.", "Club", "HR_ID", "HRating", "HRank"]
_BOLD = {"textFormat": {"bold": True}}
_THICK = {"style": "SOLID_MEDIUM"}
_DISCIPLINE_ROWS = 200


def _col_letter(n: int) -> str:
    """Convert 1-based column number to A1 letter notation (e.g. 1→A, 28→AB)."""
    result = ""
    while n:
        n, r = divmod(n - 1, 26)
        result = chr(65 + r) + result
    return result


def recalculate_seeds(ws: gspread.Worksheet) -> None:
    """Recalculate the Seed column for a discipline worksheet.

    Locates HRank and Seed columns by name; adds Seed after HRank if absent.
    Ranked fencers (numeric HRank) are seeded 1..N ascending; unranked fencers
    follow in their original row order (= registration order).
    Only rows with a non-empty No. cell are considered.
    """
    all_values = ws.get_all_values()
    if len(all_values) < 2:
        return

    header = all_values[0]

    def _find(name: str) -> int | None:
        for i, h in enumerate(header):
            if h.strip() == name:
                return i + 1  # 1-based
        return None

    name_col = _find("Name")
    hrank_col = _find("HRank")
    if hrank_col is None:
        logger.warning("HRank column not found in '%s' — skipping seed calculation", ws.title)
        return

    seed_col = _find("Seed")
    if seed_col is None:
        seed_col = hrank_col + 1
        ws.update_cell(1, seed_col, "Seed")
        ws.format(f"{_col_letter(seed_col)}1", {
            "textFormat": {"bold": True},
            "horizontalAlignment": "CENTER",
            "borders": {"bottom": _THICK},
        })
        logger.info("Added Seed column at col %d in '%s'", seed_col, ws.title)

    ranked: list[tuple[int, int]] = []   # (row_idx, hrank)
    unranked: list[int] = []             # row_idx

    for i, row in enumerate(all_values[1:], 1):
        name_val = row[name_col - 1].strip() if name_col and len(row) >= name_col else ""
        if not name_val:
            continue
        hrank_val = row[hrank_col - 1].strip() if len(row) >= hrank_col else ""
        try:
            ranked.append((i, int(hrank_val)))
        except (ValueError, TypeError):
            unranked.append(i)

    if not ranked and not unranked:
        return

    ranked.sort(key=lambda x: x[1])
    seeding_order = [row_idx for row_idx, _ in ranked] + unranked
    seed_map = {row_idx: seed_num for seed_num, row_idx in enumerate(seeding_order, 1)}

    last_row = max(seed_map)
    updates = [[seed_map.get(i, "")] for i in range(1, last_row + 1)]
    ws.update(updates, f"{_col_letter(seed_col)}2")
    logger.info(
        "Seeds recalculated for '%s': %d ranked, %d unranked",
        ws.title, len(ranked), len(unranked),
    )


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
        ws = sh.add_worksheet(title=code, rows=max(_DISCIPLINE_ROWS, len(rows) + 10), cols=10)
        ws.update([_DISCIPLINE_HEADER], "A1")
        if rows:
            ws.update(rows, "A2")

        # Pre-fill No. numbers 1–200
        ws.update([[i] for i in range(1, _DISCIPLINE_ROWS + 1)], "A2")

        # A1: bold, centered, thick bottom border only
        ws.format("A1", {
            "textFormat": {"bold": True},
            "horizontalAlignment": "CENTER",
            "borders": {"bottom": _THICK},
        })
        # B1:G1: bold, centered, thick bottom border
        ws.format("B1:G1", {
            "textFormat": {"bold": True},
            "horizontalAlignment": "CENTER",
            "borders": {"bottom": _THICK},
        })
        # A2:A{_DISCIPLINE_ROWS+1}: bold, thick right border
        ws.format(f"A2:A{_DISCIPLINE_ROWS + 1}", {
            "textFormat": {"bold": True},
            "borders": {"right": _THICK},
        })

        recalculate_seeds(ws)

    fencers_ws = sh.worksheet(FENCERS_WORKSHEET)
    discipline_worksheets = [sh.worksheet(code) for code in config.disciplines]
    sh.reorder_worksheets([fencers_ws] + discipline_worksheets)
    logger.info("Worksheets reordered: %s", [FENCERS_WORKSHEET] + list(config.disciplines))


def remove_fencers_from_sheets(names: list[str], config: RegConfig) -> dict[str, list[str]]:
    """Delete rows for the given fencer names from all worksheets.

    Searches by the Name column (case-insensitive exact match).
    Deletes bottom-to-top so row indexes stay valid during iteration.
    Recalculates seeds for any discipline worksheet that had deletions.

    Returns {"removed": [...], "not_found": [...]}.
    """
    gc = gspread.service_account(filename=config.creds_path)
    sh = gc.open_by_url(config.output_sheet_url)

    names_lower = {normalize_name(n): n for n in names}
    removed: set[str] = set()

    for ws in sh.worksheets():
        all_values = ws.get_all_values()
        if not all_values:
            continue
        header = all_values[0]
        name_col = next((i for i, h in enumerate(header) if h.strip() == "Name"), None)
        if name_col is None:
            continue

        rows_to_delete: list[int] = []  # 1-based sheet row numbers
        for row_i, row in enumerate(all_values[1:], 2):
            cell = normalize_name(row[name_col].strip()) if len(row) > name_col else ""
            if cell in names_lower:
                rows_to_delete.append(row_i)
                removed.add(names_lower[cell])

        for row_i in sorted(rows_to_delete, reverse=True):
            ws.delete_rows(row_i)

        if rows_to_delete and ws.title != FENCERS_WORKSHEET:
            recalculate_seeds(ws)

    not_found = [n for n in names if normalize_name(n) not in {normalize_name(r) for r in removed}]
    logger.info("Removed from sheets: %s; not found: %s", list(removed), not_found)
    return {"removed": list(removed), "not_found": not_found}


@observe(capture_input=False, capture_output=False)
def upload_results(
    fencers: list[FencerRecord],
    ratings: dict[int, dict[str, FencerRating]],
    config: RegConfig,
) -> None:
    """Upload enriched fencer data to the output Google Sheet."""
    if not config.output_sheet_url:
        raise ValueError("output_sheet_url is not set in user config — set it before uploading.")

    # Filter out withdrawn fencers so re-running never re-adds them.
    withdrawn = load_withdrawn(config.data_dir)
    if withdrawn:
        withdrawn_names = {w.name.lower() for w in withdrawn}
        withdrawn_ids = {w.hr_id for w in withdrawn if w.hr_id is not None}
        before = len(fencers)
        fencers = [
            f for f in fencers
            if f.name.lower() not in withdrawn_names
            and (f.hr_id is None or f.hr_id not in withdrawn_ids)
        ]
        logger.info("Skipped %d withdrawn fencer(s)", before - len(fencers))

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
        recalculate_seeds(sh.worksheet(discipline))
        logger.info(f"{discipline} done")

    logger.info("Upload complete")