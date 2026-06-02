"""Log-centre API (全链路日志方案 v2.0 §6.5 / appendix B; layering V3 §4.2).

| route                       | role        | purpose                              |
|-----------------------------|-------------|--------------------------------------|
| POST /logs/client           | viewer      | frontend batch upload (rate limited) |
| GET  /logs                  | log-access* | app_log query (paged + multi-filter) |
| GET  /logs/summary          | log-access* | dashboard aggregates                 |
| GET  /logs/trace/{trace_id} | log-access* | one trace: app_log + audit_log join  |
| GET  /logs/stream           | log-access* | live tail over SSE (token via query) |
| GET  /logs/export           | log-access* | CSV / JSON Lines download            |
| POST /logs/archive          | admin       | roll old rows to gzip (destructive)  |

\\*log-access = any role with ≥1 visible ``source`` (V3 §4.2). The returned
rows are always narrowed to the caller's ``allowed_sources_for`` set, so a
viewer can never pull a layer they're not entitled to — the frontend's
``lockSource`` is UX only, the boundary is enforced here (V3 §6.2).
"""
from __future__ import annotations

import asyncio
import csv
import datetime as dt
import io
import json
import time
from collections import defaultdict, deque

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..auth.security import decode_token
from ..config import get_settings
from ..db.base import get_db
from ..db.models import AppLog, AuditLog, User
from ..deps import ALL_SOURCES, allowed_sources_for, require_admin, require_log_access
from ..errors import ApiError
from ..pagination import paginate
from . import sse
from .store import get_store

router = APIRouter(prefix="/logs", tags=["logs"])


def _scoped_source(source: str | None, user: User) -> str | None:
    """Intersect a requested ``source`` filter with the caller's allowed layers.

    admin passes through unchanged — including ``None`` (no filter) — so it sees
    every layer, present or future (matches "admin 全开", V3 §4.2). A narrowed
    role (editor/viewer) is always intersected with :func:`allowed_sources_for`
    and the result sorted for determinism; an empty intersection is a 403 — that
    intersection, not the frontend's lockSource, is the real boundary (防越权).
    """
    if user.role == "admin":
        return source
    allowed = allowed_sources_for(user)
    requested = ({s.strip() for s in source.split(",") if s.strip()}
                 if source else set(allowed))
    eff = requested & allowed
    if not eff:
        raise ApiError("PERMISSION_DENIED", "无权查看所选日志层")
    return ",".join(sorted(eff))

_LEVELS = ["DEBUG", "INFO", "WARN", "ERROR", "CRITICAL"]
_ERROR_LEVELS = ("ERROR", "CRITICAL")
_FE_CRASH_EVENTS = ("react.boundary", "window.error", "promise.unhandled")
_MAX_EXTRA_BYTES = 16 * 1024
_MAX_STACK_BYTES = 4 * 1024


# ---------------------------------------------------------------- serialise
def _to_dict(r: AppLog) -> dict:
    return {
        "id": r.id, "ts": r.ts, "level": r.level, "logger": r.logger, "event": r.event,
        "source": r.source, "trace_id": r.trace_id, "request_id": r.request_id,
        "session_id": r.session_id, "actor": r.actor, "route": r.route, "method": r.method,
        "status_code": r.status_code, "duration_ms": r.duration_ms, "message": r.message,
        "extra": r.extra, "exc_type": r.exc_type, "exc_message": r.exc_message,
        "stack": r.stack, "host": r.host,
    }


def _flush() -> None:
    """Drain the async queue so a query reflects everything emitted so far."""
    store = get_store()
    if store is not None:
        store.flush()


