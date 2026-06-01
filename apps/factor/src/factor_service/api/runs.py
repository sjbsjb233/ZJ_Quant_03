"""Run lifecycle — submit / list / get / cancel / retry / log SSE."""
from __future__ import annotations

import asyncio
import datetime as dt
import os
import uuid
from typing import Any, AsyncGenerator

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from quant_data import logging as qd_logging
from quant_factor.config import EvalConfig

from ..config import get_settings
from ..db.models import (
    FactorRecord,
    FactorRun,
    FactorRunDegradation,
    FactorVersion,
    Task,
)
from ..deps import current_user, get_db, require_editor
from ..errors import ApiError
from ..logging_ctx import trace_id_var
from ..queue import store
from ..services.preflight_service import dry_run_preflight


router = APIRouter()

# Structured logger (source="factor", carries the request trace_id). The matching
# worker-side chain (run.start → run.stage.* → run.done) is in run_executor; both
# share the same trace_id so a run reads end-to-end in app_log (V3 §4.3).
_qlog = qd_logging.get_logger("factor.runs")


@router.post("/runs")
def submit_run(
    body: dict[str, Any],
    db: Session = Depends(get_db),
    user=Depends(require_editor),
) -> dict[str, Any]:
    """Submit an evaluation run -> enqueues an `evaluation` task."""
    factor_id = body.get("factor_id")
    if not factor_id:
        raise ApiError("INVALID_PARAMETER", "factor_id 是必填项")
    rec = db.get(FactorRecord, factor_id)
    if rec is None:
        raise ApiError("FACTOR_NOT_FOUND", f"因子 {factor_id} 不存在")

    version = body.get("version")
    if not version:
        ver = (
            db.query(FactorVersion)
            .filter(FactorVersion.factor_id == factor_id, FactorVersion.enabled == True)  # noqa: E712
            .order_by(FactorVersion.id.desc())
            .first()
        )
        if ver is None:
            raise ApiError(
                "PRECONDITION_FAILED", f"因子 {factor_id} 无 enabled 版本"
            )
        version = ver.version
    else:
        ver = (
            db.query(FactorVersion)
            .filter(
                FactorVersion.factor_id == factor_id, FactorVersion.version == version
            )
            .first()
        )
        if ver is None:
            raise ApiError(
                "VERSION_NOT_FOUND", f"版本 {factor_id}@{version} 不存在"
            )
        if not ver.enabled:
            raise ApiError(
                "PRECONDITION_FAILED",
                f"版本 {factor_id}@{version} 尚未启用,请先审核启用",
            )

    run_config = body.get("run_config") or {}
    # Sanity check the config — turn ValueError into a clean 400.
    try:
        cfg = EvalConfig.from_dict(run_config)
    except (ValueError, TypeError) as e:
        raise ApiError("INVALID_PARAMETER", f"run_config 无效: {e}")
    if not (cfg.start and cfg.end):
        # Default to last year
        cfg.end = dt.date.today()
        cfg.start = cfg.end - dt.timedelta(days=365)
    if cfg.start >= cfg.end:
        raise ApiError("INVALID_PARAMETER", "start 必须早于 end")

    params = body.get("params") or {}

    # ── preflight gate at the source (设计 §10.3) ────────────────────────────
    # Refuse to create a run that would be blocked — return 422 with the
    # structured would_block so the UI can guide a fix. Preflight failures (e.g.
    # data-layer hiccup) must NOT block submission: the worker's preflight stage
    # is the authoritative backstop, so we proceed on error.
    s = get_settings()
    if s.preflight_enabled:
        try:
            pf = dry_run_preflight(
                db, factor_id=factor_id, version=version, run_config=run_config
            )
        except ValueError:
            pf = None
        except Exception as e:  # noqa: BLE001
            _qlog.warn("run.preflight.dryrun_error", factor_id=factor_id, err=str(e))
            pf = None
        if pf and pf.get("would_block"):
            _qlog.warn(
                "run.submit.blocked", factor_id=factor_id,
                checks=[b["check_id"] for b in pf["would_block"]],
            )
            raise ApiError(
                "PREFLIGHT_BLOCKED",
                "运行前置校验未通过,已阻断:请补齐数据或调整配置后重试",
                details={
                    "would_block": pf["would_block"],
                    "would_degrade": pf.get("would_degrade", []),
                },
                status_code=422,
            )

    run_id = f"frun_{uuid.uuid4().hex[:16]}"
    run = FactorRun(
        run_id=run_id,
        factor_id=factor_id,
        version=version,
        params=params,
        run_config=cfg.to_jsonable(),
        status="queued",
        initiated_by=user.username,
        trace_id=trace_id_var.get(),
    )
    db.add(run)
    db.commit()

    lane = (run_config.get("lane") or cfg.lane or "interactive")
    task = store.enqueue(
        db,
        task_type="evaluation",
        payload={
            "run_id": run_id,
            "factor_id": factor_id,
            "version": version,
            "params": params,
            "run_config": cfg.to_jsonable(),
        },
        lane=lane,
        run_id=run_id,
        factor_id=factor_id,
        initiated_by=user.username,
        trace_id=trace_id_var.get(),
    )
    run.task_id = task.task_id
    db.commit()
    _qlog.info("run.submit", run_id=run_id, factor_id=factor_id, version=version,
               task_id=task.task_id, universe=cfg.universe_id, lane=task.lane,
               **{"from": str(cfg.start), "to": str(cfg.end)})
    return {
        "run_id": run_id,
        "task_id": task.task_id,
        "status": "queued",
        "lane": task.lane,
        "est_duration_sec": int(task.est_duration_sec),
    }


