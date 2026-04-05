"""Render pool assignment tables as PNG using Typst."""

import logging
import tempfile
from pathlib import Path

import typst

from pool_alch_agent.models import Assignment, PoolConfig, PoolFencer

log = logging.getLogger(__name__)

_TYPST_DIR = Path(__file__).parent.parent / "typst"
_FONTS_DIR = _TYPST_DIR / "fonts"
_TEMPLATE = _TYPST_DIR / "templates" / "pools_seed.typ"
_POOLS_SUBDIR = "lists"


def _escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("#", "\\#")


def _build_waves_block(assignment: Assignment, pool_config: PoolConfig) -> str:
    lines = ["#let waves = ("]
    for wave_idx, wave_size in enumerate(pool_config.wave_sizes):
        wave_start = pool_config.wave_start(wave_idx)
        wave_pools = assignment[wave_start : wave_start + wave_size]
        lines.append("  (")
        for pool_offset, pool in enumerate(wave_pools):
            pool_no = wave_start + pool_offset + 1
            sorted_pool = sorted(pool, key=lambda f: f.seed)
            names = ", ".join(f'"{_escape(f.name)}"' for f in sorted_pool)
            lines.append(f"    ({pool_no}, ({names},)),")
        lines.append("  ),")
    lines.append(")")
    return "\n".join(lines)


def _compile(source: str, out_path: Path) -> list[Path]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    typ_path = out_path.with_suffix(".typ")
    typ_path.write_text(source, encoding="utf-8")

    with tempfile.NamedTemporaryFile(suffix=".typ", mode="w", encoding="utf-8", delete=False) as f:
        f.write(source)
        tmp = Path(f.name)

    try:
        result = typst.compile(str(tmp), format="png", font_paths=[str(_FONTS_DIR)])
    finally:
        tmp.unlink(missing_ok=True)

    pages: list[bytes] = result if isinstance(result, list) else [result]
    if len(pages) == 1:
        out_path.write_bytes(pages[0])
        return [out_path, typ_path]

    paths: list[Path] = []
    for i, page in enumerate(pages, start=1):
        p = out_path.with_name(f"{out_path.stem}-{i}.png")
        p.write_bytes(page)
        paths.append(p)
    return [*paths, typ_path]


def render_pools_png(
    config,
    discipline_code: str,
    assignment: Assignment,
    pool_config: PoolConfig,
) -> list[Path]:
    """Render pool assignment to PNG(s) under data/{tournament}/lists/.

    Returns list of written paths (PNG pages + .typ source).
    """
    template = _TEMPLATE.read_text(encoding="utf-8")
    tournament_name = config.tournament_name.replace("_", " ").title()
    disciplines: dict[str, str] = getattr(config, "disciplines", {})
    discipline_name = disciplines.get(discipline_code, discipline_code)

    source = (
        template
        .replace("{{data}}", _build_waves_block(assignment, pool_config))
        .replace("{{tournament_name}}", _escape(tournament_name))
        .replace("{{discipline_name}}", _escape(discipline_name))
    )

    out_dir: Path = config.data_dir / _POOLS_SUBDIR
    out_path = out_dir / f"pools_{discipline_code}.png"
    paths = _compile(source, out_path)
    log.info("Rendered pool PNG(s) for %s: %s", discipline_code, [str(p) for p in paths])
    return paths