# ---------------------------------------------------------------- filtering
def _apply_filters(q, *, level=None, source=None, logger=None, event=None, actor=None,
                   route=None, status_code=None, min_duration_ms=None, trace_id=None,
                   session_id=None, search=None, from_=None, to=None):
    if level:
        q = q.filter(AppLog.level.in_([x.strip().upper() for x in level.split(",") if x.strip()]))
    if source:
        q = q.filter(AppLog.source.in_([x.strip() for x in source.split(",") if x.strip()]))
    if logger:
        q = q.filter(AppLog.logger.like(f"{logger.rstrip('*')}%"))
    if event:
        q = q.filter(AppLog.event.like(f"{event.rstrip('*')}%"))
    if actor:
        q = q.filter(AppLog.actor == actor)
    if route:
        q = q.filter(AppLog.route.like(f"%{route}%"))
    if status_code is not None:
        q = q.filter(AppLog.status_code == status_code)
    if min_duration_ms is not None:
        q = q.filter(AppLog.duration_ms >= min_duration_ms)
    if trace_id:
        q = q.filter(AppLog.trace_id == trace_id)
    if session_id:
        q = q.filter(AppLog.session_id == session_id)
    if search:
        like = f"%{search}%"
        q = q.filter(AppLog.message.like(like) | AppLog.event.like(like)
                     | AppLog.logger.like(like))
    if from_:
        q = q.filter(AppLog.ts >= from_)
    if to:
        q = q.filter(AppLog.ts <= to)
    return q


# ---------------------------------------------------------------- GET /logs
@router.get("")
def query_logs(
    db: Session = Depends(get_db), user: User = Depends(require_log_access),
    level: str | None = None, source: str | None = None, logger: str | None = None,
    event: str | None = None, actor: str | None = None, route: str | None = None,
    status_code: int | None = None, min_duration_ms: int | None = None,
    trace_id: str | None = None, session_id: str | None = None, search: str | None = None,
    from_: dt.datetime | None = Query(None, alias="from"),
    to: dt.datetime | None = None, page: int = 1, page_size: int = 50,
):
    _flush()
    source = _scoped_source(source, user)            # 防越权: narrow to allowed layers
    q = _apply_filters(
        db.query(AppLog), level=level, source=source, logger=logger, event=event,
        actor=actor, route=route, status_code=status_code, min_duration_ms=min_duration_ms,
        trace_id=trace_id, session_id=session_id, search=search, from_=from_, to=to,
    )
    total = q.count()
    page = max(1, page)
    page_size = max(1, min(page_size, 500))
    rows = (q.order_by(AppLog.ts.desc(), AppLog.id.desc())
            .offset((page - 1) * page_size).limit(page_size).all())
    return {"items": [_to_dict(r) for r in rows], "total": total,
            "page": page, "page_size": page_size}