@router.post("/runs/preview-eta")
def preview_eta(
    body: dict[str, Any],
    db: Session = Depends(get_db),
    user=Depends(current_user),
) -> dict[str, Any]:
    """Estimate duration + queue position BEFORE submitting (no DB write)."""
    from ..queue.estimator import estimate_duration

    task_type = body.get("task_type", "evaluation")
    payload = body.get("payload") or {}
    est = estimate_duration(db, task_type, payload)
    lane = body.get("lane", "interactive")
    priority = 100 if lane == "interactive" else 10
    # How many queued tasks in this lane with >= priority?
    ahead = (
        db.query(Task)
        .filter(
            Task.status == "queued",
            Task.lane == lane,
            Task.priority >= priority,
        )
        .count()
    )
    return {
        "est_duration_sec": int(est),
        "lane": lane,
        "queue_position": ahead + 1,
    }


@router.post("/runs/preflight")
def preflight_dry_run(
    body: dict[str, Any],
    db: Session = Depends(get_db),
    user=Depends(current_user),
) -> dict[str, Any]:
    """Dry-run the preflight gate BEFORE submitting (设计 §10.2).

    Body: {factor_id, version?, run_config}. Returns
    {would_block, would_degrade, runnable} so the UI can disable the run button
    (block) or pop a confirm (degrade) without ever queuing a doomed task.
    """
    factor_id = body.get("factor_id")
    if not factor_id:
        raise ApiError("INVALID_PARAMETER", "factor_id 是必填项")
    try:
        return dry_run_preflight(
            db,
            factor_id=factor_id,
            version=body.get("version"),
            run_config=body.get("run_config") or {},
        )
    except ValueError as e:
        raise ApiError("INVALID_PARAMETER", str(e))


@router.get("/runs")
def list_runs(
    factor_id: str | None = None,
    status: str | None = None,
    lane: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    user=Depends(current_user),
) -> dict:
    q = db.query(FactorRun)
    if factor_id:
        q = q.filter(FactorRun.factor_id == factor_id)
    if status:
        q = q.filter(FactorRun.status == status)
    rows = q.order_by(FactorRun.created_at.desc()).limit(limit).all()
    return {"items": [_serialize_run(r) for r in rows]}


@router.get("/runs/{run_id}")
def get_run(
    run_id: str, db: Session = Depends(get_db), user=Depends(current_user)
) -> dict[str, Any]:
    r = db.get(FactorRun, run_id)
    if r is None:
        raise ApiError("RUN_NOT_FOUND", f"运行 {run_id} 不存在")
    payload = _serialize_run(r)
    payload["degradation"] = _degradation_payload(db, r)
    # Live progress from the task row
    if r.task_id:
        t = db.get(Task, r.task_id)
        if t is not None:
            payload["task"] = {
                "task_id": t.task_id,
                "status": t.status,
                "lane": t.lane,
                "progress_current": t.progress_current,
                "progress_total": t.progress_total,
                "est_duration_sec": t.est_duration_sec,
                "attempt": t.attempt,
                "heartbeat_at": t.heartbeat_at.isoformat() + "Z"
                if t.heartbeat_at
                else None,
                "error": t.error,
            }
    return payload


