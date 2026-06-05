"""factor.db schema (design v2.1 §5.3).

Tables:
  - factor               : the canonical factor metadata
  - factor_version       : code-revision history per factor
  - factor_run           : a single user evaluation (the "business" unit)
  - eval_summary         : scalar metrics — drives list/tracking views
  - tasks                : persistent queue rows (the actual "queue")
  - task_duration_stat   : per-signature ETA estimates
  - universe             : preset + custom stock pools
  - universe_member      : custom-pool symbol membership
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.utcnow()


# ─────────────────────────────────────────────────────────────────────────────
class FactorRecord(Base):
    __tablename__ = "factor"

    factor_id: Mapped[str] = mapped_column(String, primary_key=True)
    base_name: Mapped[str] = mapped_column(String, index=True)
    display_name: Mapped[str] = mapped_column(String, default="")
    category: Mapped[str] = mapped_column(String, default="uncategorized")
    direction: Mapped[int] = mapped_column(Integer, default=1)
    source: Mapped[str] = mapped_column(String, default="builtin")  # builtin|upload|inbox
    status: Mapped[str] = mapped_column(String, default="enabled")  # enabled|disabled|archived
    tracking_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    tracking_universe_id: Mapped[str | None] = mapped_column(String, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    description: Mapped[str] = mapped_column(Text, default="")
    owner: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )


class FactorVersion(Base):
    __tablename__ = "factor_version"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    factor_id: Mapped[str] = mapped_column(
        ForeignKey("factor.factor_id"), index=True
    )
    version: Mapped[str] = mapped_column(String)
    class_name: Mapped[str] = mapped_column(String)
    code_sha256: Mapped[str] = mapped_column(String)
    code_path: Mapped[str | None] = mapped_column(String, nullable=True)
    requires_pit: Mapped[bool] = mapped_column(Boolean, default=False)
    dependencies: Mapped[list] = mapped_column(JSON, default=list)
    param_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    validation_report: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("ix_ver_factor_version", "factor_id", "version", unique=True),
    )


# ─────────────────────────────────────────────────────────────────────────────
class FactorRun(Base):
    __tablename__ = "factor_run"

    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    factor_id: Mapped[str] = mapped_column(String, index=True)
    version: Mapped[str] = mapped_column(String)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    run_config: Mapped[dict] = mapped_column(JSON, default=dict)
    # status ∈ queued|running|success|degraded|failed|canceled (设计 §3)
    status: Mapped[str] = mapped_column(String, default="queued")
    task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    initiated_by: Mapped[str] = mapped_column(String, default="")
    trace_id: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    # ── degradation台账 derived fields (设计 §3 / §8.1) ───────────────────
    degradation_level: Mapped[str] = mapped_column(String, default="none")  # none|partial|severe
    degradation_count: Mapped[int] = mapped_column(Integer, default=0)
    has_critical: Mapped[bool] = mapped_column(Boolean, default=False)
    # On failed-by-preflight: the structured block 详情 (list of CheckResult json)
    preflight: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)


# ─────────────────────────────────────────────────────────────────────────────
class EvalSummary(Base):
    __tablename__ = "eval_summary"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    factor_id: Mapped[str] = mapped_column(String, index=True)
    as_of: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    universe_id: Mapped[str] = mapped_column(String)
    primary_horizon: Mapped[int] = mapped_column(Integer, default=5)
    rebalance: Mapped[str] = mapped_column(String, default="W")
    ic_mean: Mapped[float] = mapped_column(Float, default=0.0)
    rank_ic_mean: Mapped[float] = mapped_column(Float, default=0.0)
    icir: Mapped[float] = mapped_column(Float, default=0.0)
    icir_annual: Mapped[float] = mapped_column(Float, default=0.0)
    ic_t_stat: Mapped[float] = mapped_column(Float, default=0.0)
    ic_win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    ls_annual_return: Mapped[float] = mapped_column(Float, default=0.0)
    ls_sharpe: Mapped[float] = mapped_column(Float, default=0.0)
    ls_max_drawdown: Mapped[float] = mapped_column(Float, default=0.0)
    long_annual_return: Mapped[float] = mapped_column(Float, default=0.0)
    short_annual_return: Mapped[float] = mapped_column(Float, default=0.0)
    turnover: Mapped[float] = mapped_column(Float, default=0.0)
    turnover_after_cost_ann: Mapped[float] = mapped_column(Float, default=0.0)
    coverage: Mapped[float] = mapped_column(Float, default=0.0)
    monotonic: Mapped[bool] = mapped_column(Boolean, default=False)
    ic_spark: Mapped[list] = mapped_column(JSON, default=list)
    # ── degradation snapshot (设计 §8.1) — denormalised onto eval_summary so the
    # library/tracking list pages can render the badge without reading artifacts.
    degradation_level: Mapped[str] = mapped_column(String, default="none")
    degradation_count: Mapped[int] = mapped_column(Integer, default=0)
    has_critical: Mapped[bool] = mapped_column(Boolean, default=False)


# ─────────────────────────────────────────────────────────────────────────────
class FactorRunDegradation(Base):
    """One row per :class:`DegradationEvent` of a run (设计 §8.2).

    Queryable/aggregatable (e.g. "which codes hit most this week" during the
    permissive 灰度 §12.3) — the full structured台账 also lives in the
    ``{run_id}.degradations.json`` artifact for the detail view.
    """

    __tablename__ = "factor_run_degradation"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    code: Mapped[str] = mapped_column(String)              # DG_*/PF_*
    category: Mapped[str] = mapped_column(String)
    severity: Mapped[str] = mapped_column(String, index=True)
    stage: Mapped[str | None] = mapped_column(String, nullable=True)
    config_key: Mapped[str | None] = mapped_column(String, nullable=True)
    title: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(Text)
    impact: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_action: Mapped[str | None] = mapped_column(Text, nullable=True)
    policy: Mapped[str | None] = mapped_column(String, nullable=True)  # degrade|ignore|block
    evidence_json: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[str | None] = mapped_column(String, nullable=True)

    __table_args__ = (
        Index("ix_frd_run", "run_id"),
        Index("ix_frd_sev", "severity"),
    )


# ─────────────────────────────────────────────────────────────────────────────
class Task(Base):
    __tablename__ = "tasks"

    task_id: Mapped[str] = mapped_column(String, primary_key=True)
    task_type: Mapped[str] = mapped_column(String)
    lane: Mapped[str] = mapped_column(String, default="interactive")
    priority: Mapped[int] = mapped_column(Integer, default=100)
    status: Mapped[str] = mapped_column(String, default="queued")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    factor_id: Mapped[str | None] = mapped_column(String, nullable=True)
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    est_duration_sec: Mapped[float] = mapped_column(Float, default=0.0)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=2)
    worker_id: Mapped[str | None] = mapped_column(String, nullable=True)
    heartbeat_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    initiated_by: Mapped[str] = mapped_column(String, default="")
    trace_id: Mapped[str | None] = mapped_column(String, nullable=True)
    result_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    # ── 篮子多级编排 (因子层重构 §4.2) — nullable/default keeps old rows valid ──
    # brun_id: the owning basket_run ("big task"); null = standalone task.
    # seq_no: in-group display order (= entry sort). gate: dependency门控 —
    # 'waiting' tasks are NOT claimable (basket_corr waits for all evals).
    brun_id: Mapped[str | None] = mapped_column(String, index=True, nullable=True)
    seq_no: Mapped[int] = mapped_column(Integer, default=0)
    gate: Mapped[str] = mapped_column(String, default="ready")  # ready|waiting

    __table_args__ = (
        Index("ix_claim", "status", "priority", "created_at"),
        Index("ix_task_lane_status", "lane", "status"),
        Index("ix_task_brun", "brun_id"),
    )


class WorkerHeartbeat(Base):
    """Dispatcher liveness beacon (修复 A1 §3.4 · 方案A).

    ``tasks.heartbeat_at`` only advances while a task is *running*, so an idle
    worker is indistinguishable from a crashed one. The dispatcher upserts its
    row here once per poll cycle, letting factor-api's ``/health`` report
    worker ``ok`` / ``stale`` / ``down`` even when the queue is empty.
    """

    __tablename__ = "worker_heartbeat"

    worker_id: Mapped[str] = mapped_column(String, primary_key=True)
    last_seen: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class TaskDurationStat(Base):
    __tablename__ = "task_duration_stat"

    signature: Mapped[str] = mapped_column(String, primary_key=True)
    p50_sec: Mapped[float] = mapped_column(Float, default=0.0)
    p80_sec: Mapped[float] = mapped_column(Float, default=0.0)
    sample_n: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )


# ─────────────────────────────────────────────────────────────────────────────
class Universe(Base):
    __tablename__ = "universe"

    universe_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    preset_flag: Mapped[str | None] = mapped_column(String, nullable=True)
    classification_system: Mapped[str | None] = mapped_column(String, nullable=True)
    default_industry_level: Mapped[int] = mapped_column(Integer, default=1)
    snapshot_retention_days: Mapped[int] = mapped_column(Integer, default=730)
    exclude_st: Mapped[bool] = mapped_column(Boolean, default=True)
    exclude_suspended: Mapped[bool] = mapped_column(Boolean, default=True)
    min_list_days: Mapped[int] = mapped_column(Integer, default=120)
    description: Mapped[str] = mapped_column(Text, default="")
    owner: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )


class UniverseMember(Base):
    __tablename__ = "universe_member"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    universe_id: Mapped[str] = mapped_column(
        ForeignKey("universe.universe_id"), index=True
    )
    symbol: Mapped[str] = mapped_column(String)

    __table_args__ = (
        Index("ix_uni_member", "universe_id", "symbol", unique=True),
    )


# ─────────────────────────────────────────────────────────────────────────────
class TrackingSchedule(Base):
    """Per-factor daily-tracking schedule. Empty by default (per user request,
    no auto tracking until a user opts in).
    """

    __tablename__ = "tracking_schedule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    factor_id: Mapped[str] = mapped_column(String, index=True, unique=True)
    universe_id: Mapped[str] = mapped_column(String)
    cron_hour: Mapped[int] = mapped_column(Integer, default=21)  # 21:00 default
    cron_minute: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    run_config: Mapped[dict] = mapped_column(JSON, default=dict)


# ═════════════════════════════════════════════════════════════════════════════
# 因子篮子 (Basket) 体系 — 因子层重构 §4.1
# ═════════════════════════════════════════════════════════════════════════════
class Basket(Base):
    """A long-lived research object: N entries sharing ONE common config, plus
    correlation/tracking settings. The basket itself runs as one "big task"."""

    __tablename__ = "basket"

    basket_id: Mapped[str] = mapped_column(String, primary_key=True)  # bsk_{hex16}
    name: Mapped[str] = mapped_column(String, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String, default="active")  # active|archived
    active_revision: Mapped[int] = mapped_column(Integer, default=1)
    # 配置改动后的待重算范围 (§9.2 失效矩阵的落点)
    stale_scope: Mapped[str] = mapped_column(String, default="none")  # none|partial|full
    stale_items: Mapped[list] = mapped_column(JSON, default=list)  # item_ids when partial
    stale_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    # 相关性设置
    correlation_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    correlation_method: Mapped[str] = mapped_column(String, default="pearson")  # pearson|spearman
    correlation_threshold: Mapped[float] = mapped_column(Float, default=0.7)
    corr_min_periods: Mapped[int] = mapped_column(Integer, default=12)
    acknowledge_degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    # 追踪设置 (§7) — EOD 数据就绪后的本地触发时刻 (默认 18:30)
    tracking_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    tracking_hour: Mapped[int] = mapped_column(Integer, default=18)
    tracking_minute: Mapped[int] = mapped_column(Integer, default=30)
    tracking_paused_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    # config_stale|data_refingerprint|manual|null
    last_tick_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    next_tick_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    owner: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )


class BasketRevision(Base):
    """One version of a basket's config (shared_config + entry set). Editing the
    config = producing a new revision (entries are config, not standalone)."""

    __tablename__ = "basket_revision"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    basket_id: Mapped[str] = mapped_column(
        ForeignKey("basket.basket_id"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)  # (basket_id, revision) unique
    shared_config: Mapped[dict] = mapped_column(JSON, default=dict)
    items: Mapped[list] = mapped_column(JSON, default=list)
    # [{item_id, factor_id, version, params, label, sort}]
    common_key: Mapped[str] = mapped_column(String, index=True, default="")
    comparable_key: Mapped[str] = mapped_column(String, index=True, default="")
    change_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    invalidation: Mapped[str] = mapped_column(String, default="none")  # none|items:..|full
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    author: Mapped[str] = mapped_column(String, default="")  # may be 'system' (§4.1 v1.3)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)

    __table_args__ = (
        Index("ix_brev_basket_rev", "basket_id", "revision", unique=True),
    )


class BasketItemState(Base):
    """Incremental ledger pointer — one row per item_key (§4.1 / §5)."""

    __tablename__ = "basket_item_state"

    item_key: Mapped[str] = mapped_column(String, primary_key=True)
    basket_id: Mapped[str] = mapped_column(String, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    item_id: Mapped[str] = mapped_column(String, index=True)
    factor_id: Mapped[str] = mapped_column(String, index=True)
    last_rebalance_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    last_settled_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    pending_dates: Mapped[list] = mapped_column(JSON, default=list)  # ISO date strings
    data_fingerprint: Mapped[str | None] = mapped_column(String, nullable=True)
    artifact_dir: Mapped[str] = mapped_column(String, default="")
    rows_factor: Mapped[int] = mapped_column(Integer, default=0)
    rows_ic: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )


class BasketRun(Base):
    """The "big task" in the queue — a basket's one generation of execution."""

    __tablename__ = "basket_run"

    brun_id: Mapped[str] = mapped_column(String, primary_key=True)  # brun_{hex16}
    basket_id: Mapped[str] = mapped_column(String, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    mode: Mapped[str] = mapped_column(String, default="full")
    # full|incremental|repair|corr_only
    trigger: Mapped[str] = mapped_column(String, default="manual")
    # manual|tracking|config_change|data_refingerprint
    with_correlation: Mapped[bool] = mapped_column(Boolean, default=True)
    resolved_start: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    resolved_end: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    as_of: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String, default="queued")
    # queued|running|success|degraded|partial|failed|canceled
    total_children: Mapped[int] = mapped_column(Integer, default=0)
    done_children: Mapped[int] = mapped_column(Integer, default=0)
    failed_children: Mapped[int] = mapped_column(Integer, default=0)
    degraded_children: Mapped[int] = mapped_column(Integer, default=0)
    est_total_sec: Mapped[float] = mapped_column(Float, default=0.0)
    corr_task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    snapshot_id: Mapped[str | None] = mapped_column(String, nullable=True)
    initiated_by: Mapped[str] = mapped_column(String, default="")
    trace_id: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (Index("ix_brun_basket", "basket_id", "created_at"),)