# ---------------------------------------------------------------- GET /logs/summary
@router.get("/summary")
def logs_summary(db: Session = Depends(get_db), user: User = Depends(require_log_access)):
    _flush()
    now = dt.datetime.utcnow()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    since_24h = now - dt.timedelta(hours=24)

    # Narrow every aggregate to the caller's visible layers (admin → no narrowing).
    allowed = allowed_sources_for(user)
    scope = [] if allowed >= ALL_SOURCES else [AppLog.source.in_(sorted(allowed))]

    def _count(*filters) -> int:
        q = db.query(func.count(AppLog.id)).filter(AppLog.ts >= day_start)
        for f in (*scope, *filters):
            q = q.filter(f)
        return int(q.scalar() or 0)

    kpis = {
        "today_total": _count(),
        "error": _count(AppLog.level.in_(_ERROR_LEVELS)),
        "warn": _count(AppLog.level == "WARN"),
        "slow": _count(AppLog.event == "http.request.slow"),
        "5xx": _count(AppLog.status_code >= 500),
        "frontend_crash": _count(AppLog.source == "frontend",
                                 AppLog.event.in_(_FE_CRASH_EVENTS)),
    }

    # 24h hourly histogram, bucketed in SQL (SQLite strftime)
    hour = func.strftime("%Y-%m-%dT%H:00:00Z", AppLog.ts)
    rows = (db.query(hour.label("h"), AppLog.level, func.count(AppLog.id))
            .filter(AppLog.ts >= since_24h, *scope).group_by("h", AppLog.level).all())
    buckets: dict[str, dict] = {}
    for h, lvl, cnt in rows:
        b = buckets.setdefault(h, {"hour": h, "info": 0, "warn": 0, "err": 0})
        if lvl in _ERROR_LEVELS:
            b["err"] += cnt
        elif lvl == "WARN":
            b["warn"] += cnt
        else:
            b["info"] += cnt
    timeline = sorted(buckets.values(), key=lambda b: b["hour"])

    top_errors_rows = (
        db.query(AppLog.event, func.count(AppLog.id).label("c"),
                 func.max(AppLog.id).label("last_id"))
        .filter(AppLog.ts >= day_start, AppLog.level.in_(_ERROR_LEVELS), *scope)
        .group_by(AppLog.event).order_by(func.count(AppLog.id).desc()).limit(10).all()
    )
    last_traces = {}
    if top_errors_rows:
        ids = [r.last_id for r in top_errors_rows]
        for lid, tid in db.query(AppLog.id, AppLog.trace_id).filter(AppLog.id.in_(ids)).all():
            last_traces[lid] = tid
    top_errors = [{"event": e, "count": int(c), "last_trace_id": last_traces.get(lid)}
                  for e, c, lid in top_errors_rows]

    top_actors_rows = (
        db.query(AppLog.actor, func.count(AppLog.id))
        .filter(AppLog.ts >= day_start, AppLog.actor.isnot(None), *scope)
        .group_by(AppLog.actor).order_by(func.count(AppLog.id).desc()).limit(10).all()
    )
    top_actors = [{"actor": a, "count": int(c)} for a, c in top_actors_rows]

    recent = (db.query(AppLog).filter(AppLog.level.in_(_ERROR_LEVELS), *scope)
              .order_by(AppLog.ts.desc()).limit(20).all())

    store = get_store()
    health = store.health() if store else {"db_sink": "off", "file_sink": "off",
                                           "queue_size": 0, "sse_clients": 0}
    settings = get_settings()
    health["retention_days"] = settings.log_db_retention_days

    return {
        "as_of": now, "kpis": kpis, "timeline_24h": timeline,
        "top_errors": top_errors, "top_actors": top_actors,
        "recent_errors": [_to_dict(r) for r in recent], "health": health,
    }


# ---------------------------------------------------------------- GET /logs/trace/{id}
@router.get("/trace/{trace_id}")
def trace_detail(trace_id: str, db: Session = Depends(get_db),
                 user: User = Depends(require_log_access)):
    _flush()
    # The summary spans the *whole* chain (so a narrowed role still sees the true
    # total / duration / level mix), but the returned detail rows are filtered to
    # the caller's visible layers (V3 §4.2 — editor 仍可排障跨层 trace, 只看有权层明细).
    logs = (db.query(AppLog).filter(AppLog.trace_id == trace_id)
            .order_by(AppLog.ts.asc(), AppLog.id.asc()).all())
    audit = (db.query(AuditLog).filter(AuditLog.trace_id == trace_id)
             .order_by(AuditLog.occurred_at.asc()).all())
    by_level: dict[str, int] = defaultdict(int)
    for r in logs:
        by_level[r.level] += 1
    start_ts = logs[0].ts if logs else None
    end_ts = logs[-1].ts if logs else None
    duration_ms = int((end_ts - start_ts).total_seconds() * 1000) if start_ts and end_ts else None
    route = next((r.route for r in logs if r.route), None)
    method = next((r.method for r in logs if r.method), None)
    actor = next((r.actor for r in logs if r.actor), None)

    allowed = allowed_sources_for(user)
    visible = logs if allowed >= ALL_SOURCES else [r for r in logs if r.source in allowed]
    return {
        "trace_id": trace_id,
        "summary": {
            "total": len(logs), "start_ts": start_ts, "end_ts": end_ts,
            "duration_ms": duration_ms, "actor": actor, "route": route, "method": method,
            "by_level": dict(by_level),
            "visible_total": len(visible),       # how many rows the caller may actually see
        },
        "logs": [_to_dict(r) for r in visible],
        "audit": [_audit_to_dict(a) for a in audit],
    }


