"""Persistence for PoolAlchDeps — save/load to JSON on the tournament data volume."""

import dataclasses
import logging
from pathlib import Path

from pydantic import BaseModel

from pool_alch_agent.models import (
    Assignment,
    PoolConfig,
    PoolFencer,
    Score,
    Weights,
)

log = logging.getLogger(__name__)

STATE_FILE = "pool_alch_state.json"


# ── Pydantic models for serialization ─────────────────────────────────────────

class _FencerS(BaseModel):
    name: str
    seed: int
    nationality: str | None
    club: str | None
    hr_id: int | None
    other_disciplines: list[str]
    h_rating: float | None = None
    h_rank: int | None = None


class _WeightsS(BaseModel):
    snake_deviation: float = 1.0
    club: float = 10.0
    nationality: float = 3.0
    wave: float = 5.0


class _PoolConfigS(BaseModel):
    num_pools: int
    wave_sizes: list[int]
    parallel_waves: list[int] = []


class _ScoreS(BaseModel):
    snake_deviation: float
    club: float
    nationality: float
    wave: float


class PoolAlchState(BaseModel):
    discipline: str
    fencers: list[_FencerS]
    validated: bool
    pool_config: _PoolConfigS | None = None
    weights: _WeightsS = _WeightsS()
    assignment: list[list[_FencerS]] | None = None
    last_score: _ScoreS | None = None


# ── Converters ─────────────────────────────────────────────────────────────────

def _fencer_to_s(f: PoolFencer) -> _FencerS:
    return _FencerS(**dataclasses.asdict(f))


def _fencer_from_s(s: _FencerS) -> PoolFencer:
    return PoolFencer(**s.model_dump())


# ── Public API ─────────────────────────────────────────────────────────────────

def save_state(deps) -> None:
    """Persist current PoolAlchDeps to data_dir/pool_alch_state.json.

    deps is PoolAlchDeps (duck-typed to avoid circular import).
    Silently skips if no discipline is loaded yet.
    """
    if not deps.current_discipline:
        return
    try:
        state = PoolAlchState(
            discipline=deps.current_discipline,
            fencers=[_fencer_to_s(f) for f in deps.fencers],
            validated=deps.validated,
            pool_config=_PoolConfigS(**dataclasses.asdict(deps.pool_config)) if deps.pool_config else None,
            weights=_WeightsS(**dataclasses.asdict(deps.weights)),
            assignment=[[_fencer_to_s(f) for f in pool] for pool in deps.assignment] if deps.assignment else None,
            last_score=_ScoreS(**dataclasses.asdict(deps.last_score)) if deps.last_score else None,
        )
        path: Path = deps.config.data_dir / STATE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        log.debug("Pool alch state saved → %s", path)
    except Exception:
        log.exception("Failed to save pool alch state")


def load_state(config) -> PoolAlchState | None:
    """Load persisted state from data_dir/pool_alch_state.json, or None if absent."""
    path: Path = config.data_dir / STATE_FILE
    if not path.exists():
        return None
    try:
        state = PoolAlchState.model_validate_json(path.read_text(encoding="utf-8"))
        log.info("Pool alch state loaded: discipline=%s, %d fencers, validated=%s",
                 state.discipline, len(state.fencers), state.validated)
        return state
    except Exception:
        log.exception("Failed to load pool alch state from %s", path)
        return None


def deps_from_state(state: PoolAlchState, channel, config) -> "PoolAlchDeps":  # type: ignore[name-defined]
    """Reconstruct a PoolAlchDeps from persisted state."""
    from pool_alch_agent.pool_alch_agent import PoolAlchDeps  # local import avoids circularity

    fencers = [_fencer_from_s(f) for f in state.fencers]
    assignment: Assignment | None = (
        [[_fencer_from_s(f) for f in pool] for pool in state.assignment]
        if state.assignment is not None else None
    )
    return PoolAlchDeps(
        channel=channel,
        config=config,
        fencers=fencers,
        validated=state.validated,
        pool_config=PoolConfig(**state.pool_config.model_dump()) if state.pool_config else None,
        weights=Weights(**state.weights.model_dump()),
        assignment=assignment,
        last_score=Score(**state.last_score.model_dump()) if state.last_score else None,
        current_discipline=state.discipline,
    )
