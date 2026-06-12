"""Runtime configuration (Pydantic Settings, doc §2.3 env vars)."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    # data layer
    quant_data_root: str = "./data"

    # deployment identity (修复 A1/A2 · 五容器拓扑). Surfaced by /health (顶栏环境徽标)
    # and /health/deps (状态栏右侧 cluster·env 标签). compose injects ENVIRONMENT/CLUSTER.
    environment: str = "DEV"     # DEV | STAGING | PROD
    cluster: str = "local"       # free-form deploy/host tag, e.g. prod-east-1

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

    # --- JSONL → app_log 回填自愈 (可观测层重构方案 §8.3②) -------------------
    # backend 宕机窗口各层只落了本地 JSONL；恢复后扫描这些文件按 (trace_id,ts,event)
    # 去重补齐中枢缺口。log_file_dir 总会扫;额外目录 (哨兵/keeper/factor 的 JSONL,
    # 经 :ro 挂入) 用 ";" 列在 log_backfill_dirs。on_start 默认关 (避免拖慢启动)。
    log_backfill_dirs: str = ""
    log_backfill_on_start: bool = False
    log_backfill_since_hours: int = 48

    # --- layered log visibility (容器化重构 V3 §4.2) -----------------------
    # Which log `source`s each non-admin role may see, as a ";"-separated list of
    # "role:source,source" segments. admin is always all sources (not listed here).
    # The view+permission layers are how we make logs "feel" per-layer without
    # splitting storage — the app_log table stays a single ledger so trace joins
    # keep working (V3 §3.5/§3.6).
    #   editor now also sees the "monitor" layer (基础设施可观测); viewer never does
    #   (可观测层重构方案 §6.1).
    log_visible_sources: str = "editor:data,factor,backend,frontend,monitor;viewer:frontend"

    # --- 监控 / 外部观察层 (可观测层重构方案 §4.3 / §11.1) ------------------
    # The evaluator + sentinel detectors (D1–D12). All thresholds are env-tunable
    # and the UI (admin) can override them per-rule (落库覆盖 → monitor_rule).
    monitor_enabled: bool = True                 # master switch for the evaluator thread
    monitor_eval_tick_sec: int = 30              # evaluator loop cadence (D7–D12 + lifecycle)
    monitor_signals_token: str = ""              # sentinel → /monitor/signals (falls back to log_ingest_token)
    # D2 重启风暴 / D3 不健康 / D4 内存压力 (sentinel observes, evaluator windows)
    restart_storm_count: int = 3                 # ΔRestartCount ≥ N …
    restart_storm_window_min: int = 10           # … within W minutes → CRITICAL
    unhealthy_for_sec: int = 90                  # Health.Status==unhealthy 持续 ≥ S
    health_flap_count: int = 3                   # 或 W 内健康状态翻转 ≥ F 次
    health_flap_window_min: int = 10
    mem_pressure_pct: int = 90                   # mem_usage/mem_limit ≥ P% …
    mem_pressure_for_sec: int = 120              # … 持续 ≥ S (OOM 前预警)
    oom_clear_minutes: int = 15                  # D1 OOM auto-resolve window
    # D7 死人开关 / D8 错误率 / D9 管道 / D10 静默 / D11 前端崩溃 / D12 哨兵离线
    deadman_hours: int = 25                      # 定时任务 > Hh 无一次 success → ERROR
    err_rate_window_min: int = 15                # 错误率统计窗口
    err_rate_warn: int = 10                      # 窗口内 ERROR+ ≥ T → WARN
    err_rate_crit: int = 50                      # … ≥ Tc → ERROR
    pipeline_queue_pct: int = 80                 # 队列水位 ≥ Q% → WARN
    layer_silence_min: str = "data=20,factor=30,backend=15"  # 某层连续 M 分钟 0 条 → WARN
    fe_crash_per_session: int = 2                # 单会话前端崩溃 ≥ Cf → WARN
    sentinel_heartbeat_sec: int = 60             # 哨兵心跳超时 → WARN (监控的监控)
    alert_resolve_clear_sec: int = 60            # 条件连续不满足 S 秒 → 自动 resolve
    monitor_alert_min_level: str = "error"       # webhook 推送阈值 (info|warn|error|critical)
    # 哨兵上报的 conditions 仅在 sentinel_last_seen 距今 ≤ S 秒时才进台账 (哨兵盲时不臆断,
    # 改由 D12 兜底);超期则停喂 → 现有 auto_resolve 关闭它们 (可观测层重构方案 §6.5)。
    # 与 sentinel_heartbeat_sec 对齐 (≤ 它): 否则哨兵失联后 conditions 比 D12 多撑一段,
    # 出现「既报哨兵离线、又继续断言其陈旧判定」的双重断言窗口。哨兵每个快扫 (~2s) 都上报,60s 容差足够。
    signal_fresh_sec: int = 60

    # --- 第一层 (哨兵) 新检测器阈值 (L0/D13/D14/D15, 可观测层重构方案 §9.2) ----
    # 检测发生在哨兵 (detector_owner=sentinel)；这些值与哨兵 env 同名,compose 同时下发
    # 给两侧。后端仅用于「规则页」渲染默认阈值 (只读「哨兵配置」徽标, §7A.5)。
    disk_warn_pct: int = 85                      # D13 磁盘水位 WARN
    disk_crit_pct: int = 95                      # D13 磁盘水位 CRITICAL
    cpu_sat_pct: int = 90                        # D14 CPU 饱和阈值
    cpu_sat_for_sec: int = 120                   # D14 须持续秒数
    core_dump_rw_gb: float = 2.0                 # D15 可写层突增 ≥ G → 疑似 core-dump

    # --- D16 准入闸滞留 (内存治理方案 WP5) -----------------------------------
    # 阈值与 quant_admission 同名 env (ADMISSION_*) 同源,compose 同时下发两侧:
    # 闸自报 stuck/timeout 事件 (app_log),评估器把持续出现的标记升为告警。
    admission_deferred_warn_min: int = 10        # 同 task_class deferred 持续 > Td 分
    admission_draining_warn_min: int = 30        # 排干模式持续 > Tr 分
    liveness_probe_timeout_sec: int = 2          # L0 存活探测超时秒

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
    def layer_silence_by(self) -> dict[str, int]:
        """Parse ``layer_silence_min`` ("data=20,factor=30") → ``{layer: minutes}``."""
        out: dict[str, int] = {}
        for seg in self.layer_silence_min.split(","):
            layer, _, mins = seg.partition("=")
            layer = layer.strip()
            if layer and mins.strip().isdigit():
                out[layer] = int(mins.strip())
        return out

    @property
    def signals_token(self) -> str:
        """Service token the sentinel uses for /monitor/signals (reuses LOG_INGEST_TOKEN
        when MONITOR_SIGNALS_TOKEN is unset, so one shared secret配 the whole chain)."""
        return self.monitor_signals_token or self.log_ingest_token

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def sensitive_keys(self) -> list[str]:
        return [k.strip().lower() for k in self.log_sensitive_keys.split(",") if k.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
