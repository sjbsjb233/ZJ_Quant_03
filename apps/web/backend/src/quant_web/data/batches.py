"""Batch ledger routes (doc §4.2 / §5.2.1-2) -- list, detail, lineage, invalidate."""
from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends, Query

from quant_data import governance, quality_log
from quant_data.metadata import batches

from ..audit.log import Auditor
from ..deps import get_auditor, get_current_user, get_reader, get_root, require_editor
from ..errors import ApiError
from ..pagination import paginate
from .serialize import table_payload

router = APIRouter(prefix="/batches", tags=["batches"])


def _duration(b: dict) -> int | None:
    s, f = b.get("started_at"), b.get("finished_at")
    if s and f:
        return int((f - s).total_seconds())
    return None


def _partition_count(b: dict) -> int:
    pk = b.get("partition_keys")
    if not pk:
        return 0
    try:
        return len(json.loads(pk))
    except Exception:  # noqa: BLE001
        return 0


def _summary(b: dict, q_batch_ids: set[str]) -> dict:
    return {
        "batch_id": b["batch_id"],
        "target_table": b["target_table"],
        "source": b["source"],
        "status": b["status"],
        "started_at": b.get("started_at"),
        "finished_at": b.get("finished_at"),
        "duration_sec": _duration(b),
        "row_count": b.get("row_count"),
        "partition_count": _partition_count(b),
        "has_quality_issues": b["batch_id"] in q_batch_ids,
        "has_supersedes": bool(b.get("supersedes")),
        "superseded_by": b.get("superseded_by"),
    }


def _csv(v: str | None) -> set[str] | None:
    return {x.strip() for x in v.split(",") if x.strip()} if v else None


@router.get("")
def list_batches(
    root: str = Depends(get_root),
    user=Depends(get_current_user),
    target_table: str | None = None,
    status: str | None = None,
    source: str | None = None,
    started_at_from: dt.date | None = None,
    started_at_to: dt.date | None = None,
    has_quality_issues: bool | None = None,
    has_supersedes: bool | None = None,
    has_superseded_by: bool | None = None,
    batch_id: str | None = None,
    parameters_hash: str | None = None,
    sort: str = "started_at:desc",
    page: int = 1,
    page_size: int = 20,
):
    rows = batches.list_batches(root=root)
    q_ids = {i["batch_id"] for i in quality_log.list_issues(root=root)}

    tt, st, sc = _csv(target_table), _csv(status), _csv(source)

    def keep(b: dict) -> bool:
        if tt and b["target_table"] not in tt:
            return False
        if st and b["status"] not in st:
            return False
        if sc and b["source"] not in sc:
            return False
        sa = b.get("started_at")
        if started_at_from and (sa is None or sa.date() < started_at_from):
            return False
        if started_at_to and (sa is None or sa.date() > started_at_to):
            return False
        if has_quality_issues is not None and (b["batch_id"] in q_ids) != has_quality_issues:
            return False
        if has_supersedes is not None and bool(b.get("supersedes")) != has_supersedes:
            return False
        if has_superseded_by is not None and bool(b.get("superseded_by")) != has_superseded_by:
            return False
        if batch_id and not b["batch_id"].startswith(batch_id):
            return False
        if parameters_hash and b.get("parameters_hash") != parameters_hash:
            return False
        return True

    filtered = [b for b in rows if keep(b)]
    field, _, direction = sort.partition(":")
    field = field if field in ("started_at", "finished_at", "row_count", "duration_sec",
                               "status", "target_table", "source") else "started_at"
    reverse = direction != "asc"

    def sort_key(b: dict):
        if field == "duration_sec":
            return _duration(b) or 0
        v = b.get(field)
        if isinstance(v, dt.datetime):
            return v
        return (v if v is not None else (0 if field == "row_count" else ""))

    filtered.sort(key=sort_key, reverse=reverse)
    summaries = [_summary(b, q_ids) for b in filtered]
    return paginate(summaries, page, page_size)


@router.get("/stats")
def stats(root: str = Depends(get_root), user=Depends(get_current_user),
          period: str = "7d"):
    now = dt.datetime.utcnow()
    rows = batches.list_batches(root=root)
    if period == "today":
        start = dt.datetime(now.year, now.month, now.day)
    elif period.endswith("d"):
        start = now - dt.timedelta(days=int(period[:-1] or 7))
    else:
        start = now - dt.timedelta(days=7)
    wk = [b for b in rows
          if (b.get("started_at") or dt.datetime.min) >= start]
    succ = sum(1 for b in wk if b["status"] == "success")
    fail = sum(1 for b in wk if b["status"] == "failed")
    run = sum(1 for b in wk if b["status"] == "running")
    inv = sum(1 for b in wk if b["status"] == "invalidated")
    rows_written = sum(b.get("row_count") or 0 for b in wk if b["status"] == "success")
    denom = succ + fail
    return {
        "period": period,
        "matched": len(wk),
        "total": len(rows),
        "success": succ,
        "failed": fail,
        "running": run,
        "invalidated": inv,
        "success_rate": round(succ / denom, 4) if denom else 1.0,
        "rows_written": rows_written,
    }


