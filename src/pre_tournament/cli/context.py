"""Build a PreConfig and the runtime environment for CLI commands.

Centralises the sys.path shim the reg_agent step files rely on (bare
`step*`, `models`, `utils` imports), .env loading, creds check, and
tournament/data-root overrides.
"""

import json
import os
import sys
from pathlib import Path

# ── sys.path shim ─────────────────────────────────────────────────────────────
# src/pre_tournament/cli/context.py → parents[2] = src/
_SRC = Path(__file__).resolve().parents[2]
_REG_AGENT = _SRC / "pre_tournament" / "reg_agent"
for _p in (_SRC, _REG_AGENT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from dotenv import load_dotenv  # noqa: E402

from shared.config.agent_config import load_agent_config  # noqa: E402
from pre_tournament.config import PreConfig  # noqa: E402

_loaded_env = False


def _ensure_env() -> None:
    global _loaded_env
    if not _loaded_env:
        load_dotenv()
        _loaded_env = True


def _resolve_user_config_path(explicit: str | None) -> Path:
    """--config → $USER_CONFIG → package default pre_user_config.json."""
    if explicit:
        return Path(explicit)
    env = os.environ.get("USER_CONFIG")
    if env:
        return Path(env)
    return _SRC / "pre_tournament" / "config" / "pre_user_config.json"


def build_config(args) -> PreConfig:
    """Construct a PreConfig, applying --tournament / --data-root overrides.

    Mirrors pre_tournament.config.load_pre_config but injects overrides into
    the user/system dicts before validation so the computed data_dir is correct.
    """
    _ensure_env()

    ucp = _resolve_user_config_path(getattr(args, "config", None))
    if not ucp.exists():
        from pre_tournament.cli.errors import ArtifactMissing

        raise ArtifactMissing(
            f"user config not found: {ucp} "
            f"(pass --config PATH or set $USER_CONFIG)"
        )
    user_data: dict = json.loads(ucp.read_text())

    agent_cfg = load_agent_config()
    system_data = agent_cfg.reg_agent.model_dump()
    system_data["pool_alch_model"] = agent_cfg.pool_alch_model

    if getattr(args, "tournament", None):
        user_data["tournament_name"] = args.tournament
    if getattr(args, "data_root", None):
        system_data["data_root_dir"] = args.data_root

    return PreConfig(**user_data, **system_data)


def creds_available(config: PreConfig) -> bool:
    return Path(config.creds_path).exists()