class BasketRunItem(Base):
    """Sub-task ↔ entry ↔ factor_run association (§4.1)."""

    __tablename__ = "basket_run_item"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    brun_id: Mapped[str] = mapped_column(String, index=True)
    item_id: Mapped[str] = mapped_column(String)
    item_key: Mapped[str] = mapped_column(String, index=True)
    factor_id: Mapped[str] = mapped_column(String)
    seq_no: Mapped[int] = mapped_column(Integer, default=0)
    task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="queued")
    inc_from: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    inc_to: Mapped[dt.date | None] = mapped_column(Date, nullable=True)


class BasketSnapshot(Base):
    """Immutable result slice produced by each generation (§4.1)."""

    __tablename__ = "basket_snapshot"

    snapshot_id: Mapped[str] = mapped_column(String, primary_key=True)  # snap_{hex16}
    basket_id: Mapped[str] = mapped_column(String, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    brun_id: Mapped[str] = mapped_column(String, index=True, default="")
    as_of: Mapped[dt.date | None] = mapped_column(Date, index=True, nullable=True)
    resolved_start: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    resolved_end: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    item_summaries: Mapped[dict] = mapped_column(JSON, default=dict)
    corr_artifact: Mapped[str | None] = mapped_column(String, nullable=True)
    corr_json: Mapped[dict] = mapped_column(JSON, default=dict)  # inline matrix/clusters/pairs
    basket_stats: Mapped[dict] = mapped_column(JSON, default=dict)
    excluded_items: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)


