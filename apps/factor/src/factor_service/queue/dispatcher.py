"""Worker entry point: `python -m factor_service.queue.dispatcher`.

A single dispatcher polls the `tasks` table every FACTOR_POLL_INTERVAL_MS and
submits eligible work to a thread pool of FACTOR_CONCURRENCY slots. Each task
runs in its own thread inside the worker process; the executor stamps progress
via heartbeats so the API can show real-time status.

We use threads rather than processes because:
  - the data-layer DataReader holds a DuckDB connection that is cheaper per
    thread than per process
  - tasks are I/O + numpy heavy; the GIL is rarely the bottleneck
  - simpler shared-state (SQLite session pool) than child processes

The reserve_interactive setting prevents routine tasks from filling all
slots — when running_routine >= concurrency - reserve, we only pick from
the interactive lane.
"""
from __future__ import annotations

import logging
import os
import signal
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

from quant_data import logging as qd_logging
from sqlalchemy.orm import Session

from ..config import ensure_dirs, get_settings
from ..db import base as _db_base
from ..db.models import Task
from ..db.seed import seed_all
from ..logging_ctx import configure_logging, trace_id_var
from ..obs import install_obs
from ..services.run_executor import execute_task
from . import store
from .estimator import update_stats


_log = logging.getLogger("factor.dispatcher")
# Structured logger: task.claimed is bound to the task's own trace_id so the
# "claimed" moment joins the same app_log chain as the run that enqueued it.
_qlog = qd_logging.get_logger("factor.dispatcher")
_stop = threading.Event()


def _handle_signal(signum, _frame):  # noqa: ARG001
    _log.warning("dispatcher received signal %s; will stop after in-flight tasks", signum)
    _stop.set()


def _run_one(task_id: str, trace_id: str | None) -> dict:
    tok = trace_id_var.set(trace_id or "-")
    # bind quant_data's context too so the task's data-layer reads carry the
    # task's trace_id into app_log (V3 §6.4 — autonomous/worker task tracing).
    qd_tok = qd_logging.bind_context(trace_id=trace_id)
    try:
        return execute_task(task_id)
    finally:
        qd_logging.reset_context(qd_tok)
        trace_id_var.reset(tok)


def _advance_basket(db: Session, t: Task) -> None:
    """The ONLY orchestration hook in the dispatcher (因子层重构 §6.3): after a
    basket child finalizes, push its parent run forward. Never crash the loop."""
    if not getattr(t, "brun_id", None):
        return
    try:
        from ..baskets import orchestrator
        orchestrator.advance(db, t.brun_id)
    except Exception:  # noqa: BLE001
        _log.warning("orchestrator.advance failed for brun %s", t.brun_id, exc_info=True)


def _finalize_future(db: Session, task_id: str, fut: Future) -> None:
    t = db.get(Task, task_id)
    if t is None:
        return
    if fut.exception() is not None:
        exc = fut.exception()
        msg = f"{type(exc).__name__}: {exc}"
        # Auto-retry within max_attempts (best-effort)
        if t.attempt < t.max_attempts:
            t.status = "queued"
            t.worker_id = None
            t.heartbeat_at = None
            t.started_at = None
        else:
            t.status = "failed"
            t.error = msg
            t.finished_at = t.finished_at or _now_utc()
        db.commit()
        _log.error("task %s failed: %s", task_id, msg)
    else:
        res = fut.result() or {}
        # The worker returns the *run* outcome in res["status"]; a preflight
        # block or cancel returns normally (no exception) but the run did NOT
        # succeed, so reflect that on the task rather than marking it success.
        run_status = res.get("status")
        if run_status == "failed":
            t.status = "failed"
            t.error = res.get("reason") or t.error or "run_failed"
        elif run_status == "canceled":
            t.status = "canceled"
        else:
            t.status = "success"  # success or degraded (the run completed)
        t.finished_at = t.finished_at or _now_utc()
        t.result_summary = res.get("summary") or t.result_summary or {}
        db.commit()
        # update duration history
        if t.started_at and t.finished_at:
            dur = (t.finished_at - t.started_at).total_seconds()
            update_stats(db, t.task_type, t.payload or {}, max(0.1, dur))
        _log.info(
            "task %s success in %.1fs",
            task_id,
            (t.finished_at - t.started_at).total_seconds()
            if (t.started_at and t.finished_at)
            else 0.0,
        )
    # Push the owning basket run forward (no-op for standalone tasks).
    _advance_basket(db, t)


def _now_utc():
    import datetime as dt

    return dt.datetime.utcnow()


