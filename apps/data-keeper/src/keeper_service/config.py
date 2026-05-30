"""Runtime settings for data-keeper (容器化重构 V3 §4.2 / §8.1).

data-keeper is the **single writer** of the data lake. One process hosts:
  - the write-command FastAPI app (lightweight metadata mutations),
  - an in-process JobRunner thread pool (heavy parquet rewrites + ingest + QC),
  - an in-process scheduler thread (autonomous crawl / quality / maintenance).

Everything is configured by env vars so a deploy can flip the rollback switches
(``ENABLE_SCHEDULER=false``) without code changes.
"""
from __future__ import annotations

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    # ── data layer (read+WRITE: data-keeper is the only :rw mount) ──────────
    quant_data_root: str = "./data"

    # ── keeper's own state (Job / scheduler bookkeeping) ────────────────────
    keeper_db_url: str = "sqlite:///./keeper-store/keeper.db"

    # ── job runner ──────────────────────────────────────────────────────────
    job_workers: int = 4

    # ── in-process scheduler (autonomous crawl / quality / maintenance) ──────
    enable_scheduler: bool = True
    # daily wall-clock (local TZ, set TZ=Asia/Shanghai) HH:MM trigger times.
    sched_crawl_at: str = "17:30"          # after the A-share close
    sched_quality_at: str = "18:00"
    sched_maintenance_at: str = "02:30"    # off-hours compaction + refresh-stats
    sched_tick_seconds: int = 30           # how often the loop wakes to check

    # ── auth (shared JWT secret with web; trust web's forwarded identity) ────
    jwt_secret: str = "dev-secret-change-me-in-prod"
    jwt_algorithm: str = "HS256"
    trust_proxy_user_headers: bool = True

    # ── CORS (in case keeper is hit directly during dev) ─────────────────────
    allowed_origins: str = (
        "http://localhost:5173,http://localhost:3000,http://localhost:8000,http://localhost:8300"
    )

    # ── logging (本地发射 + 中枢聚合, V3 §6.7) ───────────────────────────────
    log_level: str = "INFO"
    log_file_dir: str = "./logs"           # container default: /logs
    log_ingest_url: str = ""               # http://web-backend:8000/api/v1/logs/ingest
    log_ingest_token: str = ""             # shared service token (NOT a user JWT)

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings_cache() -> None:
    """Test helper — recompute settings after env vars change."""
    global _settings
    _settings = None


def ensure_dirs(s: Settings) -> None:
    """Create the writable dirs keeper needs before first use."""
    for d in (s.quant_data_root, s.log_file_dir):
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
