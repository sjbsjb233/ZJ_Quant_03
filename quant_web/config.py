"""Runtime configuration (Pydantic Settings, doc §2.3 env vars)."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    # data layer
    quant_data_root: str = "./data"

    # audit / app db
    audit_db_url: str = "sqlite:///./quant_web_audit.db"

    # auth
    jwt_secret: str = "dev-secret-change-me-in-prod"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 480          # 8h (doc §9.1)
    refresh_token_expire_minutes: int = 60 * 24 * 30  # 30d

    # cors
    allowed_origins: str = "http://localhost:5173,http://localhost:3000,http://127.0.0.1:5173"

    # jobs / cache (in-process runner needs no Redis; kept for forward-compat)
    redis_url: str = ""
    job_workers: int = 4

    # serving the built SPA (set to the frontend dist dir to serve it)
    frontend_dist: str = ""

    # bootstrap a demo admin on first launch when the user table is empty
    seed_default_users: bool = True

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
