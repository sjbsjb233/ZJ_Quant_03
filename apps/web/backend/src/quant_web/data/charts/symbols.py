"""Symbol search (doc §4.6.2) -- shared by every chart's symbol picker."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends

from ...deps import get_current_user, get_reader

router = APIRouter(prefix="/symbols", tags=["charts:symbols"])

# Typeahead fires a request per keystroke (debounced). reference.symbols is a
# small single file, but a short TTL cache keyed by active_only makes the
# during-typing reads near-zero-IO without ever touching the large bars tables
# (方案 §3.2 P1). Cached as plain dict rows (already arrow->python converted).
_SEARCH_TTL_S = 45.0
_search_cache: dict[bool, tuple[float, list]] = {}


def _symbol_rows(reader, active_only: bool) -> list:
    now = time.monotonic()
    hit = _search_cache.get(active_only)
    if hit and now - hit[0] < _SEARCH_TTL_S:
        return hit[1]
    rows = reader.read_symbols(active_only=active_only).to_pylist()
    _search_cache[active_only] = (now, rows)
    return rows


@router.get("/search")
def search(q: str = "", active_only: bool = True, limit: int = 20,
           reader=Depends(get_reader), user=Depends(get_current_user)):
    rows = _symbol_rows(reader, active_only)
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
