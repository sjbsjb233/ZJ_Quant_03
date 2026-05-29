"""Trace-id / actor context for the factor service — mirrors quant_web's
logging.context module so both services use the same idioms.
"""
from __future__ import annotations

import contextvars
import logging
import sys
import uuid

trace_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "trace_id", default=None
)
actor_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "actor", default=None
)
role_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "role", default=None
)


def current_trace_id() -> str | None:
    return trace_id_var.get()


def new_trace_id() -> str:
    return "t_" + uuid.uuid4().hex[:16]


_LOG_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    """Send structured logs to stderr; include trace_id in every line."""
    global _LOG_CONFIGURED
    if _LOG_CONFIGURED:
        return
    _LOG_CONFIGURED = True

    class _TraceFilter(logging.Filter):
        def filter(self, record):
            record.trace_id = trace_id_var.get() or "-"
            return True

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.addFilter(_TraceFilter())
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(trace_id)s %(name)s %(message)s"
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
