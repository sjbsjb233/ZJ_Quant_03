"""ASGI trace-id middleware (mirrors web's TraceIdMiddleware).

  - Read X-Trace-Id from request; mint one if absent.
  - Read X-Forwarded-User / X-Forwarded-Role (when proxied through web), or
    decode Authorization Bearer token directly when called outside the web.
  - Stamp ContextVars for the duration of the request.
  - Echo X-Trace-Id back on response headers.
"""
from __future__ import annotations

import jwt
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import get_settings
from .logging_ctx import (
    actor_var,
    new_trace_id,
    role_var,
    trace_id_var,
)


class TraceIdMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        s = get_settings()
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        tid = headers.get("x-trace-id") or new_trace_id()
        actor = None
        role = None

        # 1) proxy-trusted headers (preferred path: web -> factor)
        if s.trust_proxy_user_headers:
            actor = headers.get("x-forwarded-user") or None
            role = headers.get("x-forwarded-role") or None
        # 2) direct JWT (factor-api hit directly w/o web)
        if actor is None:
            auth = headers.get("authorization") or ""
            if auth.lower().startswith("bearer "):
                token = auth[7:]
                try:
                    payload = jwt.decode(
                        token, s.jwt_secret, algorithms=[s.jwt_algorithm]
                    )
                    actor = payload.get("sub")
                    role = payload.get("role")
                except Exception:  # noqa: BLE001
                    pass

        tid_tok = trace_id_var.set(tid)
        actor_tok = actor_var.set(actor)
        role_tok = role_var.set(role)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                hdrs = list(message.get("headers", []))
                hdrs.append((b"x-trace-id", tid.encode("latin-1")))
                message["headers"] = hdrs
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            trace_id_var.reset(tid_tok)
            actor_var.reset(actor_tok)
            role_var.reset(role_tok)
