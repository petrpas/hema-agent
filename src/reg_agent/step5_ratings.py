"""Step 5: Fetch HEMA Ratings and ranks for each fencer with a known hr_id.

Uses a prepared regex parser. If it fails, the LLM rewrites it and retries.
"""

import importlib.util
import logging
import traceback
from datetime import date
from pathlib import Path
from collections.abc import Callable

import requests
from requests import HTTPError
from config.tracing import observe
from pydantic import BaseModel
from pydantic_ai import Agent, ModelSettings

from config import RegConfig, Step
from models import FencerRating, FencerRecord
from msgs import read_msg
from utils import load_ratings, save_ratings

logger = logging.getLogger(__name__)

RATINGS_BASE_URL = "https://hemaratings.com/fighters/details/"
PARSERS_DIR = Path(__file__).parent / "ratings_parser"
PARSER_PREFIX = "ratings_parser_v"
RATING_HTML_DIR_PREFIX = "rating_html_"
FIGHTER_HTML_PREFIX = "fighter_"
RATINGS_CACHE_PREFIX = "ratings_"

HEAL_SYSTEM_PROMPT = read_msg("step5_heal_system_prompt")


class HealedParser(BaseModel):
    function_source: str


def _find_latest_parser_path() -> tuple[Path, int]:
    """Return (path, version) for the highest-versioned ratings_parser_vN.py in PARSERS_DIR."""
    candidates = sorted(
        (
            (int(p.stem[len(PARSER_PREFIX):]), p)
            for p in PARSERS_DIR.glob(f"{PARSER_PREFIX}*.py")
            if p.stem[len(PARSER_PREFIX):].isdigit()
        ),
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"No parser files found in {PARSERS_DIR}")
    version, path = candidates[0]
    return path, version


def _load_parser_from_file(path: Path) -> Callable:
    """Import parse_ratings from the given .py file."""
    spec = importlib.util.spec_from_file_location("_ratings_parser", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module.parse_ratings


def _save_next_parser(source: str, current_version: int) -> Path:
    """Write healed source as ratings_parser_vN+1.py and return the path."""
    next_path = PARSERS_DIR / f"{PARSER_PREFIX}{current_version + 1}.py"
    next_path.write_text(source, encoding="utf-8")
    logger.info(f"Saved healed parser → {next_path.name}")
    return next_path


def _heal_parser(current_source: str, tb: str, html: str, current_version: int, config: RegConfig) -> tuple[Callable, str, int]:
    """Ask the LLM to rewrite the broken parser. Saves the result as the next version file."""
    logger.warning("Parser broken — asking LLM to heal ...")
    agent = Agent(
        model=config.model(Step.HEAL),
        model_settings=ModelSettings(temperature=0.0),
        output_type=HealedParser,
        system_prompt=HEAL_SYSTEM_PROMPT,
        retries=3,
    )
    result = agent.run_sync(
        f"Parser source:\n```python\n{current_source}\n```\n\n"
        f"Error:\n```\n{tb}\n```\n\n"
        f"HTML snippet (first 50000 chars):\n```html\n{html[:50_000]}\n```"
    )
    new_source = result.output.function_source
    logger.info("Parser healed")
    next_path = _save_next_parser(new_source, current_version)
    next_version = current_version + 1
    return _load_parser_from_file(next_path), new_source, next_version


def _get_fighter_html(hr_id: int, data_dir: Path) -> str:
    today = date.today().strftime("%Y_%m_%d")
    ratings_dir = data_dir / f"{RATING_HTML_DIR_PREFIX}{today}"
    ratings_dir.mkdir(parents=True, exist_ok=True)
    cache_file = ratings_dir / f"{FIGHTER_HTML_PREFIX}{hr_id}.html"
    if cache_file.exists():
        return cache_file.read_text(encoding="utf-8")
    url = f"{RATINGS_BASE_URL}{hr_id}/"
    logger.info(f"Downloading {url} ...")
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    html = resp.text
    cache_file.write_text(html, encoding="utf-8")
    logger.info(f"Downloaded hr_id={hr_id}")
    return html


@observe(capture_input=False, capture_output=False)
def fetch_ratings(fencers: list[FencerRecord], config: RegConfig) -> tuple[dict[int, dict[str, FencerRating]], set[int]]:
    """Fetch HEMA Ratings data for all fencers with a known hr_id.

    Returns (ratings, not_found) where not_found contains hr_ids that returned HTTP 404.
    """
    data_dir = config.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().strftime("%Y_%m_%d")
    cache_filename = f"{RATINGS_CACHE_PREFIX}{today}.json"
    cached = load_ratings(data_dir, cache_filename)
    if cached is not None:
        logger.info(f"Loaded cached ratings from {cache_filename}")
        return cached, set()
    cache_path = data_dir / cache_filename

    hr_ids = {f.hr_id for f in fencers if f.hr_id is not None}
    results: dict[int, dict[str, FencerRating]] = {}
    not_found: set[int] = set()

    active_discipline_codes = set(config.disciplines.keys())

    parser_path, current_version = _find_latest_parser_path()
    logger.info(f"Using parser {parser_path.name} (v{current_version})")
    current_parser = _load_parser_from_file(parser_path)
    current_source = parser_path.read_text(encoding="utf-8")

    logger.info(f"Fetching ratings for {len(hr_ids)} fighters ...")

    for hr_id in sorted(hr_ids):
        try:
            html = _get_fighter_html(hr_id, data_dir)
        except HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                logger.warning(f"hr_id={hr_id}: 404 — profile not found, skipping (rank=9999)")
                results[hr_id] = {}
                not_found.add(hr_id)
                continue
            raise
        max_retries = 3

        for attempt in range(max_retries):
            try:
                parsed = current_parser(html, hr_id)
                discipline_ratings = {
                    discipline_code: FencerRating(hr_id=hr_id, weapon=discipline_code, rating=rating, rank=rank)
                    for discipline_code, (rating, rank) in parsed.items()
                    if discipline_code in active_discipline_codes
                }
                results[hr_id] = discipline_ratings
                logger.info(f"hr_id={hr_id}: {list(discipline_ratings.keys())}")
                break
            except Exception as exc:
                if attempt == max_retries - 1:
                    logger.error(f"FAILED for hr_id={hr_id} after {max_retries} attempts: {exc}")
                    results[hr_id] = {}
                    break
                logger.warning(f"Parser error for hr_id={hr_id} (attempt {attempt + 1}): {exc} — healing ...")
                current_parser, current_source, current_version = _heal_parser(
                    current_source, traceback.format_exc(), html, current_version, config
                )

    save_ratings(results, cache_path)
    return results, not_found