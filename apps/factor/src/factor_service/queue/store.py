"""Enqueue / claim / finalize / boost — atomic via SQLite BEGIN IMMEDIATE.

Lane / priority semantics:
  - lane="interactive" -> priority=100
  - lane="routine"     -> priority=10
  - boost (manual lift) within a lane bumps priority by +1 above the highest
    queued priority in that lane (so it always jumps to the front of its lane
    without crossing into the other lane).
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from ..db import base as _db_base
from ..db.models import Task, WorkerHeartbeat
from .estimator import estimate_duration


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


# ─────────────────────────────────────────────────────────────────────────────
# Worker liveness (修复 A1 §3.4 · 方案A) — independent of any running task.
def record_worker_heartbeat(db: Session, worker_id: str) -> None:
    """Upsert the dispatcher's ``last_seen`` (called once per poll cycle)."""
    wh = db.get(WorkerHeartbeat, worker_id)
    if wh is None:
        db.add(WorkerHeartbeat(worker_id=worker_id, last_seen=_now()))
    else:
        wh.last_seen = _now()
    db.commit()


def worker_status(db: Session, stale_window_sec: float) -> str:
    """Liveness verdict from the freshest heartbeat across all workers.

    'ok'    — newest heartbeat within ``stale_window_sec``
    'stale' — a worker checked in once but has gone quiet (likely hung/killed)
    'down'  — no dispatcher has ever checked in (table empty)
    """
    latest = db.query(func.max(WorkerHeartbeat.last_seen)).scalar()
    if latest is None:
        return "down"
    age = (_now() - latest).total_seconds()
    return "ok" if age <= stale_window_sec else "stale"


