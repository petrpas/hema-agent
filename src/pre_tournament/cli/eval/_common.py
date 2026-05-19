"""Shared eval helpers: per-step artifact resolution + run-dir layout.

Goldens and runs live under data/{tournament}/eval/ (gitignored), matching
the plan's "data dir is mainly for evaluation results".
"""

from pathlib import Path

from pre_tournament.cli.steps._base import artifacts

# step → "module:function" (the command that produces the artifact)
STEP_HANDLER: dict[str, str] = {
    "parse": "pre_tournament.cli.steps.parse:cmd_parse",
    "match": "pre_tournament.cli.steps.match:cmd_match",
    "dedup": "pre_tournament.cli.steps.dedup:cmd_dedup",
    "ratings": "pre_tournament.cli.steps.ratings:cmd_ratings",
    "pay-match": "pre_tournament.cli.agents.payment:cmd_pay_match",
    "pool-solve": "pre_tournament.cli.agents.pool_alch:cmd_pool_solve",
}


def artifact_path(step: str, config) -> Path:
    data_dir = config.data_dir
    if step == "parse":
        return artifacts.parsed(data_dir)
    if step == "match":
        return artifacts.matched(data_dir)
    if step == "dedup":
        return artifacts.deduped(data_dir)
    if step == "ratings":
        p = artifacts.latest_ratings(data_dir)
        return p if p else data_dir / "ratings_MISSING.json"
    if step == "pay-match":
        return artifacts.payments_matched(data_dir)
    if step == "pool-solve":
        from pre_tournament.pool_alch_agent.state import STATE_FILE

        return data_dir / STATE_FILE
    raise ValueError(f"unknown eval step '{step}' — known: {sorted(STEP_HANDLER)}")


def eval_root(config) -> Path:
    return config.data_dir / "eval"


def golden_dir(config, step: str, tag: str) -> Path:
    return eval_root(config) / "golden" / step / tag


def runs_dir(config, step: str) -> Path:
    return eval_root(config) / "runs" / step