@router.post("/supersede")
def supersede(
    body: dict,
    root: str = Depends(get_root),
    user=Depends(require_editor),
    auditor: Auditor = Depends(get_auditor),
):
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
    auditor.record("batch.supersede", "batch", old_id,
                   payload={"old": old_id, "new": new_id},
                   event="batch.superseded")
    return {"old_batch_id": old_id, "new_batch_id": new_id, "ok": True}


def _require_batch(batch_id: str, root: str) -> dict:
    b = batches.get_batch(batch_id, root=root)
    if b is None:
        raise ApiError("BATCH_NOT_FOUND", f"batch {batch_id} 不存在")
    return b


@router.get("/{batch_id}")
def get_batch(batch_id: str, root: str = Depends(get_root), user=Depends(get_current_user)):
    b = _require_batch(batch_id, root)
    q_ids = {i["batch_id"] for i in quality_log.list_issues(batch_id=batch_id, root=root)}
    out = _summary(b, q_ids)
    out.update({
        "parameters": json.loads(b["parameters"]) if b.get("parameters") else None,
        "parameters_hash": b.get("parameters_hash"),
        "partition_keys": json.loads(b["partition_keys"]) if b.get("partition_keys") else [],
        "invalidated_at": b.get("invalidated_at"),
        "invalidated_reason": b.get("invalidated_reason"),
        "supersedes": b.get("supersedes"),
        "error_message": b.get("error_message"),
        "notes": b.get("notes"),
    })
    return out


@router.get("/{batch_id}/lineage")
def lineage(batch_id: str, root: str = Depends(get_root), user=Depends(get_current_user)):
    _require_batch(batch_id, root)
    chain = governance.lineage(batch_id, root=root)
    nodes, current = [], None
    for b in chain:
        is_current = b["status"] == "success" and not b.get("superseded_by")
        if is_current:
            current = b["batch_id"]
        nodes.append({
            "batch_id": b["batch_id"],
            "status": b["status"],
            "started_at": b.get("started_at"),
            "row_count": b.get("row_count"),
            "source": b["source"],
            "is_current": is_current,
        })
    return {"nodes": nodes, "current_node_id": current}


@router.get("/{batch_id}/quality")
def batch_quality(batch_id: str, root: str = Depends(get_root),
                  user=Depends(get_current_user)):
    _require_batch(batch_id, root)
    issues = quality_log.list_issues(batch_id=batch_id, root=root)
    out = [{
        "id": i["id"], "check_name": i["check_name"], "level": i["level"],
        "count": i["count"], "message": i.get("message"),
        "sample": json.loads(i["sample"]) if i.get("sample") else None,
        "acknowledged": bool(i.get("acknowledged")),
    } for i in issues]
    by_level = {"warn": sum(1 for i in out if i["level"] == "warn"),
                "error": sum(1 for i in out if i["level"] == "error")}
    return {"items": out, "total": len(out), "by_level": by_level}


@router.get("/{batch_id}/rows")
def batch_rows(batch_id: str, limit: int = 100, root: str = Depends(get_root),
               reader=Depends(get_reader), user=Depends(get_current_user)):
    b = _require_batch(batch_id, root)
    tbl = reader.read_batch(batch_id, b["target_table"])
    if limit and tbl.num_rows > limit:
        tbl = tbl.slice(0, limit)
    columns, items = table_payload(tbl)
    return {"columns": columns, "items": items, "total": tbl.num_rows,
            "target_table": b["target_table"]}


@router.post("/{batch_id}/invalidate")
def invalidate(
    batch_id: str,
    body: dict,
    root: str = Depends(get_root),
    user=Depends(require_editor),
    auditor: Auditor = Depends(get_auditor),
):
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
    auditor.record("batch.invalidate", "batch", batch_id, payload={"reason": reason},
                   event="batch.invalidated",
                   event_payload={"target_table": b["target_table"]})
    return {
        "batch_id": batch_id,
        "status": "invalidated",
        "invalidated_at": row.get("invalidated_at"),
        "invalidated_reason": reason,
    }
