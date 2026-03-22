"""Langfuse tracing — optional. When LANGFUSE_SECRET_KEY is not set, all decorators
and client calls are no-ops so the rest of the application is unaffected."""

import logging
import os
import socket
from urllib.parse import urlparse

_log = logging.getLogger(__name__)


def _langfuse_reachable() -> bool:
    """Return True if the configured Langfuse host is reachable via TCP."""
    host_url = os.environ.get("LANGFUSE_HOST", "http://localhost:3000")
    parsed = urlparse(host_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 3000)
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        _log.warning("Langfuse server not reachable at %s:%s — tracing disabled.", host, port)
        return False


enabled = bool(os.environ.get("LANGFUSE_SECRET_KEY")) and _langfuse_reachable()

if enabled:
    _log.info("Langfuse tracing enabled (LANGFUSE_SECRET_KEY is set).")
    # Silence per-span export errors — startup check already warned if unreachable.
    logging.getLogger("opentelemetry").setLevel(logging.CRITICAL)
    from langfuse import observe as observe  # noqa: PLC0414
    from langfuse import get_client

    def get_langfuse_client():
        return get_client()
else:
    if not os.environ.get("LANGFUSE_SECRET_KEY"):
        _log.info("Langfuse tracing disabled (LANGFUSE_SECRET_KEY not set).")

    def observe(func=None, **kwargs):  # type: ignore[misc]
        """No-op replacement for langfuse.observe when Langfuse is not configured."""
        if func is not None:
            return func
        return lambda f: f

    def get_langfuse_client():  # type: ignore[misc]
        return None
