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

    # jobs / cache (in-process runner needs no Redis; kept for forward-compat).
    # When KEEPER_BASE is set the heavy-job runner lives in data-keeper; this
    # local runner only handles the rollback (KEEPER_BASE-unset) path.
    redis_url: str = ""
    job_workers: int = 4

    # factor service (proxied at /api/v1/factors|runs|tasks|universe|imports|queue)
    # In dev: defaults to localhost. In compose: service DNS http://factor-api:8100.
    factor_api_base: str = "http://factor-api:8100"

    # NOTE: FRONTEND_DIST removed in V3 §5 — the SPA is served by the web-frontend
    # nginx container, not this backend. (env var, if still set, is ignored.)

    # data-keeper (唯一写者). When set, data write/jobs requests are forwarded to
    # it (容器化重构 V3 §4.4); when empty, the web backend writes locally — this is
    # the rollback switch and the default for dev/tests (KEEPER_BASE unset → 本地写).
    keeper_base: str = ""

    # service-to-service log ingest token (容器化重构 V3 §6.7). Out-of-process
    # services (data-keeper, factor-*) POST their structured logs to
    # /api/v1/logs/ingest carrying X-Log-Token: <this>. NOT a user JWT. Empty
    # disables the endpoint (returns 401) so a misconfigured deploy never accepts
    # anonymous log injection.
    log_ingest_token: str = ""

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

    # --- layered log visibility (容器化重构 V3 §4.2) -----------------------
    # Which log `source`s each non-admin role may see, as a ";"-separated list of
    # "role:source,source" segments. admin is always all sources (not listed here).
    # The view+permission layers are how we make logs "feel" per-layer without
    # splitting storage — the app_log table stays a single ledger so trace joins
    # keep working (V3 §3.5/§3.6).
    log_visible_sources: str = "editor:data,factor,backend,frontend;viewer:frontend"

    @property
    def log_sources_by_role(self) -> dict[str, set[str]]:
        """Parse ``log_visible_sources`` into ``{role: {source, ...}}``."""
        out: dict[str, set[str]] = {}
        for seg in self.log_visible_sources.split(";"):
            if ":" not in seg:
                continue
            role, srcs = seg.split(":", 1)
            out[role.strip()] = {s.strip() for s in srcs.split(",") if s.strip()}
        return out

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def sensitive_keys(self) -> list[str]:
        return [k.strip().lower() for k in self.log_sensitive_keys.split(",") if k.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
