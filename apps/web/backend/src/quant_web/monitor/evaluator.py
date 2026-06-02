"""Monitor evaluator — the background守护线程 that turns signals + logs into a
managed alert lifecycle (可观测层重构方案 §3.2 / §6.2).

每 ``MONITOR_EVAL_TICK_SEC`` 一跳:
  1. 消费最近哨兵信号 → 判定 D1–D4 (哨兵给瞬时，评估器维护时间窗/持续态);
  2. 从 app_log 捞 job.*.interrupted / job.*.timeout → D5/D6 进入告警台账;
  3. D7 死人开关 (查 keeper /health.scheduler_tasks);
  4. D8 错误率、D9 管道健康、D10 某层静默、D11 前端崩溃、D12 哨兵离线;
  5. 告警生命周期: 去重 trigger / 自动 resolve / 事件总线 publish / webhook 推送 /
     以 source=monitor 落一条日志 (供实时流 + trace)。

线程模型与 ``AppLogStore`` 一致 (daemon、整跳与每个检测器都 try/except 包裹，绝不让
评估器崩溃影响请求线程)。第一跳延后一个 tick，所以短测试永不真正评估。
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import urllib.request

import httpx
from sqlalchemy import func

from ..config import Settings
from ..db.models import AppLog
from ..events import bus
from ..logging import get_store
from . import rules as rulemod
from .store import AlertStore, SignalState

_SEV_RANK = {"info": 0, "warn": 1, "error": 2, "critical": 3}
_ERROR_LEVELS = ("ERROR", "CRITICAL")
_FE_CRASH_EVENTS = ("react.boundary", "window.error", "promise.unhandled")
_KEEPER_TIMEOUT = 2.0


def layer_of_container(name: str) -> str:
    """Map a real container name → its observability layer (可观测层方案 §7.3③)."""
    n = (name or "").lower()
    if "sentinel" in n or "monitor" in n:
        return "monitor"
    if "factor" in n:
        return "factor"
    if "data" in n or "keeper" in n or "writer" in n or "reader" in n:
        return "data"
    if "frontend" in n:
        return "frontend"
    return "backend"


class MonitorEvaluator:
    def __init__(self, settings: Settings, session_factory, alert_store: AlertStore,
                 signal_state: SignalState, keeper_base: str = "") -> None:
        self._s = settings
        self._sf = session_factory
        self._alerts = alert_store
        self._signals = signal_state
        self._keeper_base = (keeper_base or "").rstrip("/")
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.eval_last_run: dt.datetime | None = None
        self._webhook_override: str | None = None        # /monitor/channels POST
        # per-condition "since" timestamps for duration rules (D3/D4)
        self._since: dict[tuple, dt.datetime] = {}

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> "MonitorEvaluator":
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="monitor-eval", daemon=True)
            self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def _run(self) -> None:
        # delay the first evaluation one tick so short-lived test apps never run a
        # full DB/HTTP pass (mirrors how AppLogStore stays inert under TestClient).
        while not self._stop.wait(max(2, self._s.monitor_eval_tick_sec)):
            try:
                self._tick()
            except Exception:  # noqa: BLE001 - the loop must never die
                pass

    # -- one evaluation pass -------------------------------------------------
    def _tick(self) -> None:
        active: dict[tuple, dict] = {}   # (rule_id, layer, target) -> alert payload
        overrides = self._overrides()
        for detector in (self._d1_d4_from_signals, self._d5_d6_jobs, self._d7_deadman,
                         self._d8_error_rate, self._d9_pipeline, self._d10_silence,
                         self._d11_fe_crash, self._d12_sentinel):
            try:
                detector(active, overrides)
            except Exception:  # noqa: BLE001 - one bad detector can't stop the rest
                pass

        # fire / bump every active condition
        for (rule_id, layer, target), payload in active.items():
            try:
                alert, is_new = self._alerts.trigger(
                    rule_id=rule_id, severity=payload["severity"], layer=layer, target=target,
                    title=payload["title"], detail=payload.get("detail"),
                    trace_id=payload.get("trace_id"))
                if is_new:
                    self._on_new_alert(alert)
            except Exception:  # noqa: BLE001
                pass

        # auto-resolve everything no longer active
        try:
            resolved = self._alerts.auto_resolve(active.keys(), self._s.alert_resolve_clear_sec)
            for a in resolved:
                self._on_resolved_alert(a)
        except Exception:  # noqa: BLE001
            pass
        self.eval_last_run = dt.datetime.utcnow()

    def _overrides(self) -> dict[str, dict]:
        try:
            with self._sf() as db:
                return rulemod.load_overrides(db)
        except Exception:  # noqa: BLE001
            return {}

    def _enabled(self, overrides, rule_id: str) -> bool:
        rule = rulemod.RULES_BY_ID.get(rule_id)
        return bool(rule) and rulemod.is_enabled(rule, overrides)

    def _p(self, overrides, rule_id: str, key: str, default: int) -> int:
        return rulemod.param(self._s, overrides, rule_id, key, default)

    # -- D1–D4 (sentinel signals, windowed/duration here) -------------------
    def _d1_d4_from_signals(self, active: dict, overrides: dict) -> None:
        snap = self._signals.snapshot()
        now = dt.datetime.utcnow()
        for name, c in snap["containers"].items():
            layer = layer_of_container(name)
            mem_pct = _mem_pct(c)
            # D1 — OOMKilled
            if self._enabled(overrides, "D1.oom_killed") and c.get("oom_killed"):
                active[("D1.oom_killed", layer, name)] = {
                    "severity": "critical",
                    "title": f"{name} 被内核 OOM 强杀",
                    "detail": {"container": name, "oom_killed": True,
                               "restart_count": c.get("restart_count")},
                }
            # D2 — restart storm
            if self._enabled(overrides, "D2.restart_storm"):
                n = self._p(overrides, "D2.restart_storm", "count", 3)
                w = self._p(overrides, "D2.restart_storm", "window_min", 10)
                delta = self._signals.restart_delta(name, w)
                if delta >= n:
                    active[("D2.restart_storm", layer, name)] = {
                        "severity": "critical",
                        "title": f"{name} 在 {w}m 内重启 {delta} 次",
                        "detail": {"container": name, "restart_delta": delta,
                                   "window_min": w, "oom_killed": bool(c.get("oom_killed"))},
                    }
            # D3 — unhealthy (duration tracked via _since)
            if self._enabled(overrides, "D3.unhealthy"):
                s = self._p(overrides, "D3.unhealthy", "for_sec", 90)
                key = ("D3", name)
                if c.get("health") == "unhealthy":
                    since = self._since.setdefault(key, now)
                    if (now - since).total_seconds() >= s:
                        active[("D3.unhealthy", layer, name)] = {
                            "severity": "error",
                            "title": f"{name} 持续不健康 ≥ {s}s",
                            "detail": {"container": name, "health": "unhealthy", "for_sec": s},
                        }
                else:
                    self._since.pop(key, None)
            # D4 — memory pressure (duration)
            if self._enabled(overrides, "D4.mem_pressure") and mem_pct is not None:
                p = self._p(overrides, "D4.mem_pressure", "pct", 90)
                s = self._p(overrides, "D4.mem_pressure", "for_sec", 120)
                key = ("D4", name)
                if mem_pct >= p:
                    since = self._since.setdefault(key, now)
                    if (now - since).total_seconds() >= s:
                        active[("D4.mem_pressure", layer, name)] = {
                            "severity": "warn",
                            "title": f"{name} 内存压力 {mem_pct}% 持续 ≥ {s}s",
                            "detail": {"container": name, "mem_pct": mem_pct, "pct": p, "for_sec": s},
                        }
                else:
                    self._since.pop(key, None)

    # -- D5/D6 (surface keeper's interrupted/timeout ERRORs as alerts) ------
    def _d5_d6_jobs(self, active: dict, overrides: dict) -> None:
        window = self._p(overrides, "D8.error_rate", "window_min", 15)
        since = dt.datetime.utcnow() - dt.timedelta(minutes=max(window, 30))
        with self._sf() as db:
            rows = (db.query(AppLog)
                    .filter(AppLog.ts >= since, AppLog.source == "data",
                            AppLog.event.like("job.%"))
                    .filter(AppLog.event.like("%.interrupted") | AppLog.event.like("%.timeout"))
                    .order_by(AppLog.ts.desc()).limit(200).all())
        for r in rows:
            interrupted = r.event.endswith(".interrupted")
            rule_id = "D5.job_interrupted" if interrupted else "D6.job_timeout"
            if not self._enabled(overrides, rule_id):
                continue
            job_type = r.event[len("job."):].rsplit(".", 1)[0]
            active[(rule_id, "data", job_type)] = {
                "severity": "error",
                "title": ("孤儿任务对账: " if interrupted else "任务超时回收: ") + job_type,
                "detail": {"event": r.event, "job_type": job_type,
                           "message": r.message, "ts": str(r.ts)},
                "trace_id": r.trace_id,
            }

    # -- D7 dead-man's switch (keeper scheduler last_success) ----------------
    def _d7_deadman(self, active: dict, overrides: dict) -> None:
        if not self._enabled(overrides, "D7.deadman") or not self._keeper_base:
            return
        hours = self._p(overrides, "D7.deadman", "hours", 25)
        try:
            with httpx.Client(timeout=_KEEPER_TIMEOUT) as client:
                r = client.get(self._keeper_base + "/health")
                health = r.json() if r.status_code == 200 else {}
        except Exception:  # noqa: BLE001
            return
        tasks = health.get("scheduler_tasks") or {}
        uptime = health.get("uptime_seconds") or 0
        now = dt.datetime.utcnow()
        cutoff = now - dt.timedelta(hours=hours)
        for task, info in tasks.items():
            last_success = _parse(info.get("last_success_at")) if isinstance(info, dict) else None
            overdue = (last_success is not None and last_success < cutoff) or \
                      (last_success is None and uptime > hours * 3600)
            if overdue:
                ago = "从未" if last_success is None else _humanize_ago(now - last_success)
                active[("D7.deadman", "data", task)] = {
                    "severity": "error",
                    "title": f"死人开关 · {task} 超 {hours}h 无成功 ({ago})",
                    "detail": {"task": task, "hours": hours,
                               "last_success_at": str(last_success) if last_success else None},
                }

    # -- D8 error rate (per layer, in window) --------------------------------
    def _d8_error_rate(self, active: dict, overrides: dict) -> None:
        if not self._enabled(overrides, "D8.error_rate"):
            return
        window = self._p(overrides, "D8.error_rate", "window_min", 15)
        warn = self._p(overrides, "D8.error_rate", "warn", 10)
        crit = self._p(overrides, "D8.error_rate", "crit", 50)
        since = dt.datetime.utcnow() - dt.timedelta(minutes=window)
        with self._sf() as db:
            rows = (db.query(AppLog.source, func.count(AppLog.id))
                    .filter(AppLog.ts >= since, AppLog.level.in_(_ERROR_LEVELS))
                    .group_by(AppLog.source).all())
        for source, c in rows:
            c = int(c)
            if c < warn:
                continue
            sev = "error" if c >= crit else "warn"
            active[("D8.error_rate", source, None)] = {
                "severity": sev,
                "title": f"{source} 层近 {window}m 内 ERROR+ {c} 条",
                "detail": {"layer": source, "window_min": window, "count": c,
                           "warn": warn, "crit": crit},
            }

    # -- D9 pipeline health (store.health()) ---------------------------------
    def _d9_pipeline(self, active: dict, overrides: dict) -> None:
        if not self._enabled(overrides, "D9.pipeline_degraded"):
            return
        store = get_store()
        if store is None:
            return
        h = store.health()
        qpct = self._p(overrides, "D9.pipeline_degraded", "queue_pct", 80)
        problems = []
        sev = "warn"
        if h.get("dropped", 0) > 0:
            problems.append(f"已丢弃 {h['dropped']} 条"); sev = "error"
        if h.get("db_sink") != "ok":
            problems.append("落库 sink 关闭"); sev = "error"
        if h.get("file_sink") != "ok":
            problems.append("落盘 sink 关闭"); sev = "error"
        qmax = h.get("queue_max") or 1
        watermark = round(100 * h.get("queue_size", 0) / qmax)
        if watermark >= qpct:
            problems.append(f"队列水位 {watermark}%")
        if problems:
            active[("D9.pipeline_degraded", "monitor", None)] = {
                "severity": sev,
                "title": "日志管道降级 · " + " / ".join(problems),
                "detail": {**h, "queue_pct": watermark, "threshold": qpct},
            }

    # -- D10 layer silence ---------------------------------------------------
    def _d10_silence(self, active: dict, overrides: dict) -> None:
        if not self._enabled(overrides, "D10.layer_silent"):
            return
        by_layer = self._s.layer_silence_by
        now = dt.datetime.utcnow()
        day_ago = now - dt.timedelta(hours=24)
        with self._sf() as db:
            rows = (db.query(AppLog.source, func.max(AppLog.ts))
                    .filter(AppLog.ts >= day_ago).group_by(AppLog.source).all())
        last_by = {src: ts for src, ts in rows}
        for layer, minutes in by_layer.items():
            last = last_by.get(layer)
            if last is None:                       # no log in 24h → layer not "active"; skip
                continue
            silent_min = (now - last).total_seconds() / 60
            if silent_min >= minutes:
                active[("D10.layer_silent", layer, None)] = {
                    "severity": "warn",
                    "title": f"{layer} 层已静默 {int(silent_min)}m (阈值 {minutes}m)",
                    "detail": {"layer": layer, "silent_min": int(silent_min), "threshold": minutes,
                               "last_ts": str(last)},
                }

    # -- D11 frontend crash per session --------------------------------------
    def _d11_fe_crash(self, active: dict, overrides: dict) -> None:
        if not self._enabled(overrides, "D11.frontend_crash"):
            return
        cf = self._p(overrides, "D11.frontend_crash", "per_session", 2)
        since = dt.datetime.utcnow() - dt.timedelta(hours=1)
        with self._sf() as db:
            rows = (db.query(AppLog.session_id, func.count(AppLog.id))
                    .filter(AppLog.ts >= since, AppLog.source == "frontend",
                            AppLog.event.in_(_FE_CRASH_EVENTS),
                            AppLog.session_id.isnot(None))
                    .group_by(AppLog.session_id).having(func.count(AppLog.id) >= cf).all())
        for session_id, c in rows:
            active[("D11.frontend_crash", "frontend", session_id)] = {
                "severity": "warn",
                "title": f"前端会话 {str(session_id)[:12]} 崩溃 {int(c)} 次",
                "detail": {"session_id": session_id, "count": int(c), "threshold": cf},
            }

    # -- D12 sentinel offline (the watcher must be watched) ------------------
    def _d12_sentinel(self, active: dict, overrides: dict) -> None:
        if not self._enabled(overrides, "D12.sentinel_offline"):
            return
        snap = self._signals.snapshot()
        last = snap["sentinel_last_seen"]
        if last is None:                           # never seen a sentinel → nothing to watch
            return
        sh = self._p(overrides, "D12.sentinel_offline", "heartbeat_sec", 60)
        silent = (dt.datetime.utcnow() - last).total_seconds()
        if silent >= sh:
            active[("D12.sentinel_offline", "monitor", "sentinel")] = {
                "severity": "warn",
                "title": f"哨兵离线 · 心跳 {int(silent)}s 未更新 (阈值 {sh}s)",
                "detail": {"sentinel_last_seen": str(last), "silent_sec": int(silent),
                           "threshold": sh},
            }

    # -- side effects on lifecycle transitions -------------------------------
    def _on_new_alert(self, alert: dict) -> None:
        self._ship_monitor_log(alert, resolved=False)
        try:
            bus.publish("monitor.alert_triggered", alert)
        except Exception:  # noqa: BLE001
            pass
        self._maybe_webhook(alert)

    def _on_resolved_alert(self, alert: dict) -> None:
        self._ship_monitor_log(alert, resolved=True)
        try:
            bus.publish("monitor.alert_resolved", alert)
        except Exception:  # noqa: BLE001
            pass

    def _ship_monitor_log(self, alert: dict, *, resolved: bool) -> None:
        store = get_store()
        if store is None:
            return
        level = "INFO" if resolved else _alert_level(alert["severity"])
        event = "monitor.alert_resolved" if resolved else "monitor.alert_triggered"
        try:
            store.enqueue({
                "level": level, "logger": "monitor.evaluator", "source": "monitor",
                "event": event, "message": alert["title"],
                "trace_id": alert.get("trace_id"),
                "extra": {"rule_id": alert["rule_id"], "layer": alert["layer"],
                          "target": alert.get("target"), "alert_id": alert["id"],
                          "severity": alert["severity"], **(alert.get("detail") or {})},
            })
        except Exception:  # noqa: BLE001
            pass

    def _maybe_webhook(self, alert: dict) -> None:
        url = (self._webhook_override or self._s.log_alert_webhook or "").strip()
        if not url:
            return
        min_rank = _SEV_RANK.get((self._s.monitor_alert_min_level or "error").lower(), 2)
        if _SEV_RANK.get(alert["severity"], 0) < min_rank:
            return
        content = (f"[{alert['severity'].upper()}] {alert['title']}\n"
                   f"rule={alert['rule_id']} layer={alert['layer']} target={alert.get('target')}\n"
                   f"trace_id={alert.get('trace_id')}")
        body = json.dumps({"msgtype": "text", "text": {"content": content}}).encode("utf-8")
        try:
            req = urllib.request.Request(url, data=body,
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=5)  # noqa: S310 - operator-configured URL
        except Exception:  # noqa: BLE001 - alerting is best-effort
            pass

    # -- channels (POST /monitor/channels) -----------------------------------
    def set_webhook(self, url: str | None) -> None:
        self._webhook_override = (url or "").strip() or None

    @property
    def webhook(self) -> str:
        return (self._webhook_override or self._s.log_alert_webhook or "")


def _mem_pct(c: dict) -> int | None:
    usage, limit = c.get("mem_usage"), c.get("mem_limit")
    if isinstance(usage, (int, float)) and isinstance(limit, (int, float)) and limit > 0:
        return round(100 * usage / limit)
    pct = c.get("mem_pct")
    return int(pct) if isinstance(pct, (int, float)) else None


def _alert_level(severity: str) -> str:
    return {"critical": "CRITICAL", "error": "ERROR", "warn": "WARN"}.get(severity, "INFO")


def _parse(value) -> dt.datetime | None:
    from .store import _parse_ts
    return _parse_ts(value)


def _humanize_ago(delta: dt.timedelta) -> str:
    secs = int(delta.total_seconds())
    if secs < 3600:
        return f"{secs // 60}m 前"
    if secs < 86400:
        return f"{secs // 3600}h 前"
    return f"{secs // 86400}d 前"


# ── process singleton ────────────────────────────────────────────────────────
_evaluator: MonitorEvaluator | None = None


def start_evaluator(settings: Settings, session_factory, alert_store: AlertStore,
                    signal_state: SignalState, keeper_base: str = "") -> MonitorEvaluator | None:
    global _evaluator
    if not settings.monitor_enabled:
        return None
    _evaluator = MonitorEvaluator(settings, session_factory, alert_store, signal_state,
                                  keeper_base).start()
    return _evaluator


def get_evaluator() -> MonitorEvaluator | None:
    return _evaluator


def stop_evaluator() -> None:
    if _evaluator is not None:
        _evaluator.stop()
