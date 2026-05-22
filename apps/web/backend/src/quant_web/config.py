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

    # bootstrap demo users on first launch when the user table is empty.
    # Off by default so a production deploy never silently creates predictable
    # accounts; enable explicitly for local/demo (see .env.example / docker-compose).
    seed_default_users: bool = False

    # --- logging platform (全链路日志方案 v2.0 §5.7) -----------------------
    log_level: str = "INFO"                  # global emission threshold
    log_to_db: bool = True                   # write the app_log table
    log_to_file: bool = True                 # write JSON Lines files
    log_file_dir: str = "/data/logs"         # container default; dev overrides to ./logs
    log_file_max_mb: int = 100               # single-file rotate threshold
    log_file_backup_count: int = 10
    log_db_retention_days: int = 30          # DB window before archive/delete
    log_slow_request_ms: int = 2000
    log_slow_query_ms: int = 2000
    log_sensitive_keys: str = "password,token,authorization,secret,api_key"
    client_log_max_batch: int = 100          # frontend records per POST
    client_log_rate_limit_per_min: int = 600  # per-user sliding window
    log_alert_webhook: str = ""              # opt-in ERROR+ webhook (钉钉/企业微信 robot)
    log_alert_min_level: str = "ERROR"
    log_alert_rate_per_min: int = 20

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def sensitive_keys(self) -> list[str]:
        return [k.strip().lower() for k in self.log_sensitive_keys.split(",") if k.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
