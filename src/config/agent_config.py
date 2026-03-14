"""All configuration models and loaders for the hema-agent project.

Files:
  agent_config.json  — system/AI settings (committed)
  user_config.json   — tournament-specific settings (gitignored)

Classes:
  RegAgentSystemConfig  — system settings section for agent_config.json
  AgentConfig           — root model for agent_config.json (one section per module)
  RegUserConfig         — tournament-specific user settings
  RegConfig             — combined runtime config passed to all step functions

Loaders:
  load_agent_config(path)            — loads agent_config.json → AgentConfig
  load_config(user_path, agent_path) — merges both files → RegConfig
"""

import json
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, computed_field

_DEFAULT_AGENT_CONFIG = Path(__file__).parent / "agent_config.json"
_DEFAULT_USER_CONFIG = Path(__file__).parent / "user_config.json"


# ── Step enum ──────────────────────────────────────────────────────────────────

class Step(StrEnum):
    PARSE = "parse"    # step2: LLM parses raw CSV rows into FencerRecord objects
    MATCH = "match"    # step3: LLM fuzzy-matches fencers to HEMA Ratings profiles
    DEDUP = "dedup"    # step4: LLM merges duplicate registrations sharing the same hr_id
    HEAL = "heal"      # step5: LLM rewrites the ratings HTML parser when it breaks
    UPLOAD = "upload"  # step6: LLM agent syncs enriched data to the output Google Sheet


# ── System config (agent_config.json) ─────────────────────────────────────────

class RegAgentSystemConfig(BaseModel):
    """System settings for the reg_agent module (section in agent_config.json)."""
    ai_models: dict[str, str] = {"default": "anthropic:claude-sonnet-4-6"}
    upload_thinking_tokens: int = 0
    creds_path: str = "creds.json"
    data_root_dir: str = "data"
    batch_sleep: float = 2.0  # seconds to wait between LLM batch calls

    def model(self, step: Step) -> str:
        """Return the model string for a step.

        Priority: ai_models[step] > ai_models["default"] > built-in sonnet default.
        """
        if step in self.ai_models:
            return self.ai_models[step]
        if "default" in self.ai_models:
            return self.ai_models["default"]
        return "anthropic:claude-sonnet-4-6"


class AgentConfig(BaseModel):
    """Root model for agent_config.json — one section per module."""
    reg_agent: RegAgentSystemConfig = RegAgentSystemConfig()


# ── User config (user_config.json) ────────────────────────────────────────────

class RegUserConfig(BaseModel):
    """Tournament-specific settings — loaded from user_config.json (gitignored)."""
    tournament_name: str
    language: str = "EN"
    output_sheet_url: str | None = None
    disciplines: dict = {}


# ── Combined runtime config ────────────────────────────────────────────────────

class RegConfig(RegUserConfig, RegAgentSystemConfig):
    """Runtime config passed to all step functions — merges user + system settings."""

    @computed_field
    @property
    def data_dir(self) -> Path:
        return Path(self.data_root_dir) / self.tournament_name


# ── Loaders ────────────────────────────────────────────────────────────────────

def load_agent_config(path: str | Path | None = None) -> AgentConfig:
    """Load agent_config.json; falls back to defaults if path is None or missing."""
    p = Path(path) if path else _DEFAULT_AGENT_CONFIG
    if not p.exists():
        return AgentConfig()
    with open(p) as f:
        return AgentConfig.model_validate(json.load(f))


def load_config(
    user_config_path: str | Path | None = None,
    agent_config_path: str | Path | None = None,
) -> RegConfig:
    """Load user_config.json and agent_config.json, return a merged RegConfig."""
    ucp = Path(user_config_path) if user_config_path else _DEFAULT_USER_CONFIG
    with open(ucp) as f:
        user_data: dict = json.load(f)

    system_data = load_agent_config(agent_config_path).reg_agent.model_dump()
    return RegConfig(**user_data, **system_data)


def save_config(config: RegUserConfig, path: str | Path | None = None) -> None:
    """Save user settings (tournament-specific) to user_config.json."""
    out = Path(path) if path else _DEFAULT_USER_CONFIG
    with open(out, "w") as f:
        json.dump(config.model_dump(), f, indent=2)