def _audit_to_dict(a: AuditLog) -> dict:
    return {
        "id": a.id, "actor": a.actor, "action": a.action, "target_type": a.target_type,
        "target_id": a.target_id, "request_payload": a.request_payload,
        "result_status": a.result_status, "error_message": a.error_message,
        "trace_id": a.trace_id, "ip": a.ip, "occurred_at": a.occurred_at,
    }


# ---------------------------------------------------------------- GET /logs/stream (SSE)
def _log_user_from_token(token: str | None, db: Session) -> User:
    """Resolve a log-access user from a query-param token (EventSource can't set
    headers). Any role with ≥1 visible source may tail; the source filter is
    narrowed to that role's allowed layers by the caller (V3 §4.2)."""
    if not token:
        raise ApiError("UNAUTHENTICATED", "缺少访问令牌")
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        raise ApiError("UNAUTHENTICATED", "访问令牌无效或已过期")
    user = db.query(User).filter(User.username == payload.get("sub")).first()
    if user is None or not user.is_active:
        raise ApiError("UNAUTHENTICATED", "用户不存在或已停用")
    if not allowed_sources_for(user):
        raise ApiError("PERMISSION_DENIED", "当前角色无任何日志查看权限")
    return user


@router.get("/stream")
async def stream_logs(token: str | None = None, level: str | None = None,
                      source: str | None = None, db: Session = Depends(get_db)):
    user = _log_user_from_token(token, db)
    # Narrow the live source filter to the caller's allowed layers (admin → as
    # requested, possibly None = all). A narrowed role can never tail a layer it
    # isn't entitled to, even by passing ?source=.
    scoped = _scoped_source(source, user)
    sources = {s.strip() for s in scoped.split(",")} if scoped else None
    client = sse.hub.register(level=level, sources=sources)
    if client is None:
        raise ApiError("SERVICE_UNAVAILABLE", "实时日志连接数已达上限,请稍后重试")

    async def gen():
        try:
            yield ": connected\n\n"
            while True:
                try:
                    record = await asyncio.wait_for(client.queue.get(), timeout=15)
                    yield f"data: {json.dumps(record, default=str, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"   # 15s heartbeat (doc §7.4)
        finally:
            sse.hub.unregister(client)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------------------------------------------------------- GET /logs/export
@router.get("/export")
def export_logs(
    db: Session = Depends(get_db), user: User = Depends(require_log_access),
    format: str = "jsonl", level: str | None = None, source: str | None = None,
    logger: str | None = None, event: str | None = None, actor: str | None = None,
    route: str | None = None, trace_id: str | None = None, search: str | None = None,
    from_: dt.datetime | None = Query(None, alias="from"), to: dt.datetime | None = None,
    limit: int = 50_000,
):
    _flush()
    source = _scoped_source(source, user)            # 防越权: never export forbidden layers
    q = _apply_filters(db.query(AppLog), level=level, source=source, logger=logger,
                       event=event, actor=actor, route=route, trace_id=trace_id,
                       search=search, from_=from_, to=to)
    rows = q.order_by(AppLog.ts.desc()).limit(min(limit, 200_000)).all()
    stamp = dt.datetime.utcnow().strftime("%Y%m%dT%H%M%S")

    if format == "csv":
        def gen_csv():
            buf = io.StringIO()
            cols = ["ts", "level", "source", "logger", "event", "route", "method",
                    "status_code", "duration_ms", "actor", "trace_id", "message"]
            w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
            w.writeheader(); yield buf.getvalue(); buf.seek(0); buf.truncate(0)
            for r in rows:
                d = _to_dict(r)
                d["ts"] = d["ts"].isoformat() if d["ts"] else ""
                w.writerow({k: d.get(k) for k in cols})
                yield buf.getvalue(); buf.seek(0); buf.truncate(0)
        return StreamingResponse(gen_csv(), media_type="text/csv", headers={
            "Content-Disposition": f"attachment; filename=app_log-{stamp}.csv"})

    def gen_jsonl():
        for r in rows:
            yield json.dumps(_to_dict(r), default=str, ensure_ascii=False) + "\n"
    return StreamingResponse(gen_jsonl(), media_type="application/x-ndjson", headers={
        "Content-Disposition": f"attachment; filename=app_log-{stamp}.jsonl"})


