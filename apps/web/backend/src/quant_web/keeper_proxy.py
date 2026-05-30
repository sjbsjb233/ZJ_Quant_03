"""Forward data **write** commands + job ops to data-keeper (容器化重构 V3 §4.4).

Read = library (web reads the lake directly via DataReader on a :ro mount).
Write = service (only data-keeper holds the :rw mount). This module mirrors
``factors_proxy``: it forwards the small set of *state-changing* data endpoints —
and all of ``/jobs/*`` (jobs now live in keeper.db) — to ``KEEPER_BASE``, stamping
the validated identity as ``X-Forwarded-User/-Role`` and propagating ``X-Trace-Id``.

Why explicit routes (not a method wildcard like factors): the data modules mix
read + write in the same path space (``POST /sources/diff`` and ``/charts/*/preview``
are *reads*). Declaring only the genuine write routes — and registering this
router *before* the local data router — shadows exactly those (path, method)
pairs while every GET and read-only POST falls through to the local :ro handlers.

Audit stays in web (V3 §4.5): on a 2xx forward we record the business action at
this boundary, so ``quant_web_audit.db`` keeps its single writer and
``/logs/trace/{id}`` still joins audit + logs.

Rollback: this router is mounted only when ``KEEPER_BASE`` is set. Unset (dev /
tests) ⇒ the local write endpoints run exactly as before.
"""
from __future__ import annotations

import json

import httpx
from fastapi import APIRouter, Depends, Request, Response

from .audit.log import Auditor
from .config import get_settings
from .deps import get_auditor, get_current_user, require_admin, require_editor
from .errors import ApiError
from .logging.context import trace_id_var

API_DATA_PREFIX = "/api/v1/data"

_HOP_BY_HOP = {
    "connection", "keep-alive", "transfer-encoding", "upgrade", "host",
    "content-length", "accept-encoding",
}


def _keeper_base() -> str:
    return get_settings().keeper_base.rstrip("/")


def _filter_request_headers(req: Request, user) -> dict[str, str]:
    headers: dict[str, str] = {}
    for k, v in req.headers.items():
        if k.lower() in _HOP_BY_HOP:
            continue
        headers[k] = v
    if user is not None:
        headers["X-Forwarded-User"] = user.username
        headers["X-Forwarded-Role"] = user.role
    tid = trace_id_var.get()
    if tid:
        headers["X-Trace-Id"] = tid
    return headers


def _filter_response_headers(resp: httpx.Response) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in resp.headers.items():
        # drop hop-by-hop + content-encoding, and x-trace-id (web's TraceIdMiddleware
        # re-stamps the canonical one, so passing keeper's through would duplicate it).
        if k.lower() in _HOP_BY_HOP or k.lower() in ("content-encoding", "x-trace-id"):
            continue
        out[k] = v
    return out


async def _call_keeper(request: Request, user, body: bytes) -> httpx.Response:
    """Forward the current request to data-keeper, preserving method/query/body."""
    keeper_path = request.url.path[len(API_DATA_PREFIX):] or "/"
    url = _keeper_base() + keeper_path
    headers = _filter_request_headers(request, user)
    params = list(request.query_params.multi_items())
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            return await client.request(
                request.method, url, content=body if body else None,
                params=params, headers=headers,
            )
    except httpx.RequestError as e:
        raise ApiError("SERVICE_UNAVAILABLE", f"无法连接数据策管服务: {e}",
                       {"upstream": _keeper_base()})


def _to_response(r: httpx.Response) -> Response:
    return Response(content=r.content, status_code=r.status_code,
                    headers=_filter_response_headers(r),
                    media_type=r.headers.get("content-type"))


def _json(r: httpx.Response) -> dict:
    try:
        return r.json()
    except Exception:  # noqa: BLE001
        return {}


async def _forward(request: Request, user, auditor: Auditor, *, action: str | None,
                   target_type: str, target_id: str | None = None,
                   event: str | None = None, payload: dict | None = None,
                   target_from_body: str | None = None,
                   target_from_result: str | None = None) -> Response:
    """Forward a write request and, on success, record the audit row in web.

    ``target_from_body`` / ``target_from_result`` name a key to pull the target_id
    from the request body / keeper response when it isn't a path param.
    """
    body = await request.body()
    parsed_body = {}
    if body:
        try:
            parsed_body = json.loads(body)
        except (ValueError, TypeError):
            parsed_body = {}
    if target_id is None and target_from_body:
        target_id = parsed_body.get(target_from_body)

    r = await _call_keeper(request, user, body)

    if 200 <= r.status_code < 300 and action:
        tid = target_id
        if tid is None and target_from_result:
            tid = _json(r).get(target_from_result)
        try:
            auditor.record(action, target_type, str(tid) if tid is not None else None,
                           payload=payload if payload is not None else (parsed_body or None),
                           event=event)
        except Exception:  # noqa: BLE001 - audit must never break the forward
            pass
    return _to_response(r)


keeper_proxy_router = APIRouter()


# ── sources ─────────────────────────────────────────────────────────────────
@keeper_proxy_router.post("/sources")
async def create_source(request: Request, user=Depends(require_editor),
                        auditor: Auditor = Depends(get_auditor)):
    return await _forward(request, user, auditor, action="source.create",
                          target_type="source", target_from_body="source_id",
                          event="source.registered")


@keeper_proxy_router.patch("/sources/{source_id}")
async def update_source(source_id: str, request: Request, user=Depends(require_editor),
                        auditor: Auditor = Depends(get_auditor)):
    return await _forward(request, user, auditor, action="source.update",
                          target_type="source", target_id=source_id, event="source.updated")