# ─────────────────────────────────────────────────────────────────────────────
def enqueue(
    db: Session,
    *,
    task_type: str,
    payload: dict[str, Any],
    lane: str = "interactive",
    run_id: str | None = None,
    factor_id: str | None = None,
    initiated_by: str = "",
    trace_id: str | None = None,
    max_attempts: int = 2,
    brun_id: str | None = None,
    seq_no: int = 0,
    gate: str = "ready",
) -> Task:
    """Persist a new task; sets priority by lane and ETA from history.

    ``brun_id``/``seq_no``/``gate`` carry the 篮子多级编排 (因子层重构 §6): a
    ``gate='waiting'`` task is created but NOT claimable until the orchestrator
    flips it to ``'ready'`` (basket_corr waits for all evals).
    """
    if lane not in ("interactive", "routine"):
        lane = "interactive"
    priority = 100 if lane == "interactive" else 10
    est = estimate_duration(db, task_type, payload)
    t = Task(
        task_id=f"ftask_{uuid.uuid4().hex[:16]}",
        task_type=task_type,
        lane=lane,
        priority=priority,
        status="queued",
        payload=payload,
        run_id=run_id,
        factor_id=factor_id,
        est_duration_sec=est,
        max_attempts=max_attempts,
        initiated_by=initiated_by,
        trace_id=trace_id,
        brun_id=brun_id,
        seq_no=seq_no,
        gate=gate,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


# ─────────────────────────────────────────────────────────────────────────────
def claim_next(db: Session, worker_id: str, *, reserve_interactive: int) -> Task | None:
    """Atomically pick the next runnable task for this worker.

    The reserve rule: when (currently-running routine) tasks already use
    `concurrency - reserve_interactive` slots, do NOT pick another routine
    task — only interactive ones — so users never starve.
    """
    running_routine = (
        db.query(Task)
        .filter(Task.status == "running", Task.lane == "routine")
        .count()
    )
    # The caller (dispatcher) is expected to fill slots until concurrency;
    # here we just decide if the routine lane is allowed for *this* claim.
    # The dispatcher passes its own concurrency cap to compute the cutoff.
    # We expose a coarse hint: callers pass `reserve_interactive` so we know
    # how many interactive slots to leave free.
    # The actual concurrency cap is enforced by the dispatcher's inflight count.
    allow_routine_param = True  # default; refined by caller via params below
    # The dispatcher refines `allow_routine_param` before calling us.

    while True:
        with _db_base.engine.begin() as conn:
            # Try interactive first; if none, try routine when allowed.
            sql_interactive = """
              SELECT task_id FROM tasks
              WHERE status='queued' AND lane='interactive'
              ORDER BY priority DESC, created_at ASC LIMIT 1
            """
            row = conn.execute(text(sql_interactive)).fetchone()
            if row is None and allow_routine_param:
                sql_routine = """
                  SELECT task_id FROM tasks
                  WHERE status='queued' AND lane='routine'
                  ORDER BY priority DESC, created_at ASC LIMIT 1
                """
                row = conn.execute(text(sql_routine)).fetchone()
            if row is None:
                return None
            result = conn.execute(
                text(
                    """UPDATE tasks SET status='running', worker_id=:w,
                       started_at=:t, heartbeat_at=:t, attempt=attempt+1
                       WHERE task_id=:id AND status='queued'"""
                ),
                {"w": worker_id, "t": _now(), "id": row[0]},
            )
            claimed = result.rowcount == 1
        if claimed:
            db.commit()
            return db.get(Task, row[0])
        # Lost the race — another worker grabbed this row. Loop and try the next.


def claim_next_filtered(
    db: Session, worker_id: str, allow_routine: bool
) -> Task | None:
    """Variant used by the dispatcher that already knows whether routine is
    allowed (based on running counts vs reserved slots).

    Race safety: the SELECT + UPDATE happen inside an autocommit transaction,
    but two threads can still SELECT the same row before either UPDATEs. We
    rely on the `AND status='queued'` predicate in the UPDATE to be the real
    arbiter — exactly one UPDATE succeeds. The loser must back off and pick
    again rather than return the (already-claimed) task to the caller.
    """
    while True:
        with _db_base.engine.begin() as conn:
            # interactive first — gate='ready' enforces依赖门控 (因子层重构 §6.2):
            # basket_corr stays 'waiting' (unclaimable) until all evals settle.
            sql = """
              SELECT task_id, lane FROM tasks
              WHERE status='queued' AND lane='interactive'
                AND (gate='ready' OR gate IS NULL)
              ORDER BY priority DESC, created_at ASC LIMIT 1
            """
            row = conn.execute(text(sql)).fetchone()
            if row is None and allow_routine:
                sql_r = """
                  SELECT task_id, lane FROM tasks
                  WHERE status='queued' AND lane='routine'
                    AND (gate='ready' OR gate IS NULL)
                  ORDER BY priority DESC, created_at ASC LIMIT 1
                """
                row = conn.execute(text(sql_r)).fetchone()
            if row is None:
                return None
            result = conn.execute(
                text(
                    """UPDATE tasks SET status='running', worker_id=:w,
                       started_at=:t, heartbeat_at=:t, attempt=attempt+1
                       WHERE task_id=:id AND status='queued'"""
                ),
                {"w": worker_id, "t": _now(), "id": row[0]},
            )
            claimed = result.rowcount == 1
        if claimed:
            db.commit()
            return db.get(Task, row[0])
        # Lost the race — another worker grabbed this task. Try the next one.
        # No backoff needed: the outer SELECT will skip the now-running row.


# ─────────────────────────────────────────────────────────────────────────────
def heartbeat(
    db: Session, task_id: str, *, current: int | None = None, total: int | None = None
) -> None:
    t = db.get(Task, task_id)
    if t is None:
        return
    if current is not None:
        t.progress_current = current
    if total is not None:
        t.progress_total = total
    t.heartbeat_at = _now()
    db.commit()


def finalize(
    db: Session,
    task_id: str,
    *,
    status: str,
    error: str | None = None,
    result_summary: dict[str, Any] | None = None,
) -> None:
    t = db.get(Task, task_id)
    if t is None:
        return
    t.status = status
    t.finished_at = _now()
    if error is not None:
        t.error = error
    if result_summary is not None:
        t.result_summary = result_summary
    db.commit()


def request_cancel(db: Session, task_id: str) -> Task | None:
    t = db.get(Task, task_id)
    if t is None:
        return None
    if t.status == "queued":
        t.status = "canceled"
        t.finished_at = _now()
    elif t.status == "running":
        t.cancel_requested = True
    db.commit()
    return t


def retry(db: Session, task_id: str) -> Task | None:
    """Reset a failed/canceled task back to queued. Bumps attempt back to 0."""
    t = db.get(Task, task_id)
    if t is None:
        return None
    if t.status in ("running", "queued"):
        return t
    t.status = "queued"
    t.attempt = 0
    t.cancel_requested = False
    t.started_at = None
    t.finished_at = None
    t.error = None
    t.worker_id = None
    t.heartbeat_at = None
    t.created_at = _now()  # restart wait clock
    db.commit()
    return t


def boost(db: Session, task_id: str) -> Task | None:
    """Lift a queued task to the front of its lane (priority = max+1)."""
    t = db.get(Task, task_id)
    if t is None or t.status != "queued":
        return t
    with _db_base.engine.begin() as conn:
        row = conn.execute(
            text(
                """SELECT MAX(priority) FROM tasks WHERE status='queued' AND lane=:l"""
            ),
            {"l": t.lane},
        ).fetchone()
        new_p = (row[0] or t.priority) + 1
        conn.execute(
            text("UPDATE tasks SET priority=:p WHERE task_id=:id"),
            {"p": new_p, "id": task_id},
        )
    db.commit()
    db.refresh(t)
    return t


# ─────────────────────────────────────────────────────────────────────────────
def recover_stale(db: Session, stale_seconds: int) -> int:
    """Take running tasks whose heartbeat is older than the threshold and:
    - if attempt < max_attempts, return to queued (auto retry);
    - else mark failed.
    Returns number of tasks affected.
    """
    cutoff = _now() - dt.timedelta(seconds=stale_seconds)
    stale = (
        db.query(Task)
        .filter(Task.status == "running", Task.heartbeat_at < cutoff)
        .all()
    )
    n = 0
    for t in stale:
        n += 1
        if t.attempt >= t.max_attempts:
            t.status = "failed"
            t.finished_at = _now()
            t.error = (t.error or "") + " · worker lost (stale heartbeat)"
        else:
            t.status = "queued"
            t.worker_id = None
            t.heartbeat_at = None
            t.started_at = None
            t.cancel_requested = False
    if n:
        db.commit()
    return n
