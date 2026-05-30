"""data-keeper entry — FastAPI write-command API + in-process JobRunner + scheduler.

A single ``uvicorn keeper_service.main:app`` runs all three (容器化重构 V3 §4.2):
on startup we install observability (log shipper), open keeper.db, spin up the
JobRunner thread pool, and — unless ``ENABLE_SCHEDULER=false`` — start the
in-process scheduler thread. Single writer ⇒ single instance ⇒ in-process cron
cannot double-fire (§4.2.1).
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from quant_data import logging as qd_logging

from .commands import command_router
from .config import ensure_dirs, get_settings
from .db import base as _db_base
from .errors import install_error_handlers
from .jobs.routes import router as jobs_router
from .jobs.runner import JobRunner
from .logging_ctx import configure_logging
from .middleware import TraceIdMiddleware
from .obs import install_obs, stop_obs
from .scheduler import start_scheduler, stop_scheduler

_log = qd_logging.get_logger("keeper.main")


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
        return {
            "status": "ok",
            "service": "data-keeper",
            "version": "0.1.0",
            "quant_data_root": s.quant_data_root,
            "scheduler": s.enable_scheduler,
        }

    # Wire app state inline (ready before the first request, no lifespan dance —
    # same pattern as quant_web.main, so TestClient works without a `with` block).
    install_obs(s)                              # log shipper (source="data")
    _db_base.init_db()                          # keeper.db (Job ledger)
    app.state.job_runner = JobRunner(
        _db_base.SessionLocal, s.quant_data_root, workers=s.job_workers
    )
    if s.enable_scheduler:
        app.state.scheduler = start_scheduler(app.state.job_runner, s)
    _log.info("keeper.startup", quant_data_root=s.quant_data_root,
              scheduler=s.enable_scheduler, job_workers=s.job_workers)

    @app.on_event("shutdown")
    def _shutdown() -> None:
        _log.info("keeper.shutdown")
        stop_scheduler()
        stop_obs()                              # drain the shipper queue

    return app


app = create_app()
