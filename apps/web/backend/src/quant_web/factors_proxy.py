"""Transparent proxy to factor-api.

Why a proxy (not a direct connection from the frontend to factor-api):
  - one origin for the browser → keeps cookies / CORS simple
  - the web service is the only thing exposed publicly; factor-api stays
    inside the docker network
  - we can stamp X-Forwarded-User / -Role from the already-validated JWT,
    so factor-api doesn't need to re-verify it

This module replaces the 501 stub `factors_router` in stubs.py. Mount it
*before* the catch-all SPA route in main.py.

The proxy honors:
  - all HTTP methods, including SSE (text/event-stream) for /runs/.../logs/stream
  - request body streaming (factor-api accepts multipart uploads)
  - X-Trace-Id propagation (forwarded as-is; factor-api echoes it back)
"""
from __future__ import annotations

import logging
import os
from typing import AsyncGenerator

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse

from .deps import get_current_user, get_current_user_sse
from .errors import ApiError
from .logging.context import trace_id_var


_log = logging.getLogger(__name__)


def _factor_api_base() -> str:
    return os.environ.get("FACTOR_API_BASE", "http://factor-api:8100")


# Headers we never forward (hop-by-hop or computed by httpx)
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "accept-encoding",
}

# Identity/trace headers we (re)stamp from the validated JWT. A client must never
# be able to supply these: Starlette lowercases inbound header names, so a forged
# ``x-forwarded-role: admin`` would otherwise survive alongside our title-cased
# ``X-Forwarded-Role`` (two distinct dict keys → both go on the wire), letting a
# viewer impersonate admin on the trusting downstream. Strip them on the way in.
_STRIP_FORWARDED = {"x-forwarded-user", "x-forwarded-role", "x-trace-id"}


def _filter_request_headers(req: Request, user) -> dict[str, str]:
    headers: dict[str, str] = {}
    for k, v in req.headers.items():
        if k.lower() in _HOP_BY_HOP or k.lower() in _STRIP_FORWARDED:
            continue
        headers[k] = v
    # Stamp identity headers from the validated JWT — factor-api trusts these
    # only when factor-api's TRUST_PROXY_USER_HEADERS=true (default).
    if user is not None:
        headers["X-Forwarded-User"] = user.username
        headers["X-Forwarded-Role"] = user.role
    tid = trace_id_var.get()
    if tid:
        headers["X-Trace-Id"] = tid
    return headers


def _filter_response_headers(resp: httpx.Response) -> list[tuple[str, str]]:
    out = []
    for k, v in resp.headers.items():
        # drop hop-by-hop + content-encoding, and x-trace-id (web's TraceIdMiddleware
        # re-stamps the canonical one, so passing factor-api's echo through would
        # duplicate it — mirrors keeper_proxy).
        if k.lower() in _HOP_BY_HOP or k.lower() in ("content-encoding", "x-trace-id"):
            continue
        out.append((k, v))
    return out


factors_proxy_router = APIRouter()


@factors_proxy_router.api_route(
    "/factors{path:path}",
    methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
)
async def proxy_factors(
    path: str,
    request: Request,
    user=Depends(get_current_user),
):
    return await _do_proxy("/factors" + path, request, user)


# SSE-aware auth is confined to the GET run-log stream: EventSource can't set an
# Authorization header, so this single GET route accepts the ?token= query param.
# It must be declared *before* the generic /runs{path:path} route so it wins the
# match (安全审查 P2: token-in-URL 只允许只读 GET 流, 不得用于改写运行的方法).
@factors_proxy_router.api_route(
    "/runs/{run_id}/logs/stream",
    methods=["GET"],
)
async def proxy_run_log_stream(
    run_id: str,
    request: Request,
    user=Depends(get_current_user_sse),
):
    return await _do_proxy(f"/runs/{run_id}/logs/stream", request, user)


@factors_proxy_router.api_route(
    "/runs{path:path}",
    methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
)
async def proxy_runs(path: str, request: Request, user=Depends(get_current_user)):
    # State-changing run routes (POST /runs, /runs/{id}/cancel|retry) require a
    # header-borne JWT — the ?token= fallback is restricted to the GET stream above.
    return await _do_proxy("/runs" + path, request, user)


