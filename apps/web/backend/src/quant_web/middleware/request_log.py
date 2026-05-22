"""RequestLogMiddleware -- structured access logging (全链路日志方案 v2.0 §5.2.2).

Pure ASGI so it shares the trace context set by :class:`TraceIdMiddleware`
(added outermost). For every API request it logs ``http.request.received`` and,
on completion, ``http.request.completed`` -- escalating to WARN for slow
requests / 4xx and ERROR for 5xx. Non-API paths (the SPA shell + assets), health
checks, docs, the SSE stream and the client-log ingest are skipped so the log
ledger does not log its own plumbing.
"""
from __future__ import annotations

import time

from ..config import get_settings
from ..logging import app_logger

_SKIP_EXACT = {
    "/api/v1/health",
    "/api/openapi.json",
    "/api/v1/logs/stream",
    "/api/v1/logs/client",
}
_SKIP_PREFIX = ("/api/docs", "/api/redoc")


def _should_skip(path: str) -> bool:
    if not path.startswith("/api/"):
        return True  # SPA shell + static assets
    if path in _SKIP_EXACT:
        return True
    return any(path.startswith(p) for p in _SKIP_PREFIX)


class RequestLogMiddleware:
    def __init__(self, app):
        self.app = app
        self._slow_ms = get_settings().log_slow_request_ms

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or _should_skip(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "")
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        client = scope.get("client")
        ip = client[0] if client else None
        ua = headers.get(b"user-agent", b"").decode("latin-1") or None
        query = scope.get("query_string", b"").decode("latin-1") or None

        app_logger.info("http.request.received", method=method, path=path,
                        query=query, ip=ip, ua=ua)

        status_holder = {"code": None}
        start = time.perf_counter()

        async def send_capture(message):
            if message["type"] == "http.response.start":
                status_holder["code"] = message.get("status")
            await send(message)

        try:
            await self.app(scope, receive, send_capture)
        except Exception:
            duration_ms = int((time.perf_counter() - start) * 1000)
            app_logger.error("http.request.failed", method=method, route=path,
                             duration_ms=duration_ms)
            raise
        else:
            duration_ms = int((time.perf_counter() - start) * 1000)
            status = status_holder["code"] or 0
            event, level = self._classify(status, duration_ms)
            getattr(app_logger, level)(event, method=method, route=path,
                                       status_code=status, duration_ms=duration_ms)

    def _classify(self, status: int, duration_ms: int) -> tuple[str, str]:
        if status >= 500:
            return "http.request.completed", "error"
        if status >= 400:
            return "http.request.completed", "warn"
        if duration_ms > self._slow_ms:
            return "http.request.slow", "warn"
        return "http.request.completed", "info"
