"""监控 / 外部观察 API (可观测层重构方案 §6.2).

落实并扩展预留的 ``/monitor/*`` (main.py 用真 router 替换 stub):

| route                              | role          | purpose                          |
|------------------------------------|---------------|----------------------------------|
| GET  /monitor/health               | 登录          | 状态栏/live 数据源 (§6.3)         |
| GET  /monitor/topology             | 登录          | 拓扑 tab 数据 (§6.4)              |
| GET  /monitor/alerts               | log-access    | 告警列表 (filter + 分页)          |
| POST /monitor/alerts/{id}/ack      | editor+       | 确认                              |
| POST /monitor/alerts/{id}/resolve  | editor+       | 手动解决                          |
| GET  /monitor/rules                | log-access    | 规则 + 当前参数                   |
| PUT  /monitor/rules/{id}           | admin         | 改阈值 / 启停 (落库覆盖)           |
| GET/POST /monitor/channels         | admin         | 推送渠道 (webhook)               |
| POST /monitor/signals              | 服务令牌      | 哨兵原始观测上报                  |

``/monitor/health`` 优先反映监控层裁决：某层即便此刻 ``/health`` 回 200，只要有活跃
critical/error 告警，该层一律渲染 ``down``。
"""
from __future__ import annotations

import datetime as dt
import time

import httpx
from fastapi import APIRouter, Body, Depends, Header, Query
from pydantic import BaseModel
from sqlalchemy import func

from .. import health as health_mod
from ..audit.log import Auditor
from ..config import get_settings
from ..db.base import SessionLocal
from ..db.models import AppLog, MonitorRule
from ..deps import (
    ALL_SOURCES,
    allowed_sources_for,
    get_auditor,
    get_current_user,
    require_admin,
    require_editor,
    require_log_access,
)
from ..errors import ApiError
from . import rules as rulemod
from .evaluator import get_evaluator, layer_of_container
from .store import get_alert_store, get_signal_state

router = APIRouter(prefix="/monitor", tags=["monitor"])

_PROCESS_START = dt.datetime.utcnow()
_ERROR_LEVELS = ("ERROR", "CRITICAL")
_FE_CRASH_EVENTS = ("react.boundary", "window.error", "promise.unhandled")
_PROBE_TIMEOUT = 2.0
_HEALTH_TTL = 5.0
_health_cache: dict = {"at": 0.0, "data": None}


# ── helpers ──────────────────────────────────────────────────────────────────
async def _probe(url: str) -> tuple[str, dict | None]:
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(url, timeout=_PROBE_TIMEOUT)
        if r.status_code == 200:
            ctype = r.headers.get("content-type", "")
            return "ok", (r.json() if "json" in ctype else {})
        return "error", None
    except Exception:  # noqa: BLE001
        return "error", None


def _layer_state(alerts: list[dict], *, liveness_down: bool = False,
                 degraded: bool = False) -> str:
    """Per-layer verdict (§6.3): active critical/error 告警 or liveness fail → down;
    active warn / 其它降级信号 → degraded; else ok."""
    has_down = liveness_down or any(a["severity"] in ("critical", "error") for a in alerts)
    has_warn = degraded or any(a["severity"] == "warn" for a in alerts)
    if has_down:
        return "down"
    if has_warn:
        return "degraded"
    return "ok"


def _worst(states: list[str]) -> str:
    if any(s == "down" for s in states):
        return "down"
    if any(s == "degraded" for s in states):
        return "degraded"
    return "ok"


def _container_for_layer(containers: dict[str, dict], layer: str) -> dict | None:
    for name, c in containers.items():
        if layer_of_container(name) == layer:
            return {**c, "name": name}
    return None


def _mem_pct(c: dict | None) -> int | None:
    if not c:
        return None
    usage, limit = c.get("mem_usage"), c.get("mem_limit")
    if isinstance(usage, (int, float)) and isinstance(limit, (int, float)) and limit > 0:
        return round(100 * usage / limit)
    pct = c.get("mem_pct")
    return int(pct) if isinstance(pct, (int, float)) else None


def _count_logs(*filters, since: dt.datetime) -> int:
    with SessionLocal() as db:
        q = db.query(func.count(AppLog.id)).filter(AppLog.ts >= since)
        for f in filters:
            q = q.filter(f)
        return int(q.scalar() or 0)


# ── GET /monitor/health — status bar / live 数据源 ───────────────────────────
@router.get("/health")
async def monitor_health(user=Depends(get_current_user)):
    now = time.monotonic()
    if _health_cache["data"] is not None and (now - _health_cache["at"]) < _HEALTH_TTL:
        return _health_cache["data"]
    data = await _build_health()
    _health_cache.update(at=now, data=data)
    return data


