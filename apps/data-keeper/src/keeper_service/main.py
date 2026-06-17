"""data-keeper entry — FastAPI write-command API + in-process JobRunner + scheduler.

A single ``uvicorn keeper_service.main:app`` runs all three (容器化重构 V3 §4.2):
on startup we install observability (log shipper), open keeper.db, spin up the
JobRunner thread pool, and — unless ``ENABLE_SCHEDULER=false`` — start the
in-process scheduler thread. Single writer ⇒ single instance ⇒ in-process cron
cannot double-fire (§4.2.1).
"""
from __future__ import annotations

import datetime as dt

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from quant_data import logging as qd_logging
from sqlalchemy import func

from .commands import command_router
from .config import ensure_dirs, get_settings
from .db import base as _db_base
from .db.models import Job
from .errors import install_error_handlers
from .jobs.routes import router as jobs_router
from .jobs.runner import JobRunner
from .logging_ctx import configure_logging
from .middleware import TraceIdMiddleware
from .obs import install_obs, stop_obs
from .reaper import start_reaper, stop_reaper
from .reconcile import ensure_job_columns, reconcile_orphans
from .scheduler import start_scheduler, stop_scheduler

_log = qd_logging.get_logger("keeper.main")

# process start (uptime baseline for /health — 进程看不见自己被 kill, 哨兵补重启数)
_STARTED_AT = dt.datetime.utcnow()

# scheduler task name → the Job.type it enqueues (scheduler.py). Used to render the
# 死人开关 panel (last_run / last_success per autonomous task, §6.5 D7).
_SCHED_TASK_TYPES = {"crawl": "crawl", "quality": "quality_scan",
                     "maintenance": "scheduled_maintenance"}
_FINISHED_OK = ("success",)
_FAILED_STATES = ("failed", "timeout")


def _health_payload(s) -> dict:
    """Rich /health for the monitor layer (可观测层重构方案 §6.5): uptime, job
    counts, last success, and per-scheduler-task last_run / last_success."""
    now = dt.datetime.utcnow()
    since_24h = now - dt.timedelta(hours=24)
    with _db_base.SessionLocal() as db:
        running = int(db.query(func.count(Job.job_id))
                      .filter(Job.status == "running").scalar() or 0)
        failed = int(db.query(func.count(Job.job_id))
                     .filter(Job.status.in_(_FAILED_STATES), Job.finished_at >= since_24h)
                     .scalar() or 0)
        ok = int(db.query(func.count(Job.job_id))
                 .filter(Job.status.in_(_FINISHED_OK), Job.finished_at >= since_24h)
                 .scalar() or 0)
        last_success = (db.query(func.max(Job.finished_at))
                        .filter(Job.status.in_(_FINISHED_OK)).scalar())
        last_finished = db.query(func.max(Job.finished_at)).scalar()
        sched: dict[str, dict] = {}
        for task, job_type in _SCHED_TASK_TYPES.items():
            last_run = db.query(func.max(Job.started_at)).filter(Job.type == job_type).scalar()
            last_ok = (db.query(func.max(Job.finished_at))
                       .filter(Job.type == job_type, Job.status.in_(_FINISHED_OK)).scalar())
            sched[task] = {"last_run_at": _iso(last_run), "last_success_at": _iso(last_ok)}
    return {
        "status": "ok", "service": "data-keeper", "version": "0.1.0",
        "quant_data_root": s.quant_data_root, "scheduler": s.enable_scheduler,
        "uptime_seconds": int((now - _STARTED_AT).total_seconds()),
        "started_at": _iso(_STARTED_AT),
        "jobs_running": running, "jobs_failed_24h": failed, "jobs_succeeded_24h": ok,
        "last_job_success_at": _iso(last_success), "last_job_finished_at": _iso(last_finished),
        "scheduler_tasks": sched,
    }


def _iso(value) -> str | None:
    return value.isoformat() + "Z" if isinstance(value, dt.datetime) else None


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.log_level)
    ensure_dirs(s)

    app = FastAPI(
        title="ZJ Quant — 数据策管服务 (data-keeper)",
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Trace-Id"],
    )
    app.add_middleware(TraceIdMiddleware)
    install_error_handlers(app)

    # Routers keep the SAME sub-paths as quant_web's data router so the web proxy
    # can forward /api/v1/data/<x> -> http://data-keeper:8300/<x> verbatim.
    app.include_router(command_router)
    app.include_router(jobs_router)

    @app.get("/health")
    @app.get("/api/v1/health")
    def _health() -> dict:
        return _health_payload(s)

    # Wire app state inline (ready before the first request, no lifespan dance —
    # same pattern as quant_web.main, so TestClient works without a `with` block).
    install_obs(s)                              # log shipper (source="data")
    _db_base.init_db()                          # keeper.db (Job ledger)
    ensure_job_columns(_db_base.engine)         # forward-migrate progress_at (D6)
    if s.reconcile_on_start:
        reconcile_orphans(_db_base.SessionLocal)  # D5: failed(interrupted) + ERROR
    app.state.job_runner = JobRunner(
        _db_base.SessionLocal, s.quant_data_root, workers=s.job_workers
    )
    app.state.reaper = start_reaper(_db_base.SessionLocal, s)   # D6: reclaim 卡死 jobs
    if s.enable_scheduler:
        app.state.scheduler = start_scheduler(app.state.job_runner, s)
    _log.info("keeper.startup", quant_data_root=s.quant_data_root,
              scheduler=s.enable_scheduler, job_workers=s.job_workers)

    @app.on_event("shutdown")
    def _shutdown() -> None:
        _log.info("keeper.shutdown")
        stop_scheduler()
        stop_reaper()
        # drain the JobRunner pool before the shipper goes down, so the drain
        # (and any in-flight job 协作取消) is still recorded in中心日志。
        app.state.job_runner.stop()
        stop_obs()                              # drain the shipper queue

    return app


app = create_app()
