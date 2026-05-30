"""Source-registry write commands (ported from quant_web.data.sources, writes only).

Audit stays in web (V3 §4.5): these endpoints just validate + mutate via
quant_data with the keeper's :rw root. The web proxy records the audit row at the
forwarding boundary.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from quant_data.metadata import sources

from ..deps import get_root, require_editor
from ..errors import ApiError
from .schemas import SourceCreate, SourceUpdate

router = APIRouter(prefix="/sources", tags=["sources"])


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


@router.post("")
def create_source(body: SourceCreate, root: str = Depends(get_root),
                  user=Depends(require_editor)):
    if sources.get_source(body.source_id, root=root) is not None:
        raise ApiError("DUPLICATE_RESOURCE", f"source {body.source_id} 已存在")
    row = sources.register_source(
        body.source_id, display_name=body.display_name, category=body.category,
        priority=body.priority, is_active=body.is_active, field_mapping=body.field_mapping,
        rate_limit_per_min=body.rate_limit_per_min, auth_method=body.auth_method,
        endpoint=body.endpoint, description=body.description, root=root,
    )
    return _to_dict(row)


@router.patch("/{source_id}")
def update_source(source_id: str, body: SourceUpdate, root: str = Depends(get_root),
                  user=Depends(require_editor)):
    if sources.get_source(source_id, root=root) is None:
        raise ApiError("SOURCE_NOT_FOUND", f"source {source_id} 不存在")
    fields = body.model_dump(exclude_none=True)
    if not fields:
        raise ApiError("INVALID_PARAMETER", "没有要更新的字段")
    row = sources.update_source(source_id, root=root, **fields)
    return _to_dict(row)


@router.delete("/{source_id}")
def deactivate_source(source_id: str, root: str = Depends(get_root),
                      user=Depends(require_editor)):
    if sources.get_source(source_id, root=root) is None:
        raise ApiError("SOURCE_NOT_FOUND", f"source {source_id} 不存在")
    row = sources.update_source(source_id, is_active=False, root=root)
    return _to_dict(row)
