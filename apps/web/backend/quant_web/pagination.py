"""Shared pagination contract (doc §3.2 rule 4 / §5.1)."""
from __future__ import annotations

from typing import Any


def paginate(items: list[Any], page: int, page_size: int) -> dict:
    """Slice a fully-materialised list into the standard envelope."""
    total = len(items)
    page = max(1, page)
    page_size = max(1, min(page_size, 1000))
    start = (page - 1) * page_size
    return {
        "items": items[start:start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
