"""Audit recording + the @audit helper (doc §7.2).

Every destructive Web action (invalidate / supersede / promote / migrate /
vacuum / compact / source edits) records "who did what, when" and re-publishes a
domain event onto the bus (doc §8.4).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..db.models import AuditLog
from ..events import bus
from ..logging import app_logger


@dataclass
class Auditor:
    """Request-scoped audit writer (resolved via deps.get_auditor)."""

    db: Session
    actor: str
    ip: str | None = None
    trace_id: str | None = None
    _events: list = field(default_factory=list)

    def record(
        self,
        action: str,
        target_type: str | None = None,
        target_id: str | None = None,
        payload: dict | None = None,
        status: str = "success",
        error: str | None = None,
        event: str | None = None,
        event_payload: dict | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            actor=self.actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            request_payload=payload,
            result_status=status,
            error_message=error,
            trace_id=self.trace_id,
            ip=self.ip,
            occurred_at=dt.datetime.utcnow(),
        )
        self.db.add(entry)
        self.db.commit()
        self.db.refresh(entry)
        # Mirror the business action onto the technical timeline (doc §5.4) so a
        # trace search shows audit + app logs interleaved on one timeline.
        app_logger.info(
            f"audit.{action}", actor=self.actor, audit_id=entry.id,
            target_type=target_type, target_id=target_id, status=status,
            message=error if status == "failed" else None,
        )
        if event:
            bus.publish(event, {"actor": self.actor, "target_id": target_id, **(event_payload or {})})
        return entry


def _audit_query(
    db: Session,
    action: str | None = None,
    actor: str | None = None,
    target: str | None = None,
    status: str | None = None,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
):
    """构造带过滤条件的 audit_log 查询(不含排序/分页)。"""
    q = db.query(AuditLog)
    if action:
        q = q.filter(AuditLog.action == action)
    if actor:
        q = q.filter(AuditLog.actor == actor)
    if target:
        q = q.filter(AuditLog.target_id.like(f"%{target}%"))
    if status:
        q = q.filter(AuditLog.result_status == status)
    if start:
        q = q.filter(AuditLog.occurred_at >= start)
    if end:
        q = q.filter(AuditLog.occurred_at <= end)
    return q


def count_audit(
    db: Session,
    action: str | None = None,
    actor: str | None = None,
    target: str | None = None,
    status: str | None = None,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
) -> int:
    """匹配过滤条件的 audit_log 行数(用于分页 total,不物化行)。"""
    return _audit_query(
        db, action=action, actor=actor, target=target,
        status=status, start=start, end=end,
    ).count()


def list_audit(
    db: Session,
    action: str | None = None,
    actor: str | None = None,
    target: str | None = None,
    status: str | None = None,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> list[AuditLog]:
    # audit_log 永久保留且持续增长 —— 把分页下推到 SQL(LIMIT/OFFSET),
    # 避免每次查询都把整张表物化进内存再在 Python 里切片(doc §3.2 规则4)。
    q = _audit_query(
        db, action=action, actor=actor, target=target,
        status=status, start=start, end=end,
    ).order_by(AuditLog.occurred_at.desc())
    if page_size is not None:
        page = max(1, page or 1)
        page_size = max(1, min(page_size, 1000))
        q = q.offset((page - 1) * page_size).limit(page_size)
    return q.all()
