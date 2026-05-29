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
    status: Mapped[str] = mapped_column(String, default="queued")
    task_id: Mapped[str | None] = mapped_column(String, nullable=True)
    initiated_by: Mapped[str] = mapped_column(String, default="")
    trace_id: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
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

    __table_args__ = (
        Index("ix_claim", "status", "priority", "created_at"),
        Index("ix_task_lane_status", "lane", "status"),
    )


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
