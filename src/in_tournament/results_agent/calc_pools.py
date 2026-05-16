"""Pool-stage results calculation, validation, and sheet writing."""

import logging

from in_tournament.render_pools import _read_pools_from_sheet
from in_tournament.results_agent.results_agent import compute_pool_stats
from in_tournament.results_agent.sheet_io import _open_sheet, ensure_worksheets

log = logging.getLogger(__name__)


def _read_fencer_meta(sh) -> dict[str, dict]:
    """Read Fencers worksheet → {name_lower: {name, nat, club}}."""
    ws = next((w for w in sh.worksheets() if w.title.strip().lower() == "fencers"), None)
    if ws is None:
        return {}
    rows = ws.get_all_values()
    if not rows:
        return {}
    header = [h.strip().lower() for h in rows[0]]
    name_col = header.index("name") if "name" in header else None
    if name_col is None:
        return {}
    nat_col = next(
        (i for i, h in enumerate(header) if h in ("nat.", "nat", "nationality")), None
    )
    club_col = header.index("club") if "club" in header else None
    meta: dict[str, dict] = {}
    for row in rows[1:]:
        if name_col >= len(row) or not row[name_col].strip():
            continue
        name = row[name_col].strip()
        meta[name.lower()] = {
            "name": name,
            "nat": row[nat_col].strip() if nat_col is not None and nat_col < len(row) else "",
            "club": row[club_col].strip() if club_col is not None and club_col < len(row) else "",
        }
    return meta


def _clear_results_ws(sh):
    """Return the existing 'Pool Results' worksheet with data rows cleared (header kept)."""
    import gspread
    try:
        ws = sh.worksheet("Pool results")
    except gspread.WorksheetNotFound:
        raise ValueError(
            "'Pool Results' worksheet not found in the data sheet — "
            "make sure the sheet was created from the correct template"
        )
    last_row = ws.row_count
    if last_row > 1:
        ws.delete_rows(2, last_row)
    return ws


def calc_and_write_pool_results(
    sheet_url: str,
    creds_path: str,
    disc: str,
) -> tuple[list[dict], list[str]]:
    """Compute pool-stage standings, validate, and write to 'Pool Results' sheet.

    Returns (ordered_rows, warnings). Each warning is a human-readable string
    suitable for posting to the setup thread.
    """
    sh = _open_sheet(sheet_url, creds_path)
    meta = _read_fencer_meta(sh)

    raw_pools = _read_pools_from_sheet(sheet_url, creds_path)
    composition: dict[int, list[str]] = {pno: names for pno, names in raw_pools}
    all_fencers = [name for names in composition.values() for name in names]

    _, verified_ws = ensure_worksheets(sh)
    verified = verified_ws.get_all_records()
    cleared = [b for b in verified if str(b.get("Confidence", "")).strip() == ""]

    stats_list = compute_pool_stats(all_fencers, cleared)
    stat_by_name = {s["name"].strip().lower(): s for s in stats_list}

    warnings: list[str] = []

    # Per-pool checks
    for pool_no, names in sorted(composition.items()):
        expected_m = len(names) - 1
        pool_ts, pool_tr = 0, 0

        for name in names:
            s = stat_by_name.get(name.strip().lower())
            if s is None:
                warnings.append(f"Pool {pool_no}: **{name}** — no results found at all")
                continue
            if s["m"] == 0:
                warnings.append(f"Pool {pool_no}: **{name}** — no verified bouts")
            elif s["m"] != expected_m:
                warnings.append(
                    f"Pool {pool_no}: **{name}** — expected {expected_m} bouts, found {s['m']}"
                )
            pool_ts += s["ts"]
            pool_tr += s["tr"]

        if pool_ts != pool_tr:
            warnings.append(
                f"Pool {pool_no}: touch totals asymmetric — TS {pool_ts} ≠ TR {pool_tr}"
            )

    # Fencers in verified sheet not in any pool
    comp_lower = {n.strip().lower() for names in composition.values() for n in names}
    unknown: set[str] = set()
    for b in cleared:
        for key in ("Fencer1", "Fencer2"):
            fname = str(b.get(key, "")).strip()
            if fname and fname.lower() not in comp_lower:
                unknown.add(fname)
    for fname in sorted(unknown):
        warnings.append(f"Unrecognised fencer in verified sheet: **{fname}**")

    # Build ordered result rows — exclude fencers with no verified bouts
    rows: list[dict] = []
    for i, s in enumerate((s for s in stats_list if s["m"] > 0), 1):
        m_data = meta.get(s["name"].strip().lower(), {})
        vm = f"{s['vm']:.2f}" if s["m"] > 0 else "0.00"
        rows.append({
            "ord": i,
            "name": s["name"],
            "nat": m_data.get("nat", ""),
            "club": m_data.get("club", ""),
            "matches": s["m"],
            "victory": s["v"],
            "wm": vm,
            "ts": s["ts"],
            "tr": s["tr"],
            "index": s["ind"],
        })

    ws = _clear_results_ws(sh)
    data_rows = [
        [r["ord"], r["name"], r["nat"], r["club"],
         r["matches"], r["victory"], r["wm"],
         r["ts"], r["tr"], r["index"]]
        for r in rows
    ]
    if data_rows:
        ws.append_rows(data_rows)
        last = 1 + len(data_rows)
        # All data cells: centre-aligned, no bold
        ws.format(f"A2:J{last}", {
            "horizontalAlignment": "CENTER",
            "textFormat": {"bold": False},
        })
        # Name (B) and Club (D): left-aligned
        ws.format(f"B2:B{last}", {"horizontalAlignment": "LEFT"})
        ws.format(f"D2:D{last}", {"horizontalAlignment": "LEFT"})

    log.info(
        "calc_pools %s: %d fencers written, %d warning(s)",
        disc, len(rows), len(warnings),
    )
    return rows, warnings
