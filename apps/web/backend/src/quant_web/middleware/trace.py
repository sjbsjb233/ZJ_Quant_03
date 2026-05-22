"""TraceIdMiddleware -- one trace_id across frontend / backend / data layer.

Implemented as *pure ASGI* (not ``BaseHTTPMiddleware``) on purpose: the context
vars must be set in the same task that invokes the downstream app so they are
visible to the route handler -- and, via anyio's context copy, to sync handlers
running in the threadpool and the ``quant_data`` calls they make.

Inbound : read ``X-Trace-Id`` (else mint ``t_`` + 16 hex), derive a per-request
          ``request_id``, best-effort decode the bearer token for the actor, and
          bind all of it into both the backend context vars and
          ``quant_data.logging`` (so the data layer stamps the same trace_id).
Outbound: echo ``X-Trace-Id`` on the response.
Always  : reset every context binding in ``finally`` (no leak across requests).
"""
from __future__ import annotations

import uuid

from quant_data import logging as qd_logging

from ..auth.security import decode_token
from ..logging import context

_HEADER = b"x-trace-id"


def _new_trace_id() -> str:
    return "t_" + uuid.uuid4().hex[:16]


def _actor_from_headers(headers: dict[bytes, bytes]) -> str | None:
    auth = headers.get(b"authorization")
    if not auth:
        return None
    try:
        scheme, _, token = auth.decode("latin-1").partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None
        payload = decode_token(token)
        if payload and payload.get("type") == "access":
            return payload.get("sub")
    except Exception:  # noqa: BLE001 - logging actor is best-effort, never fatal
        return None
    return None


class TraceIdMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        incoming = headers.get(_HEADER)
        trace_id = incoming.decode("latin-1")[:64] if incoming else _new_trace_id()
        request_id = "r_" + uuid.uuid4().hex[:16]
        actor = _actor_from_headers(headers)
        path = scope.get("path", "")
        method = scope.get("method", "")

        # Stash on scope state too: Starlette's ServerErrorMiddleware (which runs
        # the unhandled-exception handler) sits OUTSIDE this middleware, so by the
        # time it runs our finally has already reset the context vars. request.state
        # survives, so the 500 body + log still carry the request's trace_id (§5.3).
        state = scope.setdefault("state", {})
        state["trace_id"] = trace_id
        state["request_id"] = request_id
        state["actor"] = actor

        t_trace = context.trace_id_var.set(trace_id)
        t_req = context.request_id_var.set(request_id)
        t_actor = context.actor_var.set(actor)
        t_route = context.route_var.set(path)
        t_method = context.method_var.set(method)
        qd_token = qd_logging.bind_context(
            trace_id=trace_id, request_id=request_id, actor=actor
        )

        async def send_with_trace(message):
            if message["type"] == "http.response.start":
                headers_list = message.setdefault("headers", [])
                headers_list.append((_HEADER, trace_id.encode("latin-1")))
            await send(message)

        try:
            await self.app(scope, receive, send_with_trace)
        finally:
            qd_logging.reset_context(qd_token)
            context.method_var.reset(t_method)
            context.route_var.reset(t_route)
            context.actor_var.reset(t_actor)
            context.request_id_var.reset(t_req)
            context.trace_id_var.reset(t_trace)
