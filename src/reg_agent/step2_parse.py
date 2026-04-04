"""Step 2: Parse raw registration CSV into clean list[Fencer] using an LLM."""

import json
import logging
import re
import time
from pathlib import Path

import pandas as pd
from config.tracing import observe
from pydantic import BaseModel
from pydantic_ai import Agent, ModelSettings

from config import RegConfig, Step
from models import FencerRecord
from msgs import render_msg
from utils import (
    load_fencers_list, save_fencers_list,
    REG_VER_DIR, REG_VER_FILE_PTN, REG_VER_FILE_REG,
    FENCERS_PARSED_FILE,
)

logger = logging.getLogger(__name__)


BATCH_SIZE = 20


class FencersBatch(BaseModel):
    fencers: list[FencerRecord]

def _call_llm(df: pd.DataFrame, config: RegConfig) -> list[FencerRecord]:

    agent = Agent(
        model=config.model(Step.PARSE),
        model_settings=ModelSettings(temperature=0.0),
        output_type=FencersBatch,
        system_prompt=render_msg("reg/step2_system_prompt", {"disciplines": json.dumps(config.disciplines)}),
        retries=3,
    )

    records = df.to_dict("records")
    total = len(records)
    fencers: list[FencerRecord] = []

    for batch_start in range(0, total, BATCH_SIZE):
        if batch_start > 0:
            time.sleep(config.batch_sleep)
        batch = records[batch_start:batch_start + BATCH_SIZE]
        end = batch_start + len(batch)
        result = agent.run_sync(
            f"Parse the following {len(batch)} registration records in order:\n\n"
            f"```json\n{json.dumps(batch, ensure_ascii=False)}\n```\n\n"
            f"Return exactly {len(batch)} FencerRecord objects in the same order."
        )
        fencers.extend(result.output.fencers)
        names = ", ".join(f.name for f in result.output.fencers)
        logger.info(f"Parsed fencers {batch_start + 1}–{end}/{total}: {names}")

    return fencers

def _csv_unchanged(new_path: Path, data_dir: Path) -> bool:
    """Return True if new_path is identical to the previous version CSV."""
    existing = sorted((data_dir / REG_VER_DIR).glob(REG_VER_FILE_PTN))
    if len(existing) < 2:
        return False

    def _ver(p: Path) -> int:
        m = re.search(REG_VER_FILE_REG, p.name)
        return int(m.group(1)) if m else -1

    prev = max((p for p in existing if p != new_path), key=_ver, default=None)
    if prev is None:
        return False
    return new_path.read_bytes() == prev.read_bytes()


@observe(capture_input=False, capture_output=False)
def parse_registrations(csv_path: Path, config: RegConfig) -> list[FencerRecord]:
    """Parse a raw registration CSV into a clean list of Fencer objects.

    Skips the LLM call if the CSV is unchanged from the previous version.
    """
    data_dir = config.data_dir

    if _csv_unchanged(csv_path, data_dir):
        logger.info(f"CSV unchanged — loading existing {FENCERS_PARSED_FILE}")
        existing = load_fencers_list(data_dir, FENCERS_PARSED_FILE)
        if existing is not None:
            return existing
        logger.warning("No existing parse found — re-parsing anyway")

    input_df = pd.read_csv(csv_path, encoding="utf-8")
    logger.info(f"Parsing {len(input_df)} registrations via LLM ...")
    fencers = _call_llm(input_df, config)

    out_path = data_dir / FENCERS_PARSED_FILE
    save_fencers_list(fencers, out_path)
    return fencers
