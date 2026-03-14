"""Langfuse tracing — optional. When LANGFUSE_SECRET_KEY is not set, all decorators
and client calls are no-ops so the rest of the application is unaffected."""

import logging
import os

_log = logging.getLogger(__name__)

enabled = bool(os.environ.get("LANGFUSE_SECRET_KEY"))

if enabled:
    _log.info("Langfuse tracing enabled (LANGFUSE_SECRET_KEY is set).")
    from langfuse import observe as observe  # noqa: PLC0414
    from langfuse import get_client

    def get_langfuse_client():
        return get_client()
else:
    _log.info("Langfuse tracing disabled (LANGFUSE_SECRET_KEY not set).")

    def observe(func=None, **kwargs):  # type: ignore[misc]
        """No-op replacement for langfuse.observe when Langfuse is not configured."""
        if func is not None:
            return func
        return lambda f: f

    def get_langfuse_client():  # type: ignore[misc]
        return None