@keeper_proxy_router.delete("/sources/{source_id}")
async def deactivate_source(source_id: str, request: Request, user=Depends(require_editor),
                            auditor: Auditor = Depends(get_auditor)):
    return await _forward(request, user, auditor, action="source.deactivate",
                          target_type="source", target_id=source_id, event="source.deactivated")


# ── fields ──────────────────────────────────────────────────────────────────
@keeper_proxy_router.patch("/fields/{table}/{field}")
async def update_field(table: str, field: str, request: Request, user=Depends(require_editor),
                       auditor: Auditor = Depends(get_auditor)):
    return await _forward(request, user, auditor, action="field.update",
                          target_type="field", target_id=f"{table}.{field}")


@keeper_proxy_router.post("/fields/{table}/{field}/promote")
async def promote_field(table: str, field: str, request: Request, user=Depends(require_editor),
                        auditor: Auditor = Depends(get_auditor)):
    return await _forward(request, user, auditor, action="field.promote",
                          target_type="field", target_id=f"{table}.{field}",
                          event="field.promoted")


@keeper_proxy_router.post("/fields/{table}/{field}/deprecate")
async def deprecate_field(table: str, field: str, request: Request, user=Depends(require_editor),
                          auditor: Auditor = Depends(get_auditor)):
    return await _forward(request, user, auditor, action="field.deprecate",
                          target_type="field", target_id=f"{table}.{field}",
                          event="field.deprecated")


@keeper_proxy_router.post("/fields/{table}/{field}/migrate")
async def migrate_field(table: str, field: str, request: Request, user=Depends(require_admin),
                        auditor: Auditor = Depends(get_auditor)):
    return await _forward(request, user, auditor, action="field.migrate",
                          target_type="field", target_id=f"{table}.{field}",
                          target_from_result="job_id")


@keeper_proxy_router.post("/fields/{table}/{field}/consumers")
async def add_consumer(table: str, field: str, request: Request, user=Depends(require_editor),
                       auditor: Auditor = Depends(get_auditor)):
    return await _forward(request, user, auditor, action="field.add_consumer",
                          target_type="field", target_id=f"{table}.{field}")


# ── batches ─────────────────────────────────────────────────────────────────
@keeper_proxy_router.post("/batches/supersede")
async def supersede_batch(request: Request, user=Depends(require_editor),
                          auditor: Auditor = Depends(get_auditor)):
    return await _forward(request, user, auditor, action="batch.supersede",
                          target_type="batch", target_from_body="old_batch_id",
                          event="batch.superseded")


@keeper_proxy_router.post("/batches/{batch_id}/invalidate")
async def invalidate_batch(batch_id: str, request: Request, user=Depends(require_editor),
                           auditor: Auditor = Depends(get_auditor)):
    return await _forward(request, user, auditor, action="batch.invalidate",
                          target_type="batch", target_id=batch_id, event="batch.invalidated")


# ── quality ─────────────────────────────────────────────────────────────────
@keeper_proxy_router.patch("/quality/issues/{issue_id}")
async def acknowledge_issue(issue_id: int, request: Request, user=Depends(require_editor),
                            auditor: Auditor = Depends(get_auditor)):
    return await _forward(request, user, auditor, action="quality.acknowledge",
                          target_type="quality_issue", target_id=str(issue_id),
                          event="quality.acknowledged")


# ── maintenance ───────────────────────────────────────────────────────────── (admin)
@keeper_proxy_router.post("/maintenance/vacuum/execute")
async def vacuum_execute(request: Request, user=Depends(require_admin),
                         auditor: Auditor = Depends(get_auditor)):
    return await _forward(request, user, auditor, action="maintenance.vacuum",
                          target_type="maintenance", target_from_result="job_id",
                          event="maintenance.vacuum_executed")


@keeper_proxy_router.post("/maintenance/compact/execute")
async def compact_execute(request: Request, user=Depends(require_admin),
                          auditor: Auditor = Depends(get_auditor)):
    return await _forward(request, user, auditor, action="maintenance.compact",
                          target_type="maintenance", target_from_result="job_id",
                          event="maintenance.compact_executed")


@keeper_proxy_router.post("/maintenance/snapshot")
async def make_snapshot(request: Request, user=Depends(require_admin),
                        auditor: Auditor = Depends(get_auditor)):
    return await _forward(request, user, auditor, action="maintenance.snapshot",
                          target_type="snapshot", target_from_result="snapshot_id")


@keeper_proxy_router.delete("/maintenance/snapshots/{snapshot_id}")
async def delete_snapshot(snapshot_id: str, request: Request, user=Depends(require_admin),
                          auditor: Auditor = Depends(get_auditor)):
    return await _forward(request, user, auditor, action="maintenance.snapshot_delete",
                          target_type="snapshot", target_id=snapshot_id)


@keeper_proxy_router.post("/maintenance/refresh-stats")
async def refresh_stats(request: Request, user=Depends(require_admin),
                        auditor: Auditor = Depends(get_auditor)):
    # baseline monolith does NOT audit refresh-stats — match it (action=None → no audit row)
    return await _forward(request, user, auditor, action=None,
                          target_type="maintenance")


# ── jobs (all ops forward: jobs live in keeper.db) ───────────────────────────
# Any authenticated user may poll their own jobs (parity with the local
# jobs routes); keeper enforces per-initiator ownership.
@keeper_proxy_router.api_route("/jobs", methods=["GET", "POST"])
@keeper_proxy_router.api_route("/jobs/{path:path}", methods=["GET", "POST"])
async def proxy_jobs(request: Request, path: str = "", user=Depends(get_current_user)):
    body = await request.body()
    r = await _call_keeper(request, user, body)
    return _to_response(r)
