"""factor-api entry — FastAPI app under /factors/* + /runs + /tasks + ..."""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import baskets, correlation, factors, imports, results, runs, screening, tasks, universe
from .config import ensure_dirs, get_settings
from .db import base as _db_base
from .db.seed import seed_all
from .errors import install_error_handlers
from .logging_ctx import configure_logging
from .middleware import TraceIdMiddleware
from .obs import install_obs, stop_obs
from .queue import store


_log = logging.getLogger(__name__)


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.log_level)
    install_obs(s)               # log shipper → web /logs/ingest (source="factor")
    ensure_dirs(s)

    app = FastAPI(
        title="ZJ Quant — Factor Service",
        version="0.1.0",
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    # CORS (in case factor-api is hit directly during dev)
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

    # Routers — all under /api/v1 *prefix-free* because the web proxy
    # mounts them under /api/v1/factors. We keep the same paths as the
    # design's «/factors/*» so the frontend can also call this service
    # directly during local dev without rewriting URLs.
    app.include_router(factors.router)
    app.include_router(runs.router)
    app.include_router(tasks.router)
    app.include_router(results.router)
    app.include_router(imports.router)
    app.include_router(universe.router)
    app.include_router(correlation.router)
    # 因子篮子 (因子层重构) — flag-gated so the subsystem can ship dark.
    if s.factor_basket_enabled:
        app.include_router(baskets.router)
        app.include_router(screening.router)

    @app.get("/api/v1/health")
    @app.get("/health")
    def _health() -> dict:
        # Infer factor-worker liveness from its heartbeat beacon (修复 A1 §3.4 方案A).
        # Stale window = 3× poll, floored at 10s so loop/DB jitter never flaps yellow.
        poll_sec = s.factor_poll_interval_ms / 1000.0
        stale_window = max(3.0 * poll_sec, 10.0)
        try:
            with _db_base.SessionLocal() as db:
                worker = store.worker_status(db, stale_window)
        except Exception:  # noqa: BLE001 - health must never 500 on the worker probe
            worker = "unknown"
        return {
            "status": "ok",
            "service": "factor",
            "version": "0.1.0",
            "store_root": s.factor_store_root,
            "data_root": s.quant_data_root,
            "worker": worker,
        }

    @app.on_event("startup")
    async def _startup() -> None:
        _db_base.init_db()
        with _db_base.SessionLocal() as db:
            stats = seed_all(db)
        _log.info("factor-api up: seeded %s", stats)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        stop_obs()               # drain the shipper queue on shutdown

    return app


app = create_app()
