"""Data-source registry routes (doc §4.3 / §5.2.3)."""
from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends

from quant_data import governance
from quant_data.metadata import sources

from ..audit.log import Auditor
from ..deps import get_auditor, get_current_user, get_root, require_editor
from ..errors import ApiError
from .schemas import DiffRequest, SourceCreate, SourceUpdate

router = APIRouter(prefix="/sources", tags=["sources"])

# Tables whose cross-source diff must be bounded by a date window before it runs
# (a full-source diff materialises both sources in memory).
_DIFF_REQUIRE_WINDOW = {"raw.bars_daily", "raw.bars_minute", "raw.universe"}


def _to_dict(s: dict) -> dict:
    fm = s.get("field_mapping")
    return {
        "source_id": s["source_id"],
        "display_name": s.get("display_name"),
        "category": s.get("category"),
        "priority": s.get("priority"),
        "is_active": bool(s.get("is_active")),
        "field_mapping": json.loads(fm) if fm else None,
        "rate_limit_per_min": s.get("rate_limit_per_min"),
        "auth_method": s.get("auth_method"),
        "endpoint": s.get("endpoint"),
        "description": s.get("description"),
        "updated_at": s.get("_updated_at"),
    }


@router.get("")
def list_sources(root: str = Depends(get_root), user=Depends(get_current_user),
                 active_only: bool = False):
    rows = sources.list_sources(active_only=active_only, root=root)
    return {"items": [_to_dict(s) for s in rows], "total": len(rows)}


@router.post("/diff")
def diff(body: DiffRequest, root: str = Depends(get_root), user=Depends(get_current_user)):
    a = sources.get_source(body.source_a, root=root)
    b = sources.get_source(body.source_b, root=root)
    if a is None:
        raise ApiError("SOURCE_NOT_FOUND", f"source {body.source_a} 不存在")
    if b is None:
        raise ApiError("SOURCE_NOT_FOUND", f"source {body.source_b} 不存在")
    if not a["is_active"] or not b["is_active"]:
        raise ApiError("INVALID_PARAMETER", "跨源对比要求两个源均为 active")
    # High-cardinality tables: a diff materialises BOTH sources' rows into Python
    # (governance.diff_sources -> to_pylist), so without a date window it pulls an
    # entire source (18M+ rows) into the web backend and OOM-kills it. The 90-day
    # cap below previously only applied when start+end were *both* supplied, so
    # omitting dates skipped the guard entirely -- require the window for big
    # tables and enforce the cap unconditionally.
    if body.table in _DIFF_REQUIRE_WINDOW and not (body.start and body.end):
        raise ApiError("MISSING_PARAMETER", f"大表 {body.table} 跨源对比必须指定 start+end 日期范围")
    if body.start and body.end and (body.end - body.start).days > 90:
        raise ApiError("INVALID_DATE_RANGE", "时间范围不能超过 90 天")
    result = governance.diff_sources(
        body.table, body.source_a, body.source_b,
        start=body.start, end=body.end, columns=body.columns,
        sample=body.sample, root=root,
    )
    # keys are tuples -> make them JSON-friendly
    result["only_in_a"] = [list(k) for k in result["only_in_a"]]
    result["only_in_b"] = [list(k) for k in result["only_in_b"]]
    result["mismatches"] = [
        {"key": list(m["key"]),
         "diffs": {c: {"a": _safe(va), "b": _safe(vb)} for c, (va, vb) in m["diffs"].items()}}
        for m in result["mismatches"]
    ]
    return result


def _safe(v):
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    return v


@router.post("")
def create_source(
    body: SourceCreate,
    root: str = Depends(get_root),
    user=Depends(require_editor),
    auditor: Auditor = Depends(get_auditor),
):
    if sources.get_source(body.source_id, root=root) is not None:
        raise ApiError("DUPLICATE_RESOURCE", f"source {body.source_id} 已存在")
    row = sources.register_source(
        body.source_id, display_name=body.display_name, category=body.category,
        priority=body.priority, is_active=body.is_active, field_mapping=body.field_mapping,
        rate_limit_per_min=body.rate_limit_per_min, auth_method=body.auth_method,
        endpoint=body.endpoint, description=body.description, root=root,
    )
    auditor.record("source.create", "source", body.source_id,
                   payload=body.model_dump(exclude_none=True), event="source.registered")
    return _to_dict(row)


@router.get("/{source_id}")
def get_source(source_id: str, root: str = Depends(get_root), user=Depends(get_current_user)):
    s = sources.get_source(source_id, root=root)
    if s is None:
        raise ApiError("SOURCE_NOT_FOUND", f"source {source_id} 不存在")
    return _to_dict(s)


@router.patch("/{source_id}")
def update_source(
    source_id: str,
    body: SourceUpdate,
    root: str = Depends(get_root),
    user=Depends(require_editor),
    auditor: Auditor = Depends(get_auditor),
):
    if sources.get_source(source_id, root=root) is None:
        raise ApiError("SOURCE_NOT_FOUND", f"source {source_id} 不存在")
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise ApiError("INVALID_PARAMETER", "没有要更新的字段")
    row = sources.update_source(source_id, root=root, **fields)
    auditor.record("source.update", "source", source_id, payload=fields,
                   event="source.updated")
    return _to_dict(row)


@router.delete("/{source_id}")
def deactivate_source(
    source_id: str,
    root: str = Depends(get_root),
    user=Depends(require_editor),
    auditor: Auditor = Depends(get_auditor),
):
    if sources.get_source(source_id, root=root) is None:
        raise ApiError("SOURCE_NOT_FOUND", f"source {source_id} 不存在")
    row = sources.update_source(source_id, is_active=False, root=root)
    auditor.record("source.deactivate", "source", source_id, event="source.deactivated")
    return _to_dict(row)
