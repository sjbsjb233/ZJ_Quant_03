"""501 placeholders for the future modules (doc §3.3 / §8).

Every factor/strategy/monitor endpoint is registered now and returns 501 with
the planned ship date, so the frontend nav + API namespace exist from day one.
"""
from __future__ import annotations

from fastapi import APIRouter

from .errors import ApiError

# planned ship dates (doc §1.3 / terminal-shell module phases)
_PHASES = {
    "factors": {"module": "因子管理", "phase": "P1", "eta": "2026 Q3"},
    "strategies": {"module": "策略管理", "phase": "P2", "eta": "2026 Q4"},
    "monitor": {"module": "监控告警", "phase": "P3", "eta": "2027"},
}


def _not_implemented(kind: str):
    info = _PHASES[kind]

    def handler():  # no params: any path/query args are simply ignored
        raise ApiError(
            "NOT_IMPLEMENTED",
            f"{info['module']}模块尚未开发,计划 {info['eta']} 上线",
            details=info,
        )

    return handler


def _make_router(kind: str, paths: list[tuple[str, str]]) -> APIRouter:
    info = _PHASES[kind]
    router = APIRouter(prefix=f"/{kind}", tags=[f"{kind} (501)"])
    for method, path in paths:
        router.add_api_route(
            path, _not_implemented(kind), methods=[method],
            summary=f"[501] {info['module']} · {info['phase']}",
        )
    return router


factors_router = _make_router("factors", [
    ("GET", ""), ("GET", "/{name}"), ("GET", "/{name}/values"),
    ("GET", "/{name}/ic"), ("POST", "/{name}/recompute"),
    ("GET", "/{name}/dependencies"), ("GET", "/{name}/consumers"),
])
strategies_router = _make_router("strategies", [
    ("GET", ""), ("GET", "/{name}"), ("GET", "/backtests"),
    ("POST", "/backtests"), ("GET", "/backtests/{id}"),
    ("GET", "/backtests/{id}/positions"),
])
monitor_router = _make_router("monitor", [
    ("GET", "/alerts"), ("GET", "/rules"), ("POST", "/rules"),
    ("GET", "/channels"), ("POST", "/channels"),
])
