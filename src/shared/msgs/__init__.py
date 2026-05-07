"""Generic markdown-message loader.

Each phase package owns its own `msgs/EN/...` / `msgs/CS/...` tree of
markdown files. This module provides the loader code; phase packages
bind it to their own root via `bind(root)` and re-export the resulting
`read_msg` / `render_msg` from their own `msgs/__init__.py`.
"""

from functools import lru_cache
from pathlib import Path
from typing import Callable

from jinja2 import Environment, StrictUndefined

_jinja_env = Environment(undefined=StrictUndefined, keep_trailing_newline=False)


@lru_cache(maxsize=512)
def _load_raw(root: Path, code: str, language: str) -> str:
    path = root / language / f"{code}.md"
    if not path.exists():
        if language == "EN":
            raise FileNotFoundError(f"Message not found: {path}")
        path = root / "EN" / f"{code}.md"
    return path.read_text(encoding="utf-8").rstrip("\n")


def read_msg(root: Path, code: str, language: str = "EN") -> str:
    """Load raw message string from `root/<LANG>/<code>.md`; falls back to EN."""
    return _load_raw(root, code, language.upper())


def render_msg(root: Path, code: str, values: dict, language: str = "EN") -> str:
    """Render message as Jinja2 template; falls back to EN."""
    return _jinja_env.from_string(_load_raw(root, code, language.upper())).render(**values)


def bind(root: Path) -> tuple[Callable[..., str], Callable[..., str]]:
    """Return `(read_msg, render_msg)` pre-bound to a phase's msgs root.

    Usage in `<phase>/msgs/__init__.py`:

        from pathlib import Path
        from shared.msgs import bind

        read_msg, render_msg = bind(Path(__file__).parent)
    """
    def _read(code: str, language: str = "EN") -> str:
        return read_msg(root, code, language)

    def _render(code: str, values: dict, language: str = "EN") -> str:
        return render_msg(root, code, values, language)

    return _read, _render
