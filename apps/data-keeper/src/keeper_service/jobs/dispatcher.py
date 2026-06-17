"""Standalone worker entry — the escape hatch from V3 §4.2.1.

In the default single-container deployment data-keeper runs its JobRunner thread
pool **in-process** (started by ``main.py``); you do NOT run this. It exists only
for the day heavy writes get frequent/long enough to deserve crash-isolation in a
dedicated container, at which point compose adds, on the SAME image:

    command: ["python","-m","keeper_service.jobs.dispatcher"]

This poller claims ``pending`` jobs from keeper.db (atomic pending→running),
re-binds their trace_id, and runs the matching actor — so splitting it out needs
no business-code change. Run it INSTEAD of the in-process runner (set
``ENABLE_SCHEDULER`` on whichever process owns the schedule) to avoid double
execution.
"""
from __future__ import annotations

import datetime as dt
import signal
import threading
import time

from quant_admission import Grant
from quant_data import logging as qd_logging

from ..config import ensure_dirs, get_settings
from ..db import base as _db_base
from ..db.models import Job
from ..logging_ctx import configure_logging, trace_id_var
from ..obs import install_obs
from . import actors
from .runner import JobCancelled, JobContext, JobRunner

_log = qd_logging.get_logger("keeper.dispatcher")
_stop = threading.Event()

# job.type -> actor callable (names match what the command endpoints submit).
REGISTRY = {
    "field_migration": actors.field_migration,
    "vacuum": actors.vacuum,
    "compact": actors.compact,
    "snapshot": actors.snapshot,
    "refresh_stats": actors.refresh_stats,
    "quality_scan": actors.quality_scan,
    "scheduled_maintenance": actors.scheduled_maintenance,
    "crawl": actors.crawl,
}


def _handle_signal(signum, _frame):  # noqa: ARG001
    _log.warn("dispatcher.signal", signum=signum)
    _stop.set()


def _claim_one(db, worker_id: str) -> Job | None:
    """Atomically move the oldest pending job to running (single-writer-safe)."""
    job = (db.query(Job).filter(Job.status == "pending")
           .order_by(Job.created_at.asc()).first())
    if job is None:
        return None
    now = dt.datetime.utcnow()
    job.status = "running"
    job.started_at = now
    job.progress_at = now    # 心跳, 与 in-process runner 一致 (回收线程 D6)
    job.initiated_by = job.initiated_by or worker_id
    db.commit()
    return job


def main() -> None:
    s = get_settings()
    configure_logging(s.log_level)
    ensure_dirs(s)
    install_obs(s)
    _db_base.init_db()
    runner = JobRunner(_db_base.SessionLocal, s.quant_data_root, workers=s.job_workers)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    _log.info("dispatcher.up", workers=s.job_workers)

    while not _stop.is_set():
        with _db_base.SessionLocal() as db:
            job = _claim_one(db, "dispatcher")
            if job is None:
                time.sleep(1.0)
                continue
            job_id, job_type, params, trace_id = job.job_id, job.type, job.params or {}, job.trace_id
        fn = REGISTRY.get(job_type)
        if fn is None:
            runner._update(job_id, status="failed", error=f"unknown job type {job_type}",
                           finished_at=dt.datetime.utcnow())
            continue
        tok = trace_id_var.set(trace_id)
        qd_tok = qd_logging.bind_context(trace_id=trace_id)
        ctx = JobContext(runner, job_id, s.quant_data_root)
        grant: Grant | None = None
        try:
            # 与 in-process JobRunner._run 同样的 WP5 准入闸: 重型写在专属预算下
            # 排队/限流, 独立容器部署不再缺失 OOM 防护。
            grant = runner._acquire_admission(ctx, job_id, job_type)
            result = fn(ctx, **params)
            if runner._is_cancelled(job_id):
                raise JobCancelled(job_id)
            runner._update(job_id, status="success", result=result or {},
                           finished_at=dt.datetime.utcnow())
        except JobCancelled:
            runner._update(job_id, status="cancelled", finished_at=dt.datetime.utcnow())
            _log.warn("dispatcher.job.cancelled", job_id=job_id, job_type=job_type)
        except Exception as exc:  # noqa: BLE001
            runner._update(job_id, status="failed", error=str(exc),
                           finished_at=dt.datetime.utcnow())
            _log.error("dispatcher.job.failed", exc=exc, job_id=job_id, job_type=job_type)
        finally:
            if grant is not None:
                grant.release()
            runner._clear_cancel(job_id)
            qd_logging.reset_context(qd_tok)
            trace_id_var.reset(tok)


if __name__ == "__main__":
    main()