class FactorSelection(Base):
    """优选产出 · 不可变快照 (§11.7) — bound to a single (basket, snapshot)."""

    __tablename__ = "factor_selection"

    selection_id: Mapped[str] = mapped_column(String, primary_key=True)  # sel_{hex16}
    name: Mapped[str] = mapped_column(String, default="")
    basket_id: Mapped[str] = mapped_column(String, index=True)
    snapshot_id: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String, default="draft")  # draft|final|archived
    criteria_json: Mapped[dict] = mapped_column(JSON, default=dict)
    corr_method: Mapped[str] = mapped_column(String, default="pearson")
    corr_threshold: Mapped[float] = mapped_column(Float, default=0.7)
    corr_matrix_json: Mapped[dict] = mapped_column(JSON, default=dict)
    acknowledge_degraded: Mapped[bool] = mapped_column(Boolean, default=False)
    owner: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    finalized_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)


class FactorSelectionMember(Base):
    __tablename__ = "factor_selection_member"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    selection_id: Mapped[str] = mapped_column(String, index=True)
    rank: Mapped[int] = mapped_column(Integer, default=0)
    decision: Mapped[str] = mapped_column(String, default="kept")
    # kept|kept_manual|pruned_corr|pruned_capped
    item_key: Mapped[str] = mapped_column(String)
    item_id: Mapped[str] = mapped_column(String)
    factor_id: Mapped[str] = mapped_column(String)
    params_digest: Mapped[str] = mapped_column(String, default="")
    score: Mapped[float] = mapped_column(Float, default=0.0)
    metrics_json: Mapped[dict] = mapped_column(JSON, default=dict)
    blocked_by: Mapped[str | None] = mapped_column(String, nullable=True)
    rho: Mapped[float | None] = mapped_column(Float, nullable=True)

    __table_args__ = (
        Index("ix_selmember_uni", "selection_id", "item_key", unique=True),
    )


class ScreenPreset(Base):
    """Saved filters+weights combo (§11.9). Weight templates are builtin presets."""

    __tablename__ = "screen_preset"

    preset_id: Mapped[str] = mapped_column(String, primary_key=True)  # spre_{hex16}
    name: Mapped[str] = mapped_column(String, default="")
    owner: Mapped[str] = mapped_column(String, default="")
    filters_json: Mapped[dict] = mapped_column(JSON, default=dict)
    weights_json: Mapped[dict] = mapped_column(JSON, default=dict)
    builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )
