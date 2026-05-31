"""FastAPI entrypoint: wires routers, auth, jobs, error format.

Pure API service (容器化重构 V3 §5): the SPA is served by the separate
``web-frontend`` nginx container, which reverse-proxies ``/api`` here. This
backend no longer mounts StaticFiles / a catch-all SPA route.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .auth.routes import router as auth_router
from .config import get_settings
from .data import router as data_router
from .db.base import SessionLocal, init_db
from .errors import install_error_handlers
from .health import router as health_router
from .jobs.runner import JobRunner
from .logging import app_logger, configure as configure_logging, init_store, install_sinks
from .logging.routes import router as logs_router
from .middleware import RequestLogMiddleware, TraceIdMiddleware
from .seed_users import ensure_seed_users
from .stubs import monitor_router, strategies_router

# Factor service proxy — replaces the 501 stub. /factors/*, /runs/*,
# /tasks/*, /universe/*, /imports/*, /queue/* are forwarded to factor-api.
from .factors_proxy import factors_proxy_router

# data-keeper write proxy — forwards data *write* commands + /jobs to the single
# writer when KEEPER_BASE is set (容器化重构 V3 §4.4). Unset ⇒ local writes.
from .keeper_proxy import keeper_proxy_router

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
    # When data-keeper is the single writer, mount the write proxy *before* the
    # local data router so its explicit write routes (+ /jobs) shadow the local
    # ones; every GET / read-only POST still falls through to the :ro reader
    # handlers. Unset KEEPER_BASE ⇒ writes run locally (rollback / dev / tests).
    if settings.keeper_base:
        app.include_router(keeper_proxy_router, prefix=f"{API}/data")
    app.include_router(data_router, prefix=f"{API}/data")
    app.include_router(logs_router, prefix=API)
    app.include_router(factors_proxy_router, prefix=API)
    app.include_router(strategies_router, prefix=API)
    app.include_router(monitor_router, prefix=API)
    # Aggregated dependency health (状态栏数据源). Lives on a separate path from the
    # lightweight /health below so docker's liveness probe never depends on keeper/
    # factor being up (修复 A1 §3.1).
    app.include_router(health_router, prefix=API)

    @app.get(f"{API}/health", tags=["meta"])
    def health():
        # 纯 liveness：进程能响应即 ok。docker healthcheck 继续打它,绝不聚合下游
        # (否则 keeper/factor 一挂会拖垮 web-backend 的 healthcheck → 误重启)。
        return {
            "status": "ok",
            "service": "web-backend",
            "version": "0.2.0",
            "environment": settings.environment,
            "cluster": settings.cluster,
            "quant_data_root": settings.quant_data_root,
        }

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

    return app


app = create_app()
