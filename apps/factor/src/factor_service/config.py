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

    # ── 运行前置全查 + 受控降级 (设计 §12.3) ────────────────────────────
    # preflight_enabled: master switch for the gate (worker + submit-time guard).
    # degradation_policy_profile: "strict" (finance default — block on missing
    #   neutralization/return/flag data) | "permissive" (灰度: record & run on).
    preflight_enabled: bool = True
    degradation_policy_profile: str = "strict"

    # ── 因子篮子 (因子层重构 §14.1) ─────────────────────────────────────────
    # Master flag for the basket subsystem (P1/P2 isolation). When off, the
    # basket routers are not mounted and the worker's tick/orphan sweep is a
    # no-op — existing single-factor flows are untouched.
    factor_basket_enabled: bool = True

    # ── 测试专用时间旅行 (因子层验证 SCH/追踪) ───────────────────────────────
    # factor_fake_today: when set to an ISO date (optionally `YYYY-MM-DD[THH:MM]`),
    # the basket scheduler/runner believe "today" is that date — so tests can
    # advance the trading day deterministically. EMPTY = real clock (production).
    # Honored only when non-empty; never affects prod unless explicitly set.
    factor_fake_today: str = ""

    # ── 运行透视 / Run Trace 采集 (设计 §2.6) ───────────────────────────
    # trace_enabled: master switch — off ⇒ evaluator builds no trace, no埋点,
    #   no artifact, and the endpoint returns available:false (behaviour exactly
    #   equivalent to before this feature). A one-click降级回原行为.
    # trace_histogram_enabled: 单日分布 16-bucket直方图 — drop when体量 sensitive
    #   (box-and-whisker stats min/p25/p50/p75/max/mean/std are always kept).
    trace_enabled: bool = True
    trace_histogram_enabled: bool = True

    # ── 因子运行资源画像 (资源画像方案 v1.2 §3/§9) ──────────────────────────
    # resource_sampling_enabled: master switch — off ⇒ no process sampler, no
    #   stage resource capture, no artifacts; the trace's `resources` node is
    #   absent and the UI shows the legacy空态 (behaviour equivalent to before).
    # resource_sample_interval_s: process sampler cadence (1Hz, 方案 §3.1).
    # resource_max_points: trace.json `resources.samples` cap (max-pool, 方案 §3.3).
    # resource_fullres_enabled: write the 1Hz full-resolution {run_id}.samples.json.gz
    #   artifact (方案 §3.4) consumed lazily by the timeline brush/zoom.
    # resource_ndjson_retention_days / _max_mb: worker_samples NDJSON留存 (方案 §9).
    # resource_baseline_window: rolling sample count per signature for the p50/p90
    #   baseline (镜像 task_duration_stat 的轻量风格, 方案 §5.2).
    resource_sampling_enabled: bool = True
    resource_sample_interval_s: float = 1.0
    resource_max_points: int = 300
    resource_fullres_enabled: bool = True
    resource_ndjson_retention_days: int = 14
    resource_ndjson_max_mb: int = 50
    resource_baseline_window: int = 64
    resource_baseline_min_samples: int = 5
    # D17 资源回归哨兵 (方案 §5.3): 同因子新版本 p50 归一化资源增幅超此比例触发告警.
    resource_regression_threshold: float = 0.5

    # ── 函数级火焰图剖析 (资源画像方案 v1.1 §3.5) ───────────────────────────
    # resource_profile_default: 默认关闭 — 剖析有开销与体积成本, 仅按需开启.
    # resource_profile_hz / _downshift_*: 内置采样剖析 100Hz, >20min 自动降至 50Hz.
    # resource_profile_max_mb: speedscope 工件硬上限 (超限自动降采样, 方案 §3.5).
    # resource_profile_native: py-spy --native 增强模式 (需 SYS_PTRACE, 生产默认禁用).
    resource_profile_default: bool = False
    resource_profile_hz: int = 100
    resource_profile_downshift_after_s: float = 1200.0  # 20 min
    resource_profile_downshift_hz: int = 50
    resource_profile_max_mb: int = 10
    resource_profile_native: bool = False

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
    def fake_today_date(self):
        """Parsed FACTOR_FAKE_TODAY (date) or None (= real clock). Test hook."""
        import datetime as _dt

        s = (self.factor_fake_today or "").strip()
        if not s:
            return None
        try:
            return _dt.date.fromisoformat(s[:10])
        except ValueError:
            return None

    @property
    def fake_now_dt(self):
        """FACTOR_FAKE_TODAY as a datetime. Defaults to 23:59 so the scheduler's
        tick-time gate (hour:minute) always passes unless an explicit T-time is
        given (`YYYY-MM-DDTHH:MM`)."""
        import datetime as _dt

        d = self.fake_today_date
        if d is None:
            return None
        s = (self.factor_fake_today or "").strip()
        if "T" in s:
            try:
                return _dt.datetime.fromisoformat(s)
            except ValueError:
                pass
        return _dt.datetime.combine(d, _dt.time(23, 59))

    @property
    def evaluations_dir(self) -> str:
        return os.path.join(self.factor_store_root, "evaluations")

    @property
    def runs_dir(self) -> str:
        return os.path.join(self.factor_store_root, "runs")

    @property
    def worker_samples_dir(self) -> str:
        """Daily NDJSON slices of the 1Hz process sampler (资源画像方案 §3.1)."""
        return os.path.join(self.runs_dir, "worker_samples")

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
        s.worker_samples_dir,
        s.library_dir,
        s.inbox_dir,
        s.correlations_dir,
    ):
        os.makedirs(d, exist_ok=True)
