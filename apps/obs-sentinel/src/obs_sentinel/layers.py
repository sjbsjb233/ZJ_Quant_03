"""Container → observability layer mapping (mirror of web-backend's, §7.3③).

Kept byte-identical in spirit to ``quant_web.monitor.evaluator.layer_of_container``
so the sentinel's frame and the backend's /monitor/health agree on which layer a
container belongs to (the frontend merges the two by layer key).
"""
from __future__ import annotations

LAYER_ORDER = ("frontend", "backend", "data", "factor", "monitor")


def layer_of_container(name: str) -> str:
    n = (name or "").lower()
    # 旁路/运维容器 (哨兵 + watchtower) 归 monitor 层,避免被误判成 backend 幽灵节点。
    if "sentinel" in n or "monitor" in n or "watchtower" in n:
        return "monitor"
    if "factor" in n:
        return "factor"
    if "data" in n or "keeper" in n or "writer" in n or "reader" in n:
        return "data"
    if "frontend" in n:
        return "frontend"
    return "backend"


def layer_rank(layer: str) -> int:
    try:
        return LAYER_ORDER.index(layer)
    except ValueError:
        return len(LAYER_ORDER)
