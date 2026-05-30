"""Trace-id / actor context for data-keeper — mirrors quant_web.logging.context
and factor_service.logging_ctx so all three services share the same idioms.

The trace_id flows end-to-end via the ``X-Trace-Id`` header (web stamps it when
forwarding a write); the scheduler mints its own per autonomous run. Both bind it
into ``quant_data.logging`` so the data layer's write/QC logs carry the same id,
which is then shipped to web-backend's ``app_log`` (容器化重构 V3 §6.4).
"""
from __future__ import annotations

import contextvars
import logging
import sys
import uuid

trace_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)
actor_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("actor", default=None)
role_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("role", default=None)


def current_trace_id() -> str | None:
    return trace_id_var.get()


def new_trace_id() -> str:
    return "t_" + uuid.uuid4().hex[:16]


_LOG_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    """stderr handler with trace_id in every line (docker logs / 取证)."""
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
        logging.Formatter("%(asctime)s %(levelname)s %(trace_id)s %(name)s %(message)s")
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
