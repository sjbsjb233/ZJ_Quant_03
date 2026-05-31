"""聚合健康视图 (修复 A1 · 五容器拓扑)。

web-backend 是浏览器唯一能直连的后端；keeper / factor-api / factor-worker 都在 docker
内网,用户感知不到其死活。这里把"我依赖的下游通不通"聚合成状态栏的数据源。

关键：这**不是** docker liveness。docker healthcheck 打的是轻量 ``/health`` (只证明
web-backend 自己活着),绝不能把下游聚合塞进那里 —— 否则 keeper / factor 一挂会拖垮
web-backend 的 healthcheck → docker 误判 → 重启 web-backend (反的)。所以聚合单独走这里。

设计要点：
  - 本地探测 (db / warehouse) 同步、极快。
  - 下游探测 (keeper / factor) 并发、各自 2s 超时,任何异常都算 error,绝不抛。
  - 10s 进程内缓存,挡住 docker + N 个在线用户每 30s 轮询带来的探测风暴。
"""
from __future__ import annotations

import asyncio
import os
import time

import httpx
from fastapi import APIRouter, Depends

from .config import get_settings
from .db.base import SessionLocal
from .deps import get_current_user

from sqlalchemy import text

router = APIRouter(tags=["meta"])

_PROBE_TIMEOUT = 2.0          # 单个下游探测超时 (秒)
_CACHE_TTL = 10.0            # 聚合结果缓存 (秒) —— 挡 docker + 多用户轮询风暴
_cache: dict = {"at": 0.0, "data": None}


def _check_db() -> str:
    """web-backend 自身元数据库 (审计 + app_log 的 SQLite)。本地 SELECT 1。"""
    try:
        with SessionLocal() as s:
            s.execute(text("SELECT 1"))
        return "ok"
    except Exception:  # noqa: BLE001
        return "error"


def _check_warehouse() -> str:
    """parquet 数据湖 /data (:ro 挂载)。目录存在且可读即 ok。"""
    root = get_settings().quant_data_root
    return "ok" if (os.path.isdir(root) and os.access(root, os.R_OK)) else "error"


async def _probe(client: httpx.AsyncClient, url: str) -> tuple[str, dict | None]:
    """探一个下游 /health。返回 ('ok'|'error', json or None)。任何异常都算 error,绝不抛。"""
    try:
        r = await client.get(url, timeout=_PROBE_TIMEOUT)
        if r.status_code == 200:
            ctype = r.headers.get("content-type", "")
            return "ok", (r.json() if "json" in ctype else {})
        return "error", None
    except Exception:  # noqa: BLE001
        return "error", None


async def _aggregate() -> dict:
    s = get_settings()
    keeper_base = s.keeper_base or ""
    factor_base = s.factor_api_base or ""

    # 本地探测 (同步、极快)
    components: dict[str, dict] = {
        "db": {"state": _check_db()},
        "warehouse": {"state": _check_warehouse()},
    }

    # 下游并发探测。keeper_base 为空 (回滚态,写走本地) ⇒ 不探 keeper,该键不出现,
    # 前端按"不显示该点"处理。
    async with httpx.AsyncClient() as client:
        tasks: dict[str, asyncio.Future] = {}
        if keeper_base:
            tasks["keeper"] = _probe(client, keeper_base.rstrip("/") + "/health")
        if factor_base:
            tasks["factor"] = _probe(client, factor_base.rstrip("/") + "/health")
        results = await asyncio.gather(*tasks.values(), return_exceptions=False)
        for name, (state, body) in zip(tasks.keys(), results):
            components[name] = {"state": state}
            if name == "keeper" and body:
                # 调度是否开启 (ENABLE_SCHEDULER) —— 关掉是个值得知道的状态。
                components["keeper"]["scheduler"] = body.get("scheduler")
            if name == "factor" and body is not None:
                # factor-api /health 自带 worker 死活 (修复 A1 §3.4 方案A 的心跳推断)。
                components["factor"]["worker"] = body.get("worker", "unknown")

    # 整体：关键项 (db / warehouse / keeper) 全 ok 才算 ok。
    # keeper 在回滚态不在 components 里 ⇒ 其 state 为 None ⇒ 不参与判定。
    critical = [components.get(k, {}).get("state") for k in ("db", "warehouse", "keeper")]
    overall = "ok" if all(c == "ok" for c in critical if c is not None) else "error"

    return {
        "status": overall,
        "environment": s.environment,
        "cluster": s.cluster,
        "components": components,
    }


@router.get("/health/deps")
async def health_deps(user=Depends(get_current_user)):  # 登录可见即可
    """状态栏数据源：本地 db/warehouse + 并发探下游 keeper/factor,带 10s 缓存。"""
    now = time.monotonic()
    if _cache["data"] is not None and (now - _cache["at"]) < _CACHE_TTL:
        return _cache["data"]
    data = await _aggregate()
    _cache.update(at=now, data=data)
    return data
