"""Data-quality write commands (ported from quant_web.data.quality, writes only):
acknowledge a quality issue.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from quant_data import quality_log

from ..deps import get_root, require_editor
from ..errors import ApiError
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


@router.patch("/issues/{issue_id}")
def acknowledge(issue_id: int, body: AckRequest, root: str = Depends(get_root),
                user=Depends(require_editor)):
    if quality_log.get_issue(issue_id, root=root) is None:
        raise ApiError("ISSUE_NOT_FOUND", f"质量问题 {issue_id} 不存在")
    row = quality_log.acknowledge(
        issue_id, by=user.username, reason=body.ack_reason,
        expires_at=body.ack_expires_at, acknowledged=body.acknowledged, root=root,
    )
    return _to_dict(row)
