"""Per-request context (trace_id / actor / request_id / route / method).

These ``ContextVar``s are set by ``TraceIdMiddleware`` / ``RequestLogMiddleware``
and read by ``app_logger`` so every log line emitted while handling a request
is automatically stamped with the request's trace_id and actor -- the same
trace_id that is mirrored into ``quant_data.logging`` so the data layer's logs
join up too (全链路日志方案 v2.0 §3.2).
"""
from __future__ import annotations

import contextvars

trace_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("trace_id", default=None)
request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("request_id", default=None)
actor_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("actor", default=None)
route_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("route", default=None)
method_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("method", default=None)


def current_trace_id() -> str | None:
    return trace_id_var.get()