async def _build_health() -> dict:
    s = get_settings()
    alert_store = get_alert_store()
    signal = get_signal_state()
    ev = get_evaluator()
    by_layer = alert_store.active_by_layer() if alert_store else {}
    sig = signal.snapshot() if signal else {"containers": {}, "sentinel_last_seen": None}
    containers = sig["containers"]

    # local liveness (fast, sync) + downstream probes (concurrent enough at 5s cache)
    db_state = health_mod._check_db()
    wh_state = health_mod._check_warehouse()
    keeper_state, keeper_body = "none", None
    factor_state, factor_body = "none", None
    if s.keeper_base:
        keeper_state, keeper_body = await _probe(s.keeper_base.rstrip("/") + "/health")
    if s.factor_api_base:
        factor_state, factor_body = await _probe(s.factor_api_base.rstrip("/") + "/health")
    keeper_body = keeper_body or {}
    factor_body = factor_body or {}

    now = dt.datetime.utcnow()
    win = now - dt.timedelta(minutes=s.err_rate_window_min)
    day = now - dt.timedelta(hours=24)
    keeper_sig = _container_for_layer(containers, "data")
    factor_sig = _container_for_layer(containers, "factor")
    worker = factor_body.get("worker", "unknown")

    layers = {
        "frontend": {
            "state": _layer_state(by_layer.get("frontend", [])),
            "crash_24h": _count_logs(AppLog.source == "frontend",
                                     AppLog.event.in_(_FE_CRASH_EVENTS), since=day),
        },
        "backend": {
            "state": _layer_state(by_layer.get("backend", []),
                                  liveness_down=(db_state != "ok")),
            "uptime_s": int((now - _PROCESS_START).total_seconds()),
            "err_rate_15m": _count_logs(AppLog.source == "backend",
                                        AppLog.level.in_(_ERROR_LEVELS), since=win),
        },
        "data": {
            "state": _layer_state(
                by_layer.get("data", []),
                liveness_down=bool(s.keeper_base) and keeper_state == "error",
            ),
            "uptime_s": keeper_body.get("uptime_seconds"),
            "restart_count": (keeper_sig or {}).get("restart_count"),
            "oom_killed": bool((keeper_sig or {}).get("oom_killed")),
            "last_job_success_at": keeper_body.get("last_job_success_at"),
            "jobs_running": keeper_body.get("jobs_running"),
            "jobs_failed_24h": keeper_body.get("jobs_failed_24h"),
            "mem_pct": _mem_pct(keeper_sig),
            "scheduler": keeper_body.get("scheduler"),
        },
        "factor": {
            "state": _layer_state(
                by_layer.get("factor", []),
                liveness_down=bool(s.factor_api_base) and factor_state == "error",
                degraded=worker in ("stale", "down"),
            ),
            "worker": worker,
            "restart_count": (factor_sig or {}).get("restart_count"),
            "mem_pct": _mem_pct(factor_sig),
        },
        "monitor": {
            "state": _layer_state(by_layer.get("monitor", [])),
            "sentinel_last_seen": sig["sentinel_last_seen"],
            "eval_last_run": ev.eval_last_run if ev else None,
        },
    }

    # 整体 = 各关键层 (db/warehouse/data/monitor) 取最差
    overall = _worst([
        "down" if db_state != "ok" else "ok",
        "down" if wh_state != "ok" else "ok",
        layers["data"]["state"],
        layers["monitor"]["state"],
    ])
    return {
        "status": overall, "environment": s.environment, "cluster": s.cluster,
        "as_of": now, "layers": layers,
        "components": {"db": {"state": db_state}, "warehouse": {"state": wh_state},
                       **({"keeper": {"state": keeper_state}} if s.keeper_base else {}),
                       **({"factor": {"state": factor_state}} if s.factor_api_base else {})},
        "alerts": alert_store.summary() if alert_store else {"firing": 0, "by_severity": {}, "top": []},
    }


