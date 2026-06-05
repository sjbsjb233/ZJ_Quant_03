"""Task queue endpoints — list, ETA, queue stats, boost, cancel."""
from __future__ import annotations

import datetime as dt
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..db.models import FactorRun, Task
from ..deps import current_user, get_db, require_editor
from ..errors import ApiError
from ..queue import estimator, store


router = APIRouter()


def _run_info_map(db: Session, run_ids: list[str]) -> dict[str, dict]:
    """Batch-load run status + degradation for tasks (设计 §11.1 queue badges)."""
    ids = [r for r in run_ids if r]
    if not ids:
        return {}
    rows = db.query(FactorRun).filter(FactorRun.run_id.in_(ids)).all()
    return {
        r.run_id: {
            "run_status": r.status,
            "run_error": r.error,
            "degradation_level": getattr(r, "degradation_level", "none") or "none",
            "degradation_count": getattr(r, "degradation_count", 0) or 0,
            "has_critical": bool(getattr(r, "has_critical", False)),
        }
        for r in rows
    }


def _serialize_task(t: Task, *, with_eta: bool = False, db: Session | None = None,
                    run_info: dict | None = None):
    d = {
        "task_id": t.task_id,
        "task_type": t.task_type,
        "lane": t.lane,
        "priority": t.priority,
        "status": t.status,
        "run_id": t.run_id,
        "factor_id": t.factor_id,
        "brun_id": getattr(t, "brun_id", None),
        "seq_no": getattr(t, "seq_no", 0),
        "gate": getattr(t, "gate", "ready"),
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
    # Attach the linked run's degradation snapshot so the queue list can badge a
    # degraded / preflight-blocked outcome even when the *task* completed fine.
    info = (run_info or {}).get(t.run_id) if t.run_id else None
    if info:
        d.update(info)
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
    view: str | None = None,
    standalone: bool = False,
    limit: int = 100,
    db: Session = Depends(get_db),
    user=Depends(current_user),
) -> dict:
    # 组合视图 (因子层重构 §6.6): basket runs as group cards + standalone tasks.
    if view == "grouped":
        return _grouped_view(db)
    q = db.query(Task)
    # standalone=true → exclude basket-child tasks SERVER-SIDE so they don't crowd
    # the recent-N window out from under a client-side filter (live-stack regression:
    # basket_eval children buried the seeded standalone tasks in the queue's flat list).
    if standalone:
        q = q.filter(Task.brun_id.is_(None))
    if lane:
        q = q.filter(Task.lane == lane)
    if status:
        q = q.filter(Task.status == status)
    if factor_id:
        q = q.filter(Task.factor_id == factor_id)
    if initiated_by:
        q = q.filter(Task.initiated_by == initiated_by)
    rows = (
        # Recency first: a "recent tasks" view must surface live running/queued and
        # the latest completions. Ordering by status.desc() first (the old behavior)
        # let a large `success` history bury all running/queued/failed rows past the
        # limit, blanking the queue dashboard's sections once enough runs accumulated.
        q.order_by(Task.created_at.desc())
        .limit(limit)
        .all()
    )
    run_info = _run_info_map(db, [t.run_id for t in rows])
    items = [_serialize_task(t, with_eta=True, db=db, run_info=run_info) for t in rows]
    return {"items": items, "total": len(items)}


@router.get("/tasks/{task_id}")
def get_task(
    task_id: str, db: Session = Depends(get_db), user=Depends(current_user)
) -> dict:
    t = db.get(Task, task_id)
    if t is None:
        raise ApiError("TASK_NOT_FOUND", f"任务 {task_id} 不存在")
    return _serialize_task(
        t, with_eta=True, db=db, run_info=_run_info_map(db, [t.run_id])
    )


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


# ─── grouped view (因子层重构 §6.6) ──────────────────────────────────────────
def _grouped_view(db: Session) -> dict:
    from ..api.baskets import _serialize_run  # reuse child serialization
    from ..db.models import Basket, BasketRun

    runs = (
        db.query(BasketRun)
        .filter(BasketRun.status.in_(["queued", "running"]))
        .order_by(BasketRun.created_at.desc())
        .limit(40)  # cap so a large backlog can't make the grouped view O(backlog)
        .all()
    )
    done_runs = (
        db.query(BasketRun)
        .filter(BasketRun.status.in_(["success", "degraded", "partial", "failed", "canceled"]))
        .order_by(BasketRun.finished_at.desc())
        .limit(12)
        .all()
    )
    name_cache: dict[str, str] = {}

    def _grp(run: BasketRun, *, children: bool) -> dict:
        if run.basket_id not in name_cache:
            b = db.get(Basket, run.basket_id)
            name_cache[run.basket_id] = b.name if b else run.basket_id
        d = _serialize_run(db, run, with_children=children)
        d["basket_name"] = name_cache[run.basket_id]
        d["kind"] = "basket"
        d["lane"] = "daily" if run.trigger in ("tracking", "data_refingerprint") else "interactive"
        return d

    # Active groups carry children (the dashboard renders live per-item progress);
    # DONE groups do NOT — the UI only consumes `groups`, and `_children` is an
    # N+1 (~8 queries/group) that this polled endpoint pays every cycle for nothing.
    # Loading children for 12 finished runs every poll is what pushed the grouped
    # view's p95 to ~2.4s under concurrency (single-process factor-api). A done
    # group's children stay reachable on demand via GET /baskets/{id}/runs/{rid}.
    groups = [_grp(r, children=True) for r in runs]
    done_groups = [_grp(r, children=False) for r in done_runs]

    standalone_rows = (
        db.query(Task)
        .filter(Task.brun_id.is_(None))
        .order_by(Task.created_at.desc())
        .limit(60)
        .all()
    )
    run_info = _run_info_map(db, [t.run_id for t in standalone_rows])
    standalone = [_serialize_task(t, with_eta=True, db=db, run_info=run_info)
                  for t in standalone_rows]
    return {"groups": groups, "done_groups": done_groups, "standalone": standalone,
            "stats": estimator.queue_stats(db)}
