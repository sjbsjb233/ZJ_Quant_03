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
from urllib.parse import parse_qsl, urlencode

from ..config import get_settings
from ..logging import app_logger

# query string 中需脱敏的键 —— SSE 流 (run-log/logs) 把访问 JWT 放在 ?token= 上,
# 直接落库/写 JSONL/推 SSE 会泄漏可用的 bearer token。键名脱敏看不到 query 这层
# (它是 query 键的扁平字符串值),故在记录前对 query 自身做值级脱敏。
_QUERY_REDACT_KEYS = {"token", "access_token", "refresh_token", "secret", "password"}


def _scrub_query(query_string: str) -> str | None:
    """对 query string 做值级脱敏,敏感键的值替换为 ***。"""
    if not query_string:
        return None
    pairs = parse_qsl(query_string, keep_blank_values=True)
    if not pairs:
        return query_string
    scrubbed = [(k, "***" if k.lower() in _QUERY_REDACT_KEYS else v) for k, v in pairs]
    return urlencode(scrubbed)

_SKIP_EXACT = {
    "/api/v1/health",
    "/api/v1/health/deps",   # 状态栏每 30s/用户轮询的健康聚合 (修复 A1) — 同属 plumbing
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
        query = _scrub_query(scope.get("query_string", b"").decode("latin-1"))

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