def main() -> None:
    s = get_settings()
    configure_logging(s.log_level)
    install_obs(s)               # log shipper → web /logs/ingest (source="factor")
    ensure_dirs(s)
    _db_base.init_db()
    # Seed builtins + presets on first launch (idempotent).
    with _db_base.SessionLocal() as db:
        seed_all(db)

    conc = int(os.getenv("FACTOR_CONCURRENCY", str(s.factor_concurrency)))
    reserve = int(
        os.getenv("FACTOR_RESERVE_INTERACTIVE", str(s.factor_reserve_interactive))
    )
    # Memory-aware admission gate: at most `heavy_cap` data-reading evaluations
    # run at once, regardless of `conc`. Each evaluation peaks at multiple GB of
    # RSS (full bars_daily scan → polars), so 4 concurrent ones OOM-killed the
    # worker; this bounds peak memory while light tasks still fill the pool.
    heavy_cap = max(
        1, int(os.getenv("FACTOR_HEAVY_CONCURRENCY", str(s.factor_heavy_concurrency)))
    )
    poll_ms = int(os.getenv("FACTOR_POLL_INTERVAL_MS", str(s.factor_poll_interval_ms)))
    stale_sec = int(s.factor_stale_seconds)

    worker_id = (os.environ.get("HOSTNAME", "worker") + f"#{os.getpid()}")
    _log.info(
        "dispatcher up — concurrency=%d heavy_cap=%d reserve=%d poll=%dms worker=%s",
        conc,
        heavy_cap,
        reserve,
        poll_ms,
        worker_id,
    )

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    pool = ThreadPoolExecutor(max_workers=conc, thread_name_prefix="factor-worker")
    inflight: dict[Future, str] = {}
    # Subset of `inflight` whose task is a HEAVY_TASK_TYPES (data-reading) job —
    # the count drives the memory-aware admission gate below. NB: this gate is
    # per-process; it assumes the single-worker deployment (compose pins
    # container_name, so no --scale). If factor-worker is ever replicated, N
    # workers would each admit heavy_cap heavies → N×cap total, re-opening the
    # OOM — make the gate count running heavy tasks in the DB instead.
    inflight_heavy: set[Future] = set()

    db = _db_base.SessionLocal()
    store.recover_stale(db, stale_sec)

    # 因子篮子调度 (§7.1) + 孤儿巡检 (§6.3) — throttled to ~once/60s.
    _last_basket_sweep = [0.0]

    def _basket_maintenance() -> None:
        if not s.factor_basket_enabled:
            return
        now = time.monotonic()
        if now - _last_basket_sweep[0] < 60.0:
            return
        _last_basket_sweep[0] = now
        try:
            from ..baskets import scheduler as _sched
            from quant_data import DataReader
            _sched.sweep_orphans(db)
            reader = DataReader(root=s.quant_data_root)
            _sched.tick_baskets(db, reader)
        except Exception:  # noqa: BLE001 - maintenance must never crash the loop
            _log.debug("basket maintenance failed", exc_info=True)

    try:
        while not _stop.is_set():
            # 1) collect finished futures
            done = [f for f in list(inflight) if f.done()]
            for f in done:
                tid = inflight.pop(f)
                inflight_heavy.discard(f)
                _finalize_future(db, tid, f)

            # 2) recover stale running tasks (heartbeat gap)
            store.recover_stale(db, stale_sec)

            # 3) fill up to concurrency
            while len(inflight) < conc:
                running_routine = (
                    db.query(Task)
                    .filter(Task.status == "running", Task.lane == "routine")
                    .count()
                )
                allow_routine = running_routine < (conc - reserve)
                # Memory gate: stop claiming heavy (data-reading) tasks once
                # heavy_cap of them are already running. Light tasks may still
                # fill the remaining slots; queued heavy tasks wait their turn.
                allow_heavy = len(inflight_heavy) < heavy_cap
                t = store.claim_next_filtered(
                    db, worker_id, allow_routine, allow_heavy=allow_heavy
                )
                if t is None:
                    break
                fut = pool.submit(_run_one, t.task_id, t.trace_id)
                inflight[fut] = t.task_id
                if t.task_type in store.HEAVY_TASK_TYPES:
                    inflight_heavy.add(fut)
                _log.info(
                    "claimed task %s (type=%s lane=%s heavy=%d/%d)",
                    t.task_id,
                    t.task_type,
                    t.lane,
                    len(inflight_heavy),
                    heavy_cap,
                )
                # structured twin, joined to the task's trace_id (V3 §4.3 / §6.4)
                _tok = qd_logging.bind_context(trace_id=t.trace_id)
                try:
                    _qlog.info("task.claimed", task_id=t.task_id, type=t.task_type,
                               lane=t.lane, trace_id=t.trace_id)
                finally:
                    qd_logging.reset_context(_tok)

            # basket tracking tick + orphan sweep (throttled inside).
            _basket_maintenance()

            # liveness beacon (修复 A1 §3.4): mark this dispatcher alive every cycle —
            # even idle — so factor-api /health can tell live-but-idle from crashed.
            try:
                store.record_worker_heartbeat(db, worker_id)
            except Exception:  # noqa: BLE001 - heartbeat must never crash the loop
                _log.debug("worker heartbeat write failed", exc_info=True)

            time.sleep(poll_ms / 1000.0)
    finally:
        _log.info("dispatcher shutting down; %d in-flight", len(inflight))
        pool.shutdown(wait=True)
        db.close()


if __name__ == "__main__":
    main()
