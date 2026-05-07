from pathlib import Path

from shared.msgs import bind

read_msg, render_msg = bind(Path(__file__).parent)

__all__ = ["read_msg", "render_msg"]
