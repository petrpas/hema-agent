"""System-level configuration models and loaders (shared across all phases).

Files:
  agent_config.json  — system/AI settings (committed)

Classes:
  RegAgentSystemConfig  — system settings section for agent_config.json
  AgentConfig           — root model for agent_config.json (one section per module)

Loaders:
  load_agent_config(path) — loads agent_config.json → AgentConfig
"""

import json
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel

_DEFAULT_AGENT_CONFIG = Path(__file__).parent / "agent_config.json"


# ── Step enum (pre-tournament pipeline steps) ──────────────────────────────────

class Step(StrEnum):
    PARSE = "parse"              # step2: LLM parses raw CSV rows into FencerRecord objects
    MATCH = "match"              # step3: LLM fuzzy-matches fencers to HEMA Ratings profiles
    DEDUP = "dedup"              # step4: LLM merges duplicate registrations sharing the same hr_id
    HEAL = "heal"                # step5: LLM rewrites the ratings HTML parser when it breaks
    UPLOAD = "upload"            # step6: LLM agent syncs enriched data to the output Google Sheet
    PAYMENTS_PARSE = "payments_parse"  # step7: LLM parses raw bank export → ParsedTransaction list
    PAYMENTS_MATCH = "payments_match"  # step7: LLM matches transactions to fencers


# ── System config (agent_config.json) ─────────────────────────────────────────

class RegAgentSystemConfig(BaseModel):
    """System settings for the reg_agent module (section in agent_config.json)."""
    ai_models: dict[str, str] = {"default": "anthropic:claude-sonnet-4-6"}
    upload_thinking_tokens: int = 0
    creds_path: str = "creds.json"
    data_root_dir: str = "data"
    batch_sleep: float = 2.0  # seconds to wait between LLM batch calls
    drive_folder_url: str = ""  # Google Drive folder URL where output sheets are created

    def model(self, step: Step) -> str:
        """Return the model string for a step.

        Priority: ai_models[step] > ai_models["default"] > built-in sonnet default.
        """
        if step in self.ai_models:
            return self.ai_models[step]
        if "default" in self.ai_models:
            return self.ai_models["default"]
        return "anthropic:claude-sonnet-4-6"


class RunAgentSystemConfig(BaseModel):
    """System settings for the in-tournament run_agent module."""
    data_sheet_template_url: str = ""


class AgentConfig(BaseModel):
    """Root model for agent_config.json — one section per module."""
    reg_agent: RegAgentSystemConfig = RegAgentSystemConfig()
    run_agent: RunAgentSystemConfig = RunAgentSystemConfig()
    pool_alch_model: str = "anthropic:claude-sonnet-4-6"


# ── Loaders ────────────────────────────────────────────────────────────────────

def load_agent_config(path: str | Path | None = None) -> AgentConfig:
    """Load agent_config.json; falls back to defaults if path is None or missing."""
    p = Path(path) if path else _DEFAULT_AGENT_CONFIG
    if not p.exists():
        return AgentConfig()
    with open(p) as f:
        return AgentConfig.model_validate(json.load(f))
