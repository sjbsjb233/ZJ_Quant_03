"""ASGI trace-id middleware for data-keeper (mirrors factor_service.middleware).

  - Read X-Trace-Id from the request (web stamps it when forwarding); mint one
    if absent.
  - Read X-Forwarded-User / X-Forwarded-Role (trusted from the web proxy), or
    decode an Authorization Bearer token directly when called outside web.
  - Bind everything into the local ContextVars AND quant_data.logging so the
    data layer's write/QC logs carry the same trace_id (V3 §6.4).
  - Echo X-Trace-Id back on the response.
"""
from __future__ import annotations

import jwt
from quant_data import logging as qd_logging
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import get_settings
from .logging_ctx import actor_var, new_trace_id, role_var, trace_id_var


class TraceIdMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        s = get_settings()
        headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                   for k, v in scope.get("headers", [])}
        tid = (headers.get("x-trace-id") or new_trace_id())[:64]
        actor = None
        role = None

        # 1) proxy-trusted headers (preferred path: web -> keeper)
        if s.trust_proxy_user_headers:
            actor = headers.get("x-forwarded-user") or None
            role = headers.get("x-forwarded-role") or None
        # 2) direct JWT (keeper hit directly w/o web)
        if actor is None:
            auth = headers.get("authorization") or ""
            if auth.lower().startswith("bearer "):
                token = auth[7:]
                try:
                    payload = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
                    actor = payload.get("sub")
                    role = payload.get("role")
                except Exception:  # noqa: BLE001 - best effort
                    pass

        tid_tok = trace_id_var.set(tid)
        actor_tok = actor_var.set(actor)
        role_tok = role_var.set(role)
        qd_tok = qd_logging.bind_context(trace_id=tid, actor=actor)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                hdrs = list(message.get("headers", []))
                hdrs.append((b"x-trace-id", tid.encode("latin-1")))
                message["headers"] = hdrs
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            qd_logging.reset_context(qd_tok)
            role_var.reset(role_tok)
            actor_var.reset(actor_tok)
            trace_id_var.reset(tid_tok)