# ---------------------------------------------------------------- POST /logs/archive
@router.post("/archive")
def archive_logs(retention_days: int | None = None, _: User = Depends(require_admin)):
    """Roll app_log rows past the retention window into gzip JSONL, then delete
    them from the DB (doc §7.5). Returns a small summary."""
    store = get_store()
    if store is None:
        raise ApiError("SERVICE_UNAVAILABLE", "日志存储未就绪")
    return store.archive_old(retention_days)


# ---------------------------------------------------------------- POST /logs/client
class ClientLogBatch(BaseModel):
    records: list[dict]


# per-user sliding window for the client ingest rate limit (doc §6.5)
_rate_window: dict[str, deque] = defaultdict(deque)


def _rate_limited(actor: str, limit_per_min: int) -> bool:
    now = time.monotonic()
    win = _rate_window[actor]
    while win and now - win[0] > 60:
        win.popleft()
    if len(win) >= limit_per_min:
        return True
    win.append(now)
    return False


def _truncate(value, max_bytes: int):
    """Truncate, preserving the original type when small enough (for JSON `extra`)."""
    if value is None:
        return None
    s = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    if len(s.encode("utf-8")) <= max_bytes:
        return value
    return s[:max_bytes] + "…(truncated)"


def _truncate_text(value, max_bytes: int):
    """Coerce to str then truncate -- for the Text columns (message/exc_message/
    stack), so a client sending a non-string can't break the DB binding."""
    if value is None:
        return None
    s = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False)
    if len(s.encode("utf-8")) <= max_bytes:
        return s
    return s[:max_bytes] + "…(truncated)"


def _viewer_from_request(request: Request, token: str | None, db: Session) -> User:
    """Resolve any active user from the bearer header or a ``token`` query param.

    The query-param path exists for ``navigator.sendBeacon`` on page unload, which
    cannot set an Authorization header (doc §6.1).
    """
    raw = token
    if raw is None:
        auth = request.headers.get("authorization", "")
        scheme, _, value = auth.partition(" ")
        if scheme.lower() == "bearer":
            raw = value
    if not raw:
        raise ApiError("UNAUTHENTICATED", "未登录或缺少访问令牌")
    payload = decode_token(raw)
    if not payload or payload.get("type") != "access":
        raise ApiError("UNAUTHENTICATED", "访问令牌无效或已过期")
    user = db.query(User).filter(User.username == payload.get("sub")).first()
    if user is None or not user.is_active:
        raise ApiError("UNAUTHENTICATED", "用户不存在或已停用")
    return user


# ---------------------------------------------------------------- POST /logs/ingest
class ServiceLogBatch(BaseModel):
    records: list[dict]


# Out-of-process services may only claim these sources; anything else is coerced
# to "data" so a compromised shipper can't forge "frontend"/spoofed rows. (V3 §6.7)
#   "monitor" lets the obs-sentinel ship its OOM/restart detections as a distinct
#   layer over the same /logs/ingest path (可观测层重构方案 §5).
_ALLOWED_INGEST_SOURCES = {"data", "factor", "backend", "monitor"}


