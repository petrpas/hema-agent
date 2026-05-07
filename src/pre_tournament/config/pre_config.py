"""Pre-tournament configuration models and loaders.

Files:
  src/shared/config/agent_config.json  — system/AI settings (committed)
  pre_user_config.json                 — tournament-specific settings (gitignored)

Classes:
  PreUserConfig  — tournament-specific settings for the pre-tournament phase
  PreConfig      — combined runtime config (user + system) passed to all pre-tournament functions

Loaders:
  load_pre_config(user_path, agent_path) — merges both files → PreConfig
  save_pre_config(config, path)          — saves PreUserConfig to JSON
"""

import json
from pathlib import Path

from pydantic import BaseModel, computed_field

from shared.config.agent_config import RegAgentSystemConfig, load_agent_config

_DEFAULT_USER_CONFIG = Path(__file__).parent / "pre_user_config.json"


class PreUserConfig(BaseModel):
    """Tournament-specific settings for the pre-tournament phase."""
    tournament_name: str
    language: str = "EN"
    disciplines: dict = {}
    discipline_limits: dict[str, int] = {}
    registration_sheet_url: str | None = None
    output_sheet_url: str | None = None


class PreConfig(PreUserConfig, RegAgentSystemConfig):
    """Runtime config for the pre-tournament phase — merges user + system settings."""
    pool_alch_model: str = "anthropic:claude-sonnet-4-6"

    @computed_field
    @property
    def data_dir(self) -> Path:
        return Path(self.data_root_dir) / self.tournament_name


def load_pre_config(
    user_config_path: str | Path | None = None,
    agent_config_path: str | Path | None = None,
) -> PreConfig:
    """Load pre_user_config.json and agent_config.json, return a merged PreConfig."""
    ucp = Path(user_config_path) if user_config_path else _DEFAULT_USER_CONFIG
    with open(ucp) as f:
        user_data: dict = json.load(f)

    agent_cfg = load_agent_config(agent_config_path)
    system_data = agent_cfg.reg_agent.model_dump()
    system_data["pool_alch_model"] = agent_cfg.pool_alch_model
    return PreConfig(**user_data, **system_data)


def save_pre_config(config: PreUserConfig, path: str | Path | None = None) -> None:
    """Save pre-tournament user settings to pre_user_config.json."""
    out = Path(path) if path else _DEFAULT_USER_CONFIG
    with open(out, "w") as f:
        json.dump(config.model_dump(), f, indent=2)
