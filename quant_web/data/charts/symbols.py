"""Symbol search (doc §4.6.2) -- shared by every chart's symbol picker."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from ...deps import get_current_user, get_reader

router = APIRouter(prefix="/symbols", tags=["charts:symbols"])


@router.get("/search")
def search(q: str = "", active_only: bool = True, limit: int = 20,
           reader=Depends(get_reader), user=Depends(get_current_user)):
    tbl = reader.read_symbols(active_only=active_only)
    rows = tbl.to_pylist()
    ql = q.strip().lower()
    if ql:
        rows = [r for r in rows
                if ql in (r.get("symbol") or "").lower()
                or ql in (r.get("name") or "").lower()]
    rows = rows[: max(1, min(limit, 100))]
    return {"items": [{
        "symbol": r["symbol"], "name": r.get("name"), "exchange": r.get("exchange"),
        "board": r.get("board"), "is_active": bool(r.get("is_active")),
    } for r in rows]}