@router.post("/ingest")
def ingest_service_logs(
    body: ServiceLogBatch,
    x_log_token: str | None = Header(None, alias="X-Log-Token"),
):
    """Receive a batch of structured logs from an out-of-process service
    (data-keeper / factor-*) and fold them into the same ``app_log`` ledger,
    SSE stream and trace join as backend/frontend logs (容器化重构 V3 §6.7).

    Authenticated by a shared service token (``LOG_INGEST_TOKEN``), NOT a user
    JWT — these come from trusted containers on the private network, not browsers.
    """
    settings = get_settings()
    expected = settings.log_ingest_token
    if not expected or not x_log_token or x_log_token != expected:
        raise ApiError("UNAUTHENTICATED", "无效的日志上报令牌")
    store = get_store()
    cap = settings.client_log_max_batch * 4
    accepted = 0
    for rec in body.records[:cap]:
        if not isinstance(rec, dict):
            continue
        src = rec.get("source")
        rec["source"] = src if src in _ALLOWED_INGEST_SOURCES else "data"
        # bound the heavy text/JSON columns so a runaway service can't bloat the DB
        if rec.get("extra") is not None:
            rec["extra"] = _truncate(rec["extra"], _MAX_EXTRA_BYTES)
        if rec.get("stack") is not None:
            rec["stack"] = _truncate_text(rec["stack"], _MAX_STACK_BYTES)
        if rec.get("message") is not None:
            rec["message"] = _truncate_text(rec["message"], _MAX_EXTRA_BYTES)
        if store is not None:
            store.enqueue(rec)               # same redact + SSE + back-pressure path
            accepted += 1
    return {"accepted": accepted, "received": len(body.records)}


@router.post("/client")
def ingest_client(body: ClientLogBatch, request: Request, token: str | None = None,
                  db: Session = Depends(get_db)):
    user = _viewer_from_request(request, token, db)
    settings = get_settings()
    store = get_store()
    records = body.records[: settings.client_log_max_batch]
    accepted = 0
    ip = request.client.host if request.client else None
    for rec in records:
        if _rate_limited(user.username, settings.client_log_rate_limit_per_min):
            break
        level = str(rec.get("level", "INFO")).upper()
        if level not in _LEVELS:
            level = "INFO"
        base = rec.get("extra")
        extra = dict(base) if isinstance(base, dict) else ({} if base is None else {"value": base})
        extra["ip"] = ip
        # fold the frontend-specific context into extra (no dedicated columns)
        if rec.get("user_agent"):
            extra["user_agent"] = str(rec["user_agent"])[:255]
        if rec.get("viewport"):
            extra["viewport"] = str(rec["viewport"])[:32]
        row = {
            "ts": rec.get("ts") or dt.datetime.utcnow(),
            "level": level,
            "logger": str(rec.get("logger") or "frontend")[:64],
            "event": str(rec.get("event") or "frontend.event")[:128],
            "source": "frontend",                       # never trust client-supplied source
            "trace_id": (rec.get("trace_id") or None) and str(rec["trace_id"])[:64],
            "session_id": (rec.get("session_id") or None) and str(rec["session_id"])[:64],
            "actor": user.username,                     # never trust client-supplied actor
            "route": (rec.get("route") or None) and str(rec["route"])[:255],
            "message": _truncate_text(rec.get("message"), _MAX_EXTRA_BYTES),
            "extra": _truncate(extra, _MAX_EXTRA_BYTES),
            "exc_type": (rec.get("exc_type") or None) and str(rec["exc_type"])[:128],
            "exc_message": _truncate_text(rec.get("exc_message"), _MAX_EXTRA_BYTES),
            "stack": _truncate_text(rec.get("stack"), _MAX_STACK_BYTES),
            "duration_ms": rec.get("duration_ms") if isinstance(rec.get("duration_ms"), int) else None,
        }
        if store is not None:
            store.enqueue(row)
        accepted += 1
    return {"accepted": accepted, "received": len(body.records)}
