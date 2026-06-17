"""Audit-log query route (doc §4.9 / §5)."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db.base import get_db
from ..deps import get_current_user
from .log import count_audit, list_audit

router = APIRouter(prefix="/audit", tags=["audit"])


def _to_dict(e) -> dict:
    return {
        "id": e.id,
        "actor": e.actor,
        "action": e.action,
        "target_type": e.target_type,
        "target_id": e.target_id,
        "request_payload": e.request_payload,
        "result_status": e.result_status,
        "error_message": e.error_message,
        "trace_id": e.trace_id,
        "ip": e.ip,
        "occurred_at": e.occurred_at,
    }


@router.get("")
def audit(
    db: Session = Depends(get_db), user=Depends(get_current_user),
    action: str | None = None, actor: str | None = None, target: str | None = None,
    status: str | None = None, from_: dt.datetime | None = None,
    to: dt.datetime | None = None, page: int = 1, page_size: int = 20,
):
    # 分页下推到 SQL: 只统计总数 + 取当前页,避免每次把永久增长的 audit_log 整表物化。
    page = max(1, page)
    page_size = max(1, min(page_size, 1000))
    total = count_audit(db, action=action, actor=actor, target=target, status=status,
                        start=from_, end=to)
    rows = list_audit(db, action=action, actor=actor, target=target, status=status,
                      start=from_, end=to, page=page, page_size=page_size)
    return {
        "items": [_to_dict(e) for e in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