# ── GET /monitor/topology — 外部观察 ─────────────────────────────────────────
@router.get("/topology")
async def monitor_topology(user=Depends(get_current_user)):
    s = get_settings()
    signal = get_signal_state()
    alert_store = get_alert_store()
    sig = signal.snapshot() if signal else {"observed_at": None, "containers": {}}
    active = alert_store.active() if alert_store else []
    alerts_by_target: dict[str, list[str]] = {}
    for a in active:
        if a.get("target"):
            alerts_by_target.setdefault(a["target"], []).append(a["rule_id"])

    keeper_body = {}
    if s.keeper_base:
        _, keeper_body = await _probe(s.keeper_base.rstrip("/") + "/health")
        keeper_body = keeper_body or {}

    containers = []
    for name, c in sig["containers"].items():
        layer = layer_of_container(name)
        row = {
            "name": name, "layer": layer, "status": c.get("status", "unknown"),
            "health": c.get("health", "none"), "restart_count": c.get("restart_count", 0),
            "oom_killed": bool(c.get("oom_killed")), "mem_usage": c.get("mem_usage"),
            "mem_limit": c.get("mem_limit"), "mem_pct": _mem_pct(c),
            "uptime_s": c.get("uptime_s"), "last_seen": c.get("_seen_at"),
            "alerts": alerts_by_target.get(name, []),
        }
        if layer == "data" and keeper_body:
            row["uptime_s"] = keeper_body.get("uptime_seconds", row["uptime_s"])
            row["jobs_running"] = keeper_body.get("jobs_running")
            row["jobs_failed_24h"] = keeper_body.get("jobs_failed_24h")
        containers.append(row)

    # 无哨兵信号 (dev / 哨兵未上线) → 用活体探测合成一份最小拓扑，面板不空白。
    if not containers:
        containers = _synthesize_topology(s, keeper_body, alerts_by_target)
    containers.sort(key=lambda r: ("frontend", "backend", "data", "factor", "monitor").index(r["layer"]) if r["layer"] in ("frontend", "backend", "data", "factor", "monitor") else 9)
    return {"observed_at": sig.get("observed_at"), "containers": containers,
            "sentinel": signal is not None and sig.get("observed_at") is not None}


# ── GET /monitor/tasks — 死人开关面板 + keeper 任务台账 (§7.3⑦) ──────────────
@router.get("/tasks")
async def monitor_tasks(user=Depends(require_log_access), limit: int = 50):
    s = get_settings()
    out = {"deadman_hours": s.deadman_hours, "scheduler_tasks": {}, "jobs": [],
           "keeper": bool(s.keeper_base)}
    # RBAC 收窄 (折中收窄): 任务台账是 data 层视图；无 data 可见权的角色 (viewer) 看空。
    if "data" not in allowed_sources_for(user):
        return out
    if not s.keeper_base:
        return out
    base = s.keeper_base.rstrip("/")
    # forward the caller's identity so keeper's current_user accepts the read.
    headers = {"X-Forwarded-User": user.username, "X-Forwarded-Role": user.role}
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
            h = await client.get(base + "/health")
            if h.status_code == 200:
                out["scheduler_tasks"] = (h.json() or {}).get("scheduler_tasks", {})
            j = await client.get(base + "/jobs/recent", params={"limit": limit}, headers=headers)
            if j.status_code == 200:
                out["jobs"] = (j.json() or {}).get("items", [])
    except Exception:  # noqa: BLE001 - tasks tab degrades gracefully if keeper is down
        pass
    return out


def _synthesize_topology(s, keeper_body, alerts_by_target) -> list[dict]:
    base = [
        {"name": "web-frontend", "layer": "frontend"},
        {"name": "web-backend", "layer": "backend"},
        {"name": "data-keeper", "layer": "data"},
        {"name": "factor-api", "layer": "factor"},
        {"name": "factor-worker", "layer": "factor"},
    ]
    out = []
    for b in base:
        row = {**b, "status": "unknown", "health": "none", "restart_count": None,
               "oom_killed": False, "mem_usage": None, "mem_limit": None, "mem_pct": None,
               "uptime_s": None, "last_seen": None, "alerts": alerts_by_target.get(b["name"], []),
               "synthetic": True}
        if b["layer"] == "data" and keeper_body:
            row["status"] = "running"
            row["uptime_s"] = keeper_body.get("uptime_seconds")
            row["jobs_running"] = keeper_body.get("jobs_running")
        out.append(row)
    return out


# ── GET /monitor/alerts (+ ack / resolve) ────────────────────────────────────
@router.get("/alerts")
def list_alerts(user=Depends(require_log_access),
                status: str | None = None, severity: str | None = None,
                layer: str | None = None, target: str | None = None,
                search: str | None = None, page: int = 1, page_size: int = 50):
    store = get_alert_store()
    if store is None:
        return {"items": [], "total": 0, "page": page, "page_size": page_size}
    # RBAC 收窄 (可观测层方案 §8 · 折中收窄)：告警按角色可见层收窄 (viewer → 仅 frontend
    # 层告警)；admin (全开) 不收窄。health/topology 仍全员可见 (状态栏需要全局健康)。
    allowed = allowed_sources_for(user)
    allowed_layers = None if allowed >= ALL_SOURCES else allowed
    return store.list(status=status, severity=severity, layer=layer, target=target,
                      search=search, allowed_layers=allowed_layers, page=page, page_size=page_size)


