"""Render pool assignment tables as PNG/PDF using Typst, and export CSV rosters."""

import csv
import logging
import tempfile
from pathlib import Path

import typst

from pool_alch_agent.models import Assignment, PoolFencer

log = logging.getLogger(__name__)

_TYPST_DIR = Path(__file__).parent.parent / "typst"
_FONTS_DIR = _TYPST_DIR / "fonts"
_TEMPLATE = _TYPST_DIR / "templates" / "pools_seed.typ"
_POOLS_SUBDIR = "lists"


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("#", "\\#")


def _col_count(num_pools: int) -> int:
    """Smart column count: 1 pool → 1, even → 2, odd → 3."""
    if num_pools <= 1:
        return 1
    if num_pools % 2 == 0:
        return 2
    return 3


def _build_pools_block(assignment: Assignment, pool_numbers: list[int] | None = None) -> str:
    """Build flat pool list for the Typst template.

    pool_numbers: optional explicit pool numbers (1-based). If None, uses 1..N.
    """
    n = len(assignment)
    cols = _col_count(n)

    if pool_numbers is None:
        pool_numbers = list(range(1, n + 1))

    lines = [f"#let col_count = {cols}", "#let pools = ("]
    for pool_idx, pool in enumerate(assignment):
        pool_no = pool_numbers[pool_idx]
        sorted_pool = sorted(pool, key=lambda f: f.seed)
        names = ", ".join(f'"{_escape(f.name)}"' for f in sorted_pool)
        lines.append(f"  ({pool_no}, ({names},)),")
    lines.append(")")
    return "\n".join(lines)


def _compile(source: str, out_path: Path) -> list[Path]:
    """Compile Typst source to PNG page(s) + PDF. Returns all written paths."""
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Save .typ source for debugging
    typ_path = out_path.with_suffix(".typ")
    typ_path.write_text(source, encoding="utf-8")

    with tempfile.NamedTemporaryFile(suffix=".typ", mode="w", encoding="utf-8", delete=False) as f:
        f.write(source)
        tmp = Path(f.name)

    paths: list[Path] = []
    try:
        # PNG
        result = typst.compile(str(tmp), format="png", font_paths=[str(_FONTS_DIR)])
        pages: list[bytes] = result if isinstance(result, list) else [result]
        if len(pages) == 1:
            out_path.write_bytes(pages[0])
            paths.append(out_path)
        else:
            for i, page in enumerate(pages, start=1):
                p = out_path.with_name(f"{out_path.stem}-{i}.png")
                p.write_bytes(page)
                paths.append(p)

        # PDF
        pdf_bytes = typst.compile(str(tmp), format="pdf", font_paths=[str(_FONTS_DIR)])
        pdf_path = out_path.with_suffix(".pdf")
        pdf_path.write_bytes(pdf_bytes)
        paths.append(pdf_path)
    finally:
        tmp.unlink(missing_ok=True)

    paths.append(typ_path)
    return paths


def render_pools(
    config,
    discipline_code: str,
    assignment: Assignment,
    pool_numbers: list[int] | None = None,
) -> list[Path]:
    """Render pool assignment to PNG(s) + PDF under data/{tournament}/lists/.

    Returns list of written paths (PNG pages + PDF + .typ source).
    pool_numbers: optional explicit pool numbers. If None, uses 1..N.
    """
    template = _TEMPLATE.read_text(encoding="utf-8")
    tournament_name = config.tournament_name.replace("_", " ").title()
    disciplines: dict[str, str] = getattr(config, "disciplines", {})
    discipline_name = disciplines.get(discipline_code, discipline_code)

    source = (
        template
        .replace("{{data}}", _build_pools_block(assignment, pool_numbers))
        .replace("{{tournament_name}}", _escape(tournament_name))
        .replace("{{discipline_name}}", _escape(discipline_name))
    )

    out_dir: Path = config.data_dir / _POOLS_SUBDIR
    out_path = out_dir / f"pools_{discipline_code}.png"
    paths = _compile(source, out_path)
    log.info("Rendered pool PNG+PDF for %s: %s", discipline_code, [str(p) for p in paths])
    return paths


def export_pools_csv(
    config,
    discipline_code: str,
    assignment: Assignment,
    pool_numbers: list[int] | None = None,
) -> Path:
    """Export pool rosters as CSV: name,club,nat,hrid,pool,pool_order.

    pool_numbers: optional explicit pool numbers. If None, uses 1..N.
    Returns path to the written CSV file.
    """
    if pool_numbers is None:
        pool_numbers = list(range(1, len(assignment) + 1))

    out_path = config.data_dir / f"pools_rosters_{discipline_code}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "club", "nat", "hrid", "pool", "pool_order"])
        for pool_idx, pool in enumerate(assignment):
            sorted_pool = sorted(pool, key=lambda f: f.seed)
            pool_no = pool_numbers[pool_idx]
            for order, fencer in enumerate(sorted_pool, start=1):
                writer.writerow([
                    fencer.name,
                    fencer.club or "",
                    fencer.nationality or "",
                    fencer.hr_id if fencer.hr_id is not None else "",
                    pool_no,
                    order,
                ])

    log.info("Exported CSV roster for %s: %s", discipline_code, out_path)
    return out_path
