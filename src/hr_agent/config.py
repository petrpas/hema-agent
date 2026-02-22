import json
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, computed_field


class Step(StrEnum):
    PARSE = "parse"    # step2: LLM parses raw CSV rows into FencerRecord objects
    MATCH = "match"    # step3: LLM fuzzy-matches fencers to HEMA Ratings profiles
    DEDUP = "dedup"    # step4: LLM merges duplicate registrations sharing the same hr_id
    HEAL = "heal"      # step5: LLM rewrites the ratings HTML parser when it breaks
    UPLOAD = "upload"  # step6: LLM agent syncs enriched data to the output Google Sheet


_STEP_DEFAULTS: dict[str, str] = {
    Step.PARSE:  "anthropic:claude-sonnet-4-6",
    Step.MATCH:  "anthropic:claude-sonnet-4-6",
    Step.DEDUP:  "anthropic:claude-sonnet-4-6",
    Step.HEAL:   "anthropic:claude-sonnet-4-6",
    Step.UPLOAD: "anthropic:claude-sonnet-4-6",
}


class Config(BaseModel):
    tournament_name: str
    registration_sheet_url: str
    output_sheet_url: str
    creds_path: str = "creds.json"
    data_root_dir: str = "data"
    ai_models: dict
    disciplines: dict
    upload_thinking_tokens: int = 0

    @computed_field
    @property
    def data_dir(self) -> Path:
        return Path(self.data_root_dir) / self.tournament_name

    def model(self, step: Step) -> str:
        """Return the model for a step.

        Priority: ai_models[step] > ai_models["default"] > _STEP_DEFAULTS[step].
        """
        if step in self.ai_models:
            return self.ai_models[step]
        if "default" in self.ai_models:
            return self.ai_models["default"]
        return _STEP_DEFAULTS.get(step, "anthropic:claude-sonnet-4-6")


def load_config(path: str) -> Config:
    with open(path) as f:
        data = json.load(f)
    return Config(**data)


def save_config(config: Config, path: str = "config.json") -> None:
    with open(path, "w") as f:
        json.dump(config.model_dump(exclude={"data_dir"}), f, indent=2)
