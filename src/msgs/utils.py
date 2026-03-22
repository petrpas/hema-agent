from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, StrictUndefined

_MSGS_DIR = Path(__file__).parent
_jinja_env = Environment(undefined=StrictUndefined, keep_trailing_newline=False)


@lru_cache(maxsize=256)
def _load_raw(code: str, language: str) -> str:
    path = _MSGS_DIR / language / f"{code}.md"
    if not path.exists():
        if language == "EN":
            raise FileNotFoundError(f"Message not found: {path}")
        path = _MSGS_DIR / "EN" / f"{code}.md"
    return path.read_text(encoding="utf-8").rstrip("\n")


def read_msg(code: str, language: str = "EN") -> str:
    """Load raw message string; falls back to EN."""
    return _load_raw(code, language.upper())


def render_msg(code: str, values: dict, language: str = "EN") -> str:
    """Render message as Jinja2 template with values; falls back to EN."""
    return _jinja_env.from_string(_load_raw(code, language.upper())).render(**values)
