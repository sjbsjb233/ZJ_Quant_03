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
        if event:
            bus.publish(event, {"actor": self.actor, "target_id": target_id, **(event_payload or {})})
        return entry


def list_audit(
    db: Session,
    action: str | None = None,
    actor: str | None = None,
    target: str | None = None,
    status: str | None = None,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
) -> list[AuditLog]:
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
    return q.order_by(AuditLog.occurred_at.desc()).all()
