"""Output rendering: human text + machine JSON, plus shared logging setup.

In --format json the log handler is attached to stderr so stdout stays a
single clean JSON document (automode-friendly).
"""

import json
import logging
import sys
from dataclasses import dataclass, field
from logging import LogRecord
from pathlib import Path
from typing import Any


class _DeltaFormatter(logging.Formatter):
    """Adds +Xs (seconds since previous log record) to every line.

    Moved here from reg_agent/main.py so the CLI and any caller share it.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._last: float = 0.0

    def format(self, record: LogRecord) -> str:
        delta = record.created - self._last if self._last else 0.0
        self._last = record.created
        record.delta = f"+{delta:.1f}s"
        return super().format(record)


def setup_logging(verbosity: int, json_mode: bool) -> None:
    level = logging.WARNING
    if verbosity == 1:
        level = logging.INFO
    elif verbosity >= 2:
        level = logging.DEBUG

    handler = logging.StreamHandler(sys.stderr if json_mode else sys.stdout)
    handler.setFormatter(
        _DeltaFormatter(
            fmt="%(asctime)s %(delta)-7s %(levelname)-8s %(name)-22s %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


@dataclass
class StepResult:
    """Uniform result every command returns."""

    step: str
    ok: bool = True
    summary: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    artifact: Path | None = None
    warnings: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "step": self.step,
            "ok": self.ok,
            "summary": self.summary,
            "details": self.details,
            "artifact": str(self.artifact) if self.artifact else None,
            "warnings": self.warnings,
            "elapsed_s": round(self.elapsed_s, 2),
        }


def emit(result: StepResult, fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return

    mark = "✓" if result.ok else "✗"
    print(f"{mark} [{result.step}] {result.summary}")
    for k, v in result.details.items():
        print(f"    {k}: {v}")
    if result.artifact:
        print(f"    artifact: {result.artifact}")
    for w in result.warnings:
        print(f"    ⚠ {w}")
    if result.elapsed_s:
        print(f"    ({result.elapsed_s:.1f}s)")
