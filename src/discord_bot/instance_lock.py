"""Single-instance file lock — shared between pre_bot and run_bot.

Each bot picks its own default lock path so the two can coexist on one host.
The path is overridable via env var `BOT_LOCK_FILE` for ad-hoc cases.
"""

import fcntl
import os
import sys
from pathlib import Path

_lock_fh = None  # module-level so it stays alive (GC would release the lock)


def acquire_instance_lock(default_path: str) -> None:
    """Acquire an exclusive flock on `BOT_LOCK_FILE` (or `default_path` if unset).

    Exits the process if another instance already holds the lock. The OS
    releases the lock automatically when the process dies.
    """
    global _lock_fh
    lock_path = Path(os.environ.get("BOT_LOCK_FILE", default_path))
    _lock_fh = lock_path.open("w")
    try:
        fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fh.write(str(os.getpid()))
        _lock_fh.flush()
    except BlockingIOError:
        sys.exit(f"ERROR: another bot instance is already running (lock held: {lock_path}). Exiting.")
