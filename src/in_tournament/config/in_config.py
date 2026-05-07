"""In-tournament configuration models and loaders.

Files:
  src/shared/config/agent_config.json  — system/AI settings (committed)
  in_user_config.json                  — tournament-specific settings (gitignored)

Classes:
  InUserConfig  — tournament-specific settings for the in-tournament phase
  InConfig      — combined runtime config (user + system settings)

Loaders:
  load_in_config(user_path, agent_path) — merges both files → InConfig
  save_in_config(config, path)          — saves InUserConfig to JSON
"""

import json
from pathlib import Path

from pydantic import BaseModel, computed_field

from shared.config.agent_config import load_agent_config

_DEFAULT_USER_CONFIG = Path(__file__).parent / "in_user_config.json"


class InUserConfig(BaseModel):
    """Tournament-specific settings for the in-tournament phase."""
    tournament_name: str
    language: str = "EN"
    disciplines: dict = {}
    discipline_limits: dict[str, int] = {}
    tournament_display_name: str | None = None
    data_sheet_url: str | None = None


class InConfig(InUserConfig):
    """Runtime config for the in-tournament phase."""
    data_root_dir: str = "data"
    creds_path: str = "creds.json"

    @computed_field
    @property
    def data_dir(self) -> Path:
        return Path(self.data_root_dir) / self.tournament_name


def load_in_config(
    user_config_path: str | Path | None = None,
    agent_config_path: str | Path | None = None,
) -> InConfig:
    """Load in_user_config.json and agent_config.json, return a merged InConfig."""
    ucp = Path(user_config_path) if user_config_path else _DEFAULT_USER_CONFIG
    with open(ucp) as f:
        user_data: dict = json.load(f)

    agent_cfg = load_agent_config(agent_config_path)
    system_data = {
        "data_root_dir": agent_cfg.reg_agent.data_root_dir,
        "creds_path": agent_cfg.reg_agent.creds_path,
    }
    return InConfig(**user_data, **system_data)


def save_in_config(config: InUserConfig, path: str | Path | None = None) -> None:
    """Save in-tournament user settings to in_user_config.json."""
    out = Path(path) if path else _DEFAULT_USER_CONFIG
    with open(out, "w") as f:
        json.dump(config.model_dump(), f, indent=2)