def _serialize_run(r: FactorRun) -> dict[str, Any]:
    return {
        "run_id": r.run_id,
        "factor_id": r.factor_id,
        "version": r.version,
        "params": dict(r.params or {}),
        "run_config": dict(r.run_config or {}),
        "status": r.status,
        "task_id": r.task_id,
        "initiated_by": r.initiated_by,
        "trace_id": r.trace_id,
        "error": r.error,
        "summary": dict(r.summary or {}),
        # ── degradation derived fields (设计 §3 / §10.1) ──────────────────
        "degradation_level": getattr(r, "degradation_level", "none") or "none",
        "degradation_count": getattr(r, "degradation_count", 0) or 0,
        "has_critical": bool(getattr(r, "has_critical", False)),
        "preflight": getattr(r, "preflight", None),
        "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
        "started_at": r.started_at.isoformat() + "Z" if r.started_at else None,
        "finished_at": r.finished_at.isoformat() + "Z" if r.finished_at else None,
        "duration_sec": r.duration_sec,
    }


def _degradation_payload(db: Session, r: FactorRun) -> dict[str, Any]:
    """Assemble the run's degradation block (设计 §10.1) from the DB rows."""
    rows = (
        db.query(FactorRunDegradation)
        .filter(FactorRunDegradation.run_id == r.run_id)
        .all()
    )
    events = [
        {
            "code": d.code,
            "category": d.category,
            "severity": d.severity,
            "stage": d.stage,
            "config_key": d.config_key,
            "title": d.title,
            "reason": d.reason,
            "impact": d.impact,
            "suggested_action": d.suggested_action,
            "policy": d.policy,
            "evidence": dict(d.evidence_json or {}),
            "occurred_at": d.occurred_at,
        }
        for d in rows
        if d.policy != "block"  # block rows surface via `preflight`, not events
    ]
    return {
        "level": getattr(r, "degradation_level", "none") or "none",
        "count": getattr(r, "degradation_count", 0) or 0,
        "has_critical": bool(getattr(r, "has_critical", False)),
        "events": events,
    }


@router.post("/runs/{run_id}/cancel")
def cancel_run(
    run_id: str,
    db: Session = Depends(get_db),
    user=Depends(require_editor),
) -> dict:
    r = db.get(FactorRun, run_id)
    if r is None:
        raise ApiError("RUN_NOT_FOUND", f"运行 {run_id} 不存在")
    if r.task_id:
        store.request_cancel(db, r.task_id)
    if r.status == "queued":
        r.status = "canceled"
        db.commit()
    return {"run_id": run_id, "status": r.status}


@router.post("/runs/{run_id}/retry")
def retry_run(
    run_id: str,
    db: Session = Depends(get_db),
    user=Depends(require_editor),
) -> dict[str, Any]:
    r = db.get(FactorRun, run_id)
    if r is None:
        raise ApiError("RUN_NOT_FOUND", f"运行 {run_id} 不存在")
    # New run + new task with original config.
    new_id = f"frun_{uuid.uuid4().hex[:16]}"
    nr = FactorRun(
        run_id=new_id,
        factor_id=r.factor_id,
        version=r.version,
        params=dict(r.params or {}),
        run_config=dict(r.run_config or {}),
        status="queued",
        initiated_by=user.username,
        trace_id=trace_id_var.get(),
    )
    db.add(nr)
    db.commit()
    task = store.enqueue(
        db,
        task_type="evaluation",
        payload={
            "run_id": new_id,
            "factor_id": r.factor_id,
            "version": r.version,
            "params": dict(r.params or {}),
            "run_config": dict(r.run_config or {}),
        },
        lane=(r.run_config or {}).get("lane") or "interactive",
        run_id=new_id,
        factor_id=r.factor_id,
        initiated_by=user.username,
        trace_id=trace_id_var.get(),
    )
    nr.task_id = task.task_id
    db.commit()
    _qlog.info("run.submit", run_id=new_id, factor_id=r.factor_id, version=r.version,
               task_id=task.task_id, retried_from=run_id)
    return {"run_id": new_id, "task_id": task.task_id, "status": "queued"}


# ─────────────────────────────────────────────────────────────────────────────
@router.get("/runs/{run_id}/logs/stream")
async def stream_logs(run_id: str) -> StreamingResponse:
    """Server-sent events: tail the runs/<run_id>.log file."""
    s = get_settings()
    path = os.path.join(s.runs_dir, f"{run_id}.log")

    async def gen() -> AsyncGenerator[bytes, None]:
        yield b": connected\n\n"
        pos = 0
        for _ in range(2400):  # max ~2400 * 0.5s = 20 minutes
            if os.path.exists(path):
                with open(path, "rb") as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
                if chunk:
                    for line in chunk.splitlines():
                        if not line:
                            continue
                        yield b"data: " + line + b"\n\n"
            await asyncio.sleep(0.5)
        yield b"event: done\ndata: end-of-stream\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
