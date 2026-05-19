"""Shared helpers for step command modules.

Importing this runs the sys.path shim (via artifacts → context), so bare
`step*` / `models` / `utils` imports resolve in every steps/ module.
"""

import time
from contextlib import contextmanager

from pre_tournament.cli import artifacts  # noqa: F401  (runs the shim)
from pre_tournament.cli.errors import RemoteBlocked
from pre_tournament.cli.report import StepResult

__all__ = ["artifacts", "StepResult", "timed", "require_remote"]


@contextmanager
def timed(result: StepResult):
    t0 = time.perf_counter()
    try:
        yield
    finally:
        result.elapsed_s = time.perf_counter() - t0


def require_remote(args, what: str) -> None:
    """Raise RemoteBlocked (exit 4) unless --allow-remote was passed."""
    if not getattr(args, "allow_remote", False):
        raise RemoteBlocked(
            f"{what} touches Google/network — pass --allow-remote to proceed"
        )
