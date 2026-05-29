"""Duration + ETA estimation (design §13.7).

Per-task duration:
  signature = task_type | size-bucket | rebalance | neutralize | quantiles
  -> p80(task_duration_stat[signature]) if any history, else a hardcoded default

ETA for a queued task:
  - count tasks ahead (higher priority OR same priority earlier created_at)
  - sum est_duration_sec
  - add remaining time of currently-running tasks
  - divide by concurrency (close approximation)
"""
from __future__ import annotations

import datetime as dt
import math
import os
from typing import Any

from sqlalchemy.orm import Session

from ..db.models import Task, TaskDurationStat


# ── default estimates (used when no history yet) ────────────────────────
_DEFAULTS = {
    "evaluation": 120.0,   # 2 minutes typical
    "correlation": 360.0,  # 6 minutes (N factors)
    "import_validate": 15.0,
}


def _size_bucket(symbols_hint: int | None) -> str:
    if symbols_hint is None or symbols_hint <= 0:
        return "any"
    if symbols_hint < 100:
        return "<100"
    if symbols_hint < 500:
        return "100-500"
    if symbols_hint < 2000:
        return "500-2000"
    return "2000+"


def _signature_from_payload(task_type: str, payload: dict[str, Any]) -> str:
    cfg = payload.get("run_config") or {}
    bucket = _size_bucket(payload.get("symbol_count"))
    parts = [
        task_type,
        bucket,
        str(cfg.get("rebalance", "W")),
        "n+" if cfg.get("neutralize_industry") else "n-",
        str(cfg.get("quantiles", 5)),
    ]
    if task_type == "correlation":
        n_factors = len(payload.get("factor_ids") or []) or 0
        parts.append(f"f={n_factors}")
    return "|".join(parts)


def estimate_duration(
    db: Session, task_type: str, payload: dict[str, Any]
) -> float:
    sig = _signature_from_payload(task_type, payload)
    row = db.get(TaskDurationStat, sig)
    if row is not None and row.sample_n > 0:
        return float(row.p80_sec)
    # try by task_type alone
    fallback = (
        db.query(TaskDurationStat)
        .filter(TaskDurationStat.signature.like(f"{task_type}|%"))
        .all()
    )
    if fallback:
        avg = sum(r.p80_sec for r in fallback) / len(fallback)
        return float(avg)
    return float(_DEFAULTS.get(task_type, 60.0))


# ── update duration stats after a task finishes successfully ────────────
def update_stats(
    db: Session, task_type: str, payload: dict[str, Any], duration: float
) -> None:
    sig = _signature_from_payload(task_type, payload)
    row = db.get(TaskDurationStat, sig)
    if row is None:
        row = TaskDurationStat(signature=sig, p50_sec=duration, p80_sec=duration, sample_n=1)
        db.add(row)
    else:
        n = row.sample_n
        # exponential smoothing for both percentiles (cheap & online)
        alpha = 1.0 / max(1, n)
        # treat current p80 like the upper smoothing target
        row.p50_sec = float(row.p50_sec * (1 - alpha) + duration * alpha)
        row.p80_sec = float(max(row.p80_sec * (1 - alpha) + duration * alpha, duration))
        row.sample_n = n + 1
    db.commit()


# ── ETA for a queued task ───────────────────────────────────────────────
def estimate_wait(db: Session, task: Task) -> dict[str, Any]:
    """Return queue position, est wait, est start time, est finish time."""
    now = dt.datetime.utcnow()
    concurrency = int(os.getenv("FACTOR_CONCURRENCY", "4"))

    ahead = (
        db.query(Task)
        .filter(
            Task.status == "queued",
            Task.task_id != task.task_id,
            (
                (Task.priority > task.priority)
                | (
                    (Task.priority == task.priority)
                    & (Task.created_at < task.created_at)
                )
            ),
        )
        .all()
    )
    ahead_work = sum(a.est_duration_sec for a in ahead)

    running = db.query(Task).filter(Task.status == "running").all()
    remaining = 0.0
    for r in running:
        used = (now - (r.started_at or now)).total_seconds()
        remaining += max(0.0, float(r.est_duration_sec) - used)

    est_wait = (ahead_work + remaining) / max(1, concurrency)
    est_start_at = now + dt.timedelta(seconds=est_wait)
    est_finish_at = est_start_at + dt.timedelta(seconds=float(task.est_duration_sec))

    return {
        "queue_position": len(ahead) + 1,
        "est_wait_sec": round(est_wait),
        "est_start_at": est_start_at.isoformat() + "Z",
        "est_finish_at": est_finish_at.isoformat() + "Z",
        "est_duration_sec": round(float(task.est_duration_sec)),
    }


# ── queue overview (design §15 "queue stats" KPI strip) ─────────────────
def queue_stats(db: Session) -> dict[str, Any]:
    by_lane = {
        "interactive": {"running": 0, "queued": 0},
        "routine": {"running": 0, "queued": 0},
    }
    rows = (
        db.query(Task.lane, Task.status, Task.task_id)
        .filter(Task.status.in_(["running", "queued"]))
        .all()
    )
    for lane, status, _ in rows:
        b = by_lane.get(lane, {"running": 0, "queued": 0})
        b[status] = b.get(status, 0) + 1
        by_lane[lane] = b

    # today_done / today_failed
    today_start = dt.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    today_done = (
        db.query(Task)
        .filter(Task.status == "success", Task.finished_at >= today_start)
        .count()
    )
    today_failed = (
        db.query(Task)
        .filter(Task.status == "failed", Task.finished_at >= today_start)
        .count()
    )

    # avg wait over last hour (successful tasks only)
    hour_ago = dt.datetime.utcnow() - dt.timedelta(hours=1)
    recent = (
        db.query(Task)
        .filter(
            Task.finished_at >= hour_ago,
            Task.status == "success",
            Task.started_at.isnot(None),
        )
        .all()
    )
    waits = [
        max(0.0, (r.started_at - r.created_at).total_seconds())
        for r in recent
        if r.started_at and r.created_at
    ]
    avg_wait = round(sum(waits) / len(waits)) if waits else 0

    concurrency = int(os.getenv("FACTOR_CONCURRENCY", "4"))
    reserve = int(os.getenv("FACTOR_RESERVE_INTERACTIVE", "1"))
    workers_busy = sum(b["running"] for b in by_lane.values())

    return {
        "lanes": by_lane,
        "today_done": today_done,
        "today_failed": today_failed,
        "avg_wait_sec": avg_wait,
        "concurrency": concurrency,
        "reserve_interactive": reserve,
        "workers_busy": workers_busy,
        "workers_total": concurrency,
        "worker_host": os.environ.get("HOSTNAME", "local"),
        "next_cron_at": None,        # no default tracking per user choice
        "next_cron_count": 0,
    }
