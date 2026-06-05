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


def _filter_request_headers(req: Request, user) -> dict[str, str]:
    headers: dict[str, str] = {}
    for k, v in req.headers.items():
        if k.lower() in _HOP_BY_HOP:
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
        if k.lower() in _HOP_BY_HOP or k.lower() == "content-encoding":
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


@factors_proxy_router.api_route(
    "/runs{path:path}",
    methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
)
async def proxy_runs(path: str, request: Request, user=Depends(get_current_user_sse)):
    # SSE-aware auth: /runs/{id}/logs/stream is consumed by EventSource, which
    # can't set an Authorization header — accept the ?token= query param too.
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
    params = list(request.query_params.multi_items())
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

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
