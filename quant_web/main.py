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
    )
    install_error_handlers(app)

    # --- routers ------------------------------------------------------------
    app.include_router(auth_router, prefix=API)
    app.include_router(data_router, prefix=f"{API}/data")
    app.include_router(factors_router, prefix=API)
    app.include_router(strategies_router, prefix=API)
    app.include_router(monitor_router, prefix=API)

    @app.get(f"{API}/health", tags=["meta"])
    def health():
        return {"status": "ok", "version": "0.2.0", "quant_data_root": settings.quant_data_root}

    # --- bootstrap app state (ready before the first request, no lifespan dance) ---
    init_db()
    if settings.seed_default_users:
        ensure_seed_users(SessionLocal)
    app.state.job_runner = JobRunner(
        SessionLocal, settings.quant_data_root, workers=settings.job_workers
    )

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
