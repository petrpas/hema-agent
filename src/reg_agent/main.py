import argparse
import logging
import sys
import time
from logging import LogRecord
from pathlib import Path

# Ensure src/ is on sys.path so the config package and step bare-imports all resolve.
_SRC = Path(__file__).parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from dotenv import load_dotenv

from config import load_config
from step1_download import download_registrations
from step2_parse import parse_registrations
from step3_match import match_fencers
from step4_dedup import deduplicate_fencers
from step5_ratings import fetch_ratings
from step6_upload import upload_results

logger = logging.getLogger(__name__)


class _DeltaFormatter(logging.Formatter):
    """Adds +Xs (seconds since previous log record) to every line."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._last: float = 0.0

    def format(self, record: LogRecord) -> str:
        delta = record.created - self._last if self._last else 0.0
        self._last = record.created
        record.delta = f"+{delta:.1f}s"
        return super().format(record)


def main() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_DeltaFormatter(
        fmt="%(asctime)s %(delta)-7s %(levelname)-8s %(name)-22s %(message)s",
        datefmt="%H:%M:%S",
    ))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    load_dotenv()

    from config.tracing import enabled as _tracing_enabled
    if _tracing_enabled:
        from pydantic_ai.agent import Agent
        Agent.instrument_all()

    parser = argparse.ArgumentParser(
        description="Enrich HEMA tournament registrations with HEMA Ratings scores.",
    )
    parser.add_argument("config", help="Path to user_config.json")
    args = parser.parse_args()

    t0 = time.perf_counter()
    logger.info("=== reg-agent starting ===")

    config = load_config(args.config)
    csv_path = download_registrations(config)
    fencers = parse_registrations(csv_path, config)
    fencers = match_fencers(fencers, config)
    fencers = deduplicate_fencers(fencers, config)
    ratings = fetch_ratings(fencers, config)
    upload_results(fencers, ratings, config)

    logger.info(f"=== done ({time.perf_counter() - t0:.1f}s) ===")


if __name__ == "__main__":
    main()