"""ORM models for keeper.db: the Job ledger (+ trace_id) for the in-process runner.

This mirrors quant_web.db.models.Job but adds a ``trace_id`` column so an
autonomous run (scheduler) or a forwarded UI command can be traced end-to-end:
the runner re-binds this trace_id when the worker thread executes (V3 §6.4), so
"按钮 → 命令 → 重写文件" and "采集 → 入库 → 质检 → 批次登记" share one trace.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


def _utcnow() -> dt.datetime:
    return dt.datetime.utcnow()


class Job(Base):
    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(32))  # field_migration|vacuum|compact|snapshot|crawl_*|quality_scan
    status: Mapped[str] = mapped_column(String(16), default="pending")
    progress_current: Mapped[int] = mapped_column(Integer, default=0)
    progress_total: Mapped[int] = mapped_column(Integer, default=0)
    progress_unit: Mapped[str] = mapped_column(String(32), default="items")
    log: Mapped[list | None] = mapped_column(JSON, default=list)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    initiated_by: Mapped[str] = mapped_column(String(64), index=True)
    # the trace_id this job runs under — set at enqueue, re-bound on execute.
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