@router.post("/alerts/{alert_id}/ack")
def ack_alert(alert_id: int, user=Depends(require_editor),
              auditor: Auditor = Depends(get_auditor)):
    store = get_alert_store()
    alert = store.ack(alert_id, user.username) if store else None
    if alert is None:
        raise ApiError("NOT_FOUND", f"告警 {alert_id} 不存在")
    _audit_safe(auditor, "monitor.alert.ack", alert)
    return alert


@router.post("/alerts/{alert_id}/resolve")
def resolve_alert(alert_id: int, user=Depends(require_editor),
                  auditor: Auditor = Depends(get_auditor)):
    store = get_alert_store()
    alert = store.resolve(alert_id, user.username) if store else None
    if alert is None:
        raise ApiError("NOT_FOUND", f"告警 {alert_id} 不存在")
    _audit_safe(auditor, "monitor.alert.resolve", alert)
    return alert


def _audit_safe(auditor: Auditor, action: str, alert: dict) -> None:
    try:
        auditor.record(action, "monitor_alert", str(alert["id"]),
                       payload={"rule_id": alert["rule_id"], "layer": alert["layer"]})
    except Exception:  # noqa: BLE001 - audit must never break the action
        pass


# ── GET /monitor/rules (+ PUT) ───────────────────────────────────────────────
@router.get("/rules")
def list_rules(user=Depends(require_log_access)):
    with SessionLocal() as db:
        return {"items": rulemod.list_rules(get_settings(), db)}


class RuleUpdate(BaseModel):
    params: dict | None = None
    enabled: bool | None = None


@router.put("/rules/{rule_id}")
def update_rule(rule_id: str, body: RuleUpdate, user=Depends(require_admin),
                auditor: Auditor = Depends(get_auditor)):
    if rule_id not in rulemod.RULES_BY_ID:
        raise ApiError("NOT_FOUND", f"规则 {rule_id} 不存在")
    with SessionLocal() as db:
        row = db.get(MonitorRule, rule_id)
        if row is None:
            row = MonitorRule(rule_id=rule_id)
            db.add(row)
        if body.params is not None:
            merged = dict(row.params or {})
            merged.update(body.params)
            row.params = merged
        if body.enabled is not None:
            row.enabled = body.enabled
        row.updated_by = user.username
        row.updated_at = dt.datetime.utcnow()
        db.commit()
        result = rulemod.list_rules(get_settings(), db)
    try:
        auditor.record("monitor.rule.update", "monitor_rule", rule_id,
                       payload=body.model_dump(exclude_none=True))
    except Exception:  # noqa: BLE001
        pass
    item = next((r for r in result if r["id"] == rule_id), None)
    return item or {"id": rule_id}


# ── GET/POST /monitor/channels — 推送渠道 (webhook) ──────────────────────────
@router.get("/channels")
def get_channels(user=Depends(require_admin)):
    ev = get_evaluator()
    s = get_settings()
    url = ev.webhook if ev else s.log_alert_webhook
    return {"webhook_configured": bool(url), "webhook_masked": _mask(url),
            "min_level": s.monitor_alert_min_level}


@router.post("/channels")
def set_channels(user=Depends(require_admin), webhook: str | None = Body(None, embed=True),
                 auditor: Auditor = Depends(get_auditor)):
    ev = get_evaluator()
    if ev is None:
        raise ApiError("SERVICE_UNAVAILABLE", "监控评估器未启用")
    ev.set_webhook(webhook)
    try:
        auditor.record("monitor.channel.update", "monitor_channel", "webhook",
                       payload={"configured": bool(webhook)})
    except Exception:  # noqa: BLE001
        pass
    return {"webhook_configured": bool(ev.webhook), "webhook_masked": _mask(ev.webhook)}


def _mask(url: str | None) -> str | None:
    if not url:
        return None
    return url[:18] + "…" if len(url) > 20 else url


# ── POST /monitor/signals — 哨兵原始观测上报 (服务令牌) ──────────────────────
class SignalPayload(BaseModel):
    observed_at: str | None = None
    containers: list[dict]


@router.post("/signals")
def ingest_signals(body: SignalPayload,
                   x_monitor_token: str | None = Header(None, alias="X-Monitor-Token"),
                   x_log_token: str | None = Header(None, alias="X-Log-Token")):
    s = get_settings()
    expected = s.signals_token
    token = x_monitor_token or x_log_token
    if not expected or not token or token != expected:
        raise ApiError("UNAUTHENTICATED", "无效的监控信号令牌")
    signal = get_signal_state()
    if signal is None:
        raise ApiError("SERVICE_UNAVAILABLE", "监控信号缓冲未就绪")
    accepted = signal.ingest(body.model_dump())
    return {"accepted": accepted}
