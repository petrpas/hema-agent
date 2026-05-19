"""pool_alch core — solve / validate / write / render, no Discord."""

from types import SimpleNamespace

from pre_tournament.cli.errors import ArtifactMissing, StepFailed
from pre_tournament.cli.steps._base import StepResult, require_remote, timed
from pre_tournament.pool_alch_agent.models import PoolConfig, PoolFencer, Weights
from pre_tournament.pool_alch_agent.loader import load_discipline
from pre_tournament.pool_alch_agent.validator import validate
from pre_tournament.pool_alch_agent.solver import solve, score as compute_score
from pre_tournament.pool_alch_agent.writer import write_pools_sheet
from pre_tournament.pool_alch_agent.renderer import render_pools, export_pools_csv
from pre_tournament.pool_alch_agent.state import (
    STATE_FILE,
    load_state,
    _fencer_from_s,
)


def _pool_config_from_args(args) -> PoolConfig | None:
    if not args.num_pools:
        return None
    if args.waves:
        waves = [int(x) for x in args.waves.split(",") if x.strip()]
    else:
        waves = [args.num_pools]
    parallel = (
        [int(x) for x in args.parallel_waves.split(",") if x.strip()]
        if getattr(args, "parallel_waves", None) else []
    )
    if sum(waves) != args.num_pools:
        raise StepFailed(
            f"--waves {waves} sum to {sum(waves)} but --num-pools is {args.num_pools}"
        )
    return PoolConfig(num_pools=args.num_pools, wave_sizes=waves, parallel_waves=parallel)


def _load_fencers(args, config) -> tuple[list[PoolFencer], list[str], object | None]:
    """Return (fencers, warnings, state) from persisted state or the sheet."""
    if getattr(args, "from_state", False):
        st = load_state(config)
        if st is None or st.discipline != args.discipline:
            raise ArtifactMissing(
                f"no saved pool state for '{args.discipline}' — run `pool-solve` first"
            )
        return [_fencer_from_s(f) for f in st.fencers], [], st
    require_remote(args, "pool load (Google Sheets read)")
    fencers, warnings = load_discipline(config, args.discipline)
    return fencers, warnings, None


def cmd_pool_validate(args, config) -> StepResult:
    fencers, warnings, _ = _load_fencers(args, config)
    pc = _pool_config_from_args(args)
    res = StepResult(step="pool-validate")
    with timed(res):
        issues = validate(fencers, pc)
    res.ok = not issues
    res.summary = f"{len(fencers)} fencers — {len(issues)} issue(s)"
    res.details = {f"issue {i + 1}": str(x) for i, x in enumerate(issues)}
    res.warnings = warnings
    return res


def _save_state(config, discipline, fencers, pc, weights, assignment, sc) -> None:
    from pre_tournament.pool_alch_agent.state import save_state

    save_state(SimpleNamespace(
        current_discipline=discipline,
        fencers=fencers,
        validated=True,
        pool_config=pc,
        weights=weights,
        assignment=assignment,
        last_score=sc,
        config=config,
    ))


def cmd_pool_solve(args, config) -> StepResult:
    fencers, warnings, st = _load_fencers(args, config)
    pc = _pool_config_from_args(args)
    if pc is None and st is not None and st.pool_config is not None:
        pc = PoolConfig(**st.pool_config.model_dump())
    if pc is None:
        raise StepFailed("need --num-pools (and --waves), or a saved state with one")
    weights = Weights(**st.weights.model_dump()) if st is not None else Weights()

    res = StepResult(step="pool-solve")
    with timed(res):
        assignment, sc = solve(fencers, pc, weights)
        _save_state(config, args.discipline, fencers, pc, weights, assignment, sc)

    res.summary = f"solved {len(fencers)} fencers into {pc.num_pools} pools — {sc}"
    res.details = {
        "pools": pc.num_pools,
        "waves": pc.wave_sizes,
        "score_total": round(sc.total, 2),
        "snake": round(sc.snake_deviation, 2),
        "club": round(sc.club, 2),
        "nationality": round(sc.nationality, 2),
        "wave": round(sc.wave, 2),
    }
    res.warnings = warnings
    res.artifact = config.data_dir / STATE_FILE
    return res


def _assignment_from_state(args, config):
    st = load_state(config)
    if st is None or st.discipline != args.discipline or st.assignment is None:
        raise ArtifactMissing(
            f"no solved assignment for '{args.discipline}' — run `pool-solve` first"
        )
    fencers = [_fencer_from_s(f) for f in st.fencers]
    assignment = [[_fencer_from_s(f) for f in pool] for pool in st.assignment]
    pc = PoolConfig(**st.pool_config.model_dump()) if st.pool_config else None
    return fencers, assignment, pc


def cmd_pool_write(args, config) -> StepResult:
    fencers, assignment, pc = _assignment_from_state(args, config)
    if pc is None:
        raise StepFailed("saved state has no pool_config")
    require_remote(args, "pool-write (Google Sheets write)")
    res = StepResult(step="pool-write")
    with timed(res):
        url, warnings = write_pools_sheet(config, args.discipline, fencers, assignment, pc)
    res.summary = f"wrote {args.discipline}_Pools"
    res.details = {"url": url}
    res.warnings = warnings
    return res


def cmd_pool_render(args, config) -> StepResult:
    _, assignment, _ = _assignment_from_state(args, config)
    res = StepResult(step="pool-render")
    with timed(res):
        paths = render_pools(config, args.discipline, assignment)
        csv_path = export_pools_csv(config, args.discipline, assignment)
    # recompute score for visibility (no state mutation)
    res.summary = f"rendered {len(assignment)} pools → {len(paths)} file(s)"
    res.details = {
        "files": ", ".join(p.name for p in paths),
        "csv": csv_path.name,
    }
    res.artifact = next((p for p in paths if p.suffix == ".pdf"), paths[0])
    return res


# compute_score kept imported for eval/metrics reuse
__all__ = [
    "cmd_pool_validate",
    "cmd_pool_solve",
    "cmd_pool_write",
    "cmd_pool_render",
    "compute_score",
]