@factors_proxy_router.api_route(
    "/tasks{path:path}",
    methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
)
async def proxy_tasks(path: str, request: Request, user=Depends(get_current_user)):
    return await _do_proxy("/tasks" + path, request, user)


@factors_proxy_router.api_route(
    "/universe{path:path}",
    methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
)
async def proxy_universe(path: str, request: Request, user=Depends(get_current_user)):
    return await _do_proxy("/universe" + path, request, user)


@factors_proxy_router.api_route(
    "/imports{path:path}",
    methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
)
async def proxy_imports(path: str, request: Request, user=Depends(get_current_user)):
    return await _do_proxy("/imports" + path, request, user)


@factors_proxy_router.api_route(
    "/queue{path:path}",
    methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
)
async def proxy_queue(path: str, request: Request, user=Depends(get_current_user)):
    return await _do_proxy("/queue" + path, request, user)


# ── 因子篮子 (因子层重构 §12) — top-level REST prefixes on factor-api ─────────
@factors_proxy_router.api_route(
    "/baskets{path:path}",
    methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
)
async def proxy_baskets(path: str, request: Request, user=Depends(get_current_user)):
    return await _do_proxy("/baskets" + path, request, user)


@factors_proxy_router.api_route(
    "/selections{path:path}",
    methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
)
async def proxy_selections(path: str, request: Request, user=Depends(get_current_user)):
    return await _do_proxy("/selections" + path, request, user)


@factors_proxy_router.api_route(
    "/screening{path:path}",
    methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
)
async def proxy_screening(path: str, request: Request, user=Depends(get_current_user)):
    return await _do_proxy("/screening" + path, request, user)


@factors_proxy_router.api_route(
    "/screen-presets{path:path}",
    methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
)
async def proxy_presets(path: str, request: Request, user=Depends(get_current_user)):
    return await _do_proxy("/screen-presets" + path, request, user)


async def _do_proxy(path: str, request: Request, user) -> Response:
    base = _factor_api_base()
    url = base.rstrip("/") + path
    headers = _filter_request_headers(request, user)
    # Strip the access JWT from the forwarded query string: web already validated
    # it, and identity travels onward via X-Forwarded-User/-Role headers. Leaving
    # ?token= in would leak the token into factor-api's request logs (安全审查 P2).
    params = [(k, v) for k, v in request.query_params.multi_items() if k != "token"]
    body = await request.body()

    accept = request.headers.get("accept", "")
    wants_stream = "text/event-stream" in accept

    try:
        if wants_stream:
            return await _stream_response(url, request.method, headers, params, body)
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.request(
                request.method,
                url,
                content=body if body else None,
                params=params,
                headers=headers,
            )
        return Response(
            content=r.content,
            status_code=r.status_code,
            headers=dict(_filter_response_headers(r)),
            media_type=r.headers.get("content-type"),
        )
    except httpx.RequestError as e:
        # Upstream unreachable is a 503, not a 400 — "INTERNAL" isn't in
        # ERROR_STATUS so it silently fell back to 400 (looked like a client
        # error). Match keeper_proxy: a clean SERVICE_UNAVAILABLE envelope the
        # frontend can render as an error instead of "无因子数据".
        _log.error("factor-api proxy error: %s -> %s", url, e)
        raise ApiError(
            "SERVICE_UNAVAILABLE",
            f"无法连接因子服务: {e}",
            {"upstream": _factor_api_base()},
        )


async def _stream_response(
    url: str,
    method: str,
    headers: dict[str, str],
    params,
    body: bytes,
) -> StreamingResponse:
    """SSE-friendly streamed proxy — used by /runs/{id}/logs/stream."""

    async def gen() -> AsyncGenerator[bytes, None]:
        # The connection opens lazily on first iteration — by now Starlette has
        # already sent 200 + SSE headers, so an upstream ConnectError here can't
        # become a 503. Catch it and emit a terminal SSE error event so the stream
        # closes cleanly instead of raising mid-body (会触发 EventSource 重连风暴).
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    method,
                    url,
                    content=body if body else None,
                    params=params,
                    headers=headers,
                ) as r:
                    async for chunk in r.aiter_raw():
                        if not chunk:
                            continue
                        yield chunk
        except httpx.RequestError as e:
            _log.error("factor-api stream proxy error: %s -> %s", url, e)
            yield b"event: error\ndata: upstream unavailable\n\n"
            return

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
