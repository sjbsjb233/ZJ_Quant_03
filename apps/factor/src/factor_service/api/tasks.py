"""Task queue endpoints — list, ETA, queue stats, boost, cancel."""
from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db.models import Task
from ..deps import current_user, get_db, require_editor
from ..errors import ApiError
from ..queue import estimator, store


router = APIRouter()


def _serialize_task(t: Task, *, with_eta: bool = False, db: Session | None = None):
    d = {
        "task_id": t.task_id,
        "task_type": t.task_type,
        "lane": t.lane,
        "priority": t.priority,
        "status": t.status,
        "run_id": t.run_id,
        "factor_id": t.factor_id,
        "progress_current": t.progress_current,
        "progress_total": t.progress_total,
        "est_duration_sec": t.est_duration_sec,
        "attempt": t.attempt,
        "max_attempts": t.max_attempts,
        "worker_id": t.worker_id,
        "heartbeat_at": t.heartbeat_at.isoformat() + "Z" if t.heartbeat_at else None,
        "cancel_requested": t.cancel_requested,
        "created_at": t.created_at.isoformat() + "Z" if t.created_at else None,
        "started_at": t.started_at.isoformat() + "Z" if t.started_at else None,
        "finished_at": t.finished_at.isoformat() + "Z" if t.finished_at else None,
        "error": t.error,
        "initiated_by": t.initiated_by,
    }
    if with_eta and db is not None and t.status == "queued":
        d["eta"] = estimator.estimate_wait(db, t)
    # Running runtime / remaining
    if t.status == "running" and t.started_at:
        used = (dt.datetime.utcnow() - t.started_at).total_seconds()
        d["runtime_sec"] = round(used)
        if t.est_duration_sec > 0:
            if t.progress_total > 0 and t.progress_current > 0:
                # remaining = used * (total-current)/current  (rate-based)
                ratio = max(0.0, (t.progress_total - t.progress_current) / max(1, t.progress_current))
                d["remaining_sec"] = round(used * ratio)
            else:
                d["remaining_sec"] = round(max(0.0, t.est_duration_sec - used))
    return d


@router.get("/tasks")
def list_tasks(
    lane: str | None = None,
    status: str | None = None,
    factor_id: str | None = None,
    initiated_by: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    user=Depends(current_user),
) -> dict:
    q = db.query(Task)
    if lane:
        q = q.filter(Task.lane == lane)
    if status:
        q = q.filter(Task.status == status)
    if factor_id:
        q = q.filter(Task.factor_id == factor_id)
    if initiated_by:
        q = q.filter(Task.initiated_by == initiated_by)
    rows = (
        q.order_by(Task.status.desc(), Task.priority.desc(), Task.created_at.desc())
        .limit(limit)
        .all()
    )
    items = [_serialize_task(t, with_eta=True, db=db) for t in rows]
    return {"items": items, "total": len(items)}


@router.get("/tasks/{task_id}")
def get_task(
    task_id: str, db: Session = Depends(get_db), user=Depends(current_user)
) -> dict:
    t = db.get(Task, task_id)
    if t is None:
        raise ApiError("TASK_NOT_FOUND", f"任务 {task_id} 不存在")
    return _serialize_task(t, with_eta=True, db=db)


@router.get("/tasks/{task_id}/eta")
def task_eta(
    task_id: str, db: Session = Depends(get_db), user=Depends(current_user)
) -> dict:
    t = db.get(Task, task_id)
    if t is None:
        raise ApiError("TASK_NOT_FOUND", f"任务 {task_id} 不存在")
    if t.status != "queued":
        return {"task_id": task_id, "status": t.status, "eta": None}
    return {"task_id": task_id, "status": "queued", "eta": estimator.estimate_wait(db, t)}


@router.post("/tasks/{task_id}/boost")
def boost_task(
    task_id: str,
    db: Session = Depends(get_db),
    user=Depends(require_editor),
) -> dict:
    t = store.boost(db, task_id)
    if t is None:
        raise ApiError("TASK_NOT_FOUND", f"任务 {task_id} 不存在")
    return _serialize_task(t, with_eta=True, db=db)


@router.post("/tasks/{task_id}/cancel")
def cancel_task(
    task_id: str,
    db: Session = Depends(get_db),
    user=Depends(require_editor),
) -> dict:
    t = store.request_cancel(db, task_id)
    if t is None:
        raise ApiError("TASK_NOT_FOUND", f"任务 {task_id} 不存在")
    return _serialize_task(t, db=db)


@router.get("/queue/stats")
def queue_stats(
    db: Session = Depends(get_db), user=Depends(current_user)
) -> dict:
    return estimator.queue_stats(db)
