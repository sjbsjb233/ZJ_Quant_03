"""factor-api entry — FastAPI app under /factors/* + /runs + /tasks + ..."""
from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import correlation, factors, imports, results, runs, tasks, universe
from .config import ensure_dirs, get_settings
from .db import base as _db_base
from .db.seed import seed_all
from .errors import install_error_handlers
from .logging_ctx import configure_logging
from .middleware import TraceIdMiddleware


_log = logging.getLogger(__name__)


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging("INFO")
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

    @app.get("/api/v1/health")
    @app.get("/health")
    def _health() -> dict:
        return {
            "status": "ok",
            "service": "factor",
            "version": "0.1.0",
            "store_root": s.factor_store_root,
            "data_root": s.quant_data_root,
        }

    @app.on_event("startup")
    async def _startup() -> None:
        _db_base.init_db()
        with _db_base.SessionLocal() as db:
            stats = seed_all(db)
        _log.info("factor-api up: seeded %s", stats)

    return app


app = create_app()
