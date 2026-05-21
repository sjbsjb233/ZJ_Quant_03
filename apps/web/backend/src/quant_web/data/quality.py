"""Data-quality routes (doc §4.7 / §7.1) -- issues, timeseries, acknowledge."""
from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends

from quant_data import quality_log

from ..audit.log import Auditor
from ..deps import get_auditor, get_current_user, get_root, require_editor
from ..errors import ApiError
from ..pagination import paginate
from .schemas import AckRequest

router = APIRouter(prefix="/quality", tags=["quality"])


def _to_dict(i: dict) -> dict:
    return {
        "id": i["id"],
        "batch_id": i["batch_id"],
        "target_table": i["target_table"],
        "check_name": i["check_name"],
        "level": i["level"],
        "count": i["count"],
        "message": i.get("message"),
        "sample": json.loads(i["sample"]) if i.get("sample") else None,
        "detected_at": i.get("detected_at"),
        "acknowledged": bool(i.get("acknowledged")),
        "ack_by": i.get("ack_by"),
        "ack_reason": i.get("ack_reason"),
        "ack_expires_at": i.get("ack_expires_at"),
    }


@router.get("/issues")
def issues(
    root: str = Depends(get_root), user=Depends(get_current_user),
    table: str | None = None, check_name: str | None = None, level: str | None = None,
    batch_id: str | None = None, acknowledged: bool | None = None,
    from_: dt.datetime | None = None, to: dt.datetime | None = None,
    page: int = 1, page_size: int = 20,
):
    levels = [x.strip() for x in level.split(",")] if level else None
    rows = quality_log.list_issues(
        table=table, check_name=check_name, level=levels, batch_id=batch_id,
        acknowledged=acknowledged, start=from_, end=to, root=root,
    )
    return paginate([_to_dict(i) for i in rows], page, page_size)


@router.get("/timeseries")
def timeseries(root: str = Depends(get_root), user=Depends(get_current_user),
               days: int = 30, group_by: str = "check_name"):
    try:
        series = quality_log.timeseries(days=days, group_by=group_by, root=root)
    except ValueError as e:
        raise ApiError("INVALID_PARAMETER", str(e)) from e
    return {"days": days, "group_by": group_by, "series": series}


@router.get("/by-table")
def by_table(root: str = Depends(get_root), user=Depends(get_current_user)):
    return {"items": quality_log.by_table(root=root),
            "by_check": quality_log.by_check(root=root)}


@router.patch("/issues/{issue_id}")
def acknowledge(
    issue_id: int, body: AckRequest,
    root: str = Depends(get_root),
    user=Depends(require_editor),
    auditor: Auditor = Depends(get_auditor),
):
    if quality_log.get_issue(issue_id, root=root) is None:
        raise ApiError("ISSUE_NOT_FOUND", f"质量问题 {issue_id} 不存在")
    row = quality_log.acknowledge(
        issue_id, by=user.username, reason=body.ack_reason,
        expires_at=body.ack_expires_at, acknowledged=body.acknowledged, root=root,
    )
    auditor.record("quality.acknowledge", "quality_issue", str(issue_id),
                   payload={"acknowledged": body.acknowledged, "reason": body.ack_reason},
                   event="quality.acknowledged")
    return _to_dict(row)
