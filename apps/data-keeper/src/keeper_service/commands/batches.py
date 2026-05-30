"""Batch-ledger write commands (ported from quant_web.data.batches, writes only):
supersede + invalidate. Plain-dict bodies match the web wire shape exactly.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from quant_data.metadata import batches

from ..deps import get_root, require_editor
from ..errors import ApiError

router = APIRouter(prefix="/batches", tags=["batches"])


def _require_batch(batch_id: str, root: str) -> dict:
    b = batches.get_batch(batch_id, root=root)
    if b is None:
        raise ApiError("BATCH_NOT_FOUND", f"batch {batch_id} 不存在")
    return b


@router.post("/supersede")
def supersede(body: dict, root: str = Depends(get_root), user=Depends(require_editor)):
    old_id = body.get("old_batch_id")
    new_id = body.get("new_batch_id")
    old = batches.get_batch(old_id, root=root)
    new = batches.get_batch(new_id, root=root)
    if old is None:
        raise ApiError("BATCH_NOT_FOUND", f"old batch {old_id} 不存在", {"which": "old"})
    if new is None:
        raise ApiError("BATCH_NOT_FOUND", f"new batch {new_id} 不存在", {"which": "new"})
    if new["status"] != "success":
        raise ApiError("INVALID_NEW_BATCH", "新批次必须是 success 状态",
                       {"status": new["status"]})
    try:
        batches.supersede_batch(old_id, new_id, root=root)
    except ValueError as e:
        if "cycle" in str(e).lower():
            raise ApiError("CYCLE_DETECTED", str(e)) from e
        raise ApiError("INVALID_PARAMETER", str(e)) from e
    return {"old_batch_id": old_id, "new_batch_id": new_id, "ok": True}


@router.post("/{batch_id}/invalidate")
def invalidate(batch_id: str, body: dict, root: str = Depends(get_root),
               user=Depends(require_editor)):
    reason = (body or {}).get("reason", "")
    if not reason or len(reason) < 10:
        raise ApiError("INVALID_PARAMETER", "作废理由必填,至少 10 个字符")
    if len(reason) > 500:
        raise ApiError("INVALID_PARAMETER", "作废理由不超过 500 字符")
    b = _require_batch(batch_id, root)
    if b["status"] == "invalidated":
        raise ApiError("INVALID_STATE_TRANSITION", "该批次已作废",
                       {"current_state": "invalidated"})
    if b["status"] == "running":
        raise ApiError("INVALID_STATE_TRANSITION", "running 批次需先 finalize 再作废",
                       {"current_state": "running"})
    row = batches.invalidate_batch(batch_id, reason, root=root)
    return {
        "batch_id": batch_id,
        "status": "invalidated",
        "invalidated_at": row.get("invalidated_at"),
        "invalidated_reason": reason,
    }
