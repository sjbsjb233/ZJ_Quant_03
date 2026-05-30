"""Runtime settings for factor-api + factor-worker."""
from __future__ import annotations

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="", env_file=".env", extra="ignore"
    )

    # ── data layer (read-only) ─────────────────────────────────────────
    quant_data_root: str = "./data"

    # ── factor service own store (writable) ────────────────────────────
    factor_store_root: str = "./factor-store"
    factor_db_url: str = "sqlite:///./factor-store/factor.db"

    # ── auth (shared JWT secret with web) ─────────────────────────────
    jwt_secret: str = "dev-secret-change-me-in-prod"
    jwt_algorithm: str = "HS256"

    # ── role + worker tunables ─────────────────────────────────────────
    factor_role: str = "api"  # "api" | "worker"
    factor_concurrency: int = 4
    factor_reserve_interactive: int = 1
    factor_poll_interval_ms: int = 1000
    factor_stale_seconds: int = 120

    # ── CORS (in case factor service is exposed directly) ──────────────
    allowed_origins: str = (
        "http://localhost:5173,http://localhost:3000,http://localhost:8000,http://localhost:8100"
    )

    # ── trust headers from the web proxy (X-Forwarded-User / Role) ────
    trust_proxy_user_headers: bool = True

    # ── logging (本地发射 + 中枢聚合, 容器化重构 V3 §6.7) ───────────────
    # First time factor logs join the unified app_log: a shipper posts them to
    # web-backend /logs/ingest as source="factor"; empty url ⇒ stderr only.
    log_level: str = "INFO"
    log_file_dir: str = "./logs"               # container default: /logs
    log_ingest_url: str = ""                   # http://web-backend:8000/api/v1/logs/ingest
    log_ingest_token: str = ""                 # shared service token (NOT a user JWT)

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def evaluations_dir(self) -> str:
        return os.path.join(self.factor_store_root, "evaluations")

    @property
    def runs_dir(self) -> str:
        return os.path.join(self.factor_store_root, "runs")

    @property
    def library_dir(self) -> str:
        return os.path.join(self.factor_store_root, "library")

    @property
    def inbox_dir(self) -> str:
        return os.path.join(self.factor_store_root, "inbox")

    @property
    def correlations_dir(self) -> str:
        return os.path.join(self.factor_store_root, "correlations")


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
    """Create the store sub-directories the service expects."""
    for d in (
        s.factor_store_root,
        s.evaluations_dir,
        s.runs_dir,
        s.library_dir,
        s.inbox_dir,
        s.correlations_dir,
    ):
        os.makedirs(d, exist_ok=True)
