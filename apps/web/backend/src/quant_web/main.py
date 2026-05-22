"""FastAPI entrypoint: wires routers, auth, jobs, error format, and the SPA."""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .auth.routes import router as auth_router
from .config import get_settings
from .data import router as data_router
from .db.base import SessionLocal, init_db
from .errors import install_error_handlers
from .jobs.runner import JobRunner
from .logging import app_logger, configure as configure_logging, init_store, install_sinks
from .logging.routes import router as logs_router
from .middleware import RequestLogMiddleware, TraceIdMiddleware
from .seed_users import ensure_seed_users
from .stubs import factors_router, monitor_router, strategies_router

logging.basicConfig(level=logging.INFO)

API = "/api/v1"


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="ZJ Quant · 数据治理平台 API",
        version="0.2.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Trace-Id"],
    )
    # Trace must wrap request logging so every log line carries the trace_id
    # (doc §5.2). add_middleware stacks outermost-last.
    app.add_middleware(RequestLogMiddleware)
    app.add_middleware(TraceIdMiddleware)
    install_error_handlers(app)

    # --- routers ------------------------------------------------------------
    app.include_router(auth_router, prefix=API)
    app.include_router(data_router, prefix=f"{API}/data")
    app.include_router(logs_router, prefix=API)
    app.include_router(factors_router, prefix=API)
    app.include_router(strategies_router, prefix=API)
    app.include_router(monitor_router, prefix=API)

    @app.get(f"{API}/health", tags=["meta"])
    def health():
        return {"status": "ok", "version": "0.2.0", "quant_data_root": settings.quant_data_root}

    # --- bootstrap app state (ready before the first request, no lifespan dance) ---
    init_db()
    # logging platform: configure threshold, start the async store, wire the
    # data-layer sink (全链路日志方案 v2.0 §5/§4.4). Must follow init_db (app_log table).
    configure_logging(settings.log_level)
    store = init_store(SessionLocal, settings)
    app.state.app_log_store = store
    install_sinks(settings)
    app_logger.info("app.startup", quant_data_root=settings.quant_data_root,
                    log_to_db=settings.log_to_db, log_to_file=store.file_ok)
    if settings.seed_default_users:
        ensure_seed_users(SessionLocal)
    app.state.job_runner = JobRunner(
        SessionLocal, settings.quant_data_root, workers=settings.job_workers
    )

    @app.on_event("shutdown")
    def _flush_logs() -> None:  # drain the queue + close files (doc §7.2)
        app_logger.info("app.shutdown")
        store.stop()

    # --- serve built SPA (production single-container) ----------------------
    dist = settings.frontend_dist
    if dist and os.path.isdir(dist):
        assets = os.path.join(dist, "assets")
        if os.path.isdir(assets):
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            # API + docs already matched above; everything else is the SPA shell.
            candidate = os.path.join(dist, full_path)
            if full_path and os.path.isfile(candidate):
                return FileResponse(candidate)
            return FileResponse(os.path.join(dist, "index.html"))

    return app


app = create_app()
