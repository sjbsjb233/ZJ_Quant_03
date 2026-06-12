"""Monitor evaluator — the background守护线程 that turns signals + logs into a
managed alert lifecycle (可观测层重构方案 §3.2 / §6.2).

评估器瘦身 (可观测层重构方案 §6.1)：基建/存活类条件 (L0/D1–D4/D7/D13–D15) 改由外置
哨兵检测并上报,评估器**不再自算**;它只保留日志派生检测,并把哨兵判定喂进同一台账。

每 ``MONITOR_EVAL_TICK_SEC`` 一跳:
  1. 消费哨兵上报的 conditions (L0/D1–D4/D7/D13–D15) → 进入同一告警生命周期 (唯一台账);
  2. 从 app_log 捞 job.*.interrupted / job.*.timeout → D5/D6 进入告警台账;
  3. D8 错误率、D9 管道健康、D10 某层静默、D11 前端崩溃、D12 哨兵离线 (日志派生);
  4. 告警生命周期: 去重 trigger / 自动 resolve / 事件总线 publish / webhook 推送 /
     以 source=monitor 落一条日志 (供实时流 + trace)。

哨兵判定与生命周期分离：检测可在哨兵 (绕开 backend),但**台账始终只有一个** (在 backend)。
哨兵停报某条件 ⇒ 现有 ``auto_resolve`` 自动关闭它 (键控于 active 集)。

线程模型与 ``AppLogStore`` 一致 (daemon、整跳与每个检测器都 try/except 包裹，绝不让
评估器崩溃影响请求线程)。第一跳延后一个 tick，所以短测试永不真正评估。
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import urllib.request

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


def layer_of_container(name: str) -> str:
    """Map a real container name → its observability layer (可观测层方案 §7.3③)."""
    n = (name or "").lower()
    # 旁路/运维容器 (哨兵 + watchtower 自动更新) 归入 monitor 层，避免被误判成 backend 幽灵节点。
    if "sentinel" in n or "monitor" in n or "watchtower" in n:
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
        # keeper_base kept for signature stability; D7 detection moved to the sentinel
        # (评估器瘦身 §6.1), so the evaluator no longer probes keeper directly.
        self._s = settings
        self._sf = session_factory
        self._alerts = alert_store
        self._signals = signal_state
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.eval_last_run: dt.datetime | None = None
        self._webhook_override: str | None = None        # /monitor/channels POST

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
        for detector in (self._consume_sentinel_conditions, self._d5_d6_jobs,
                         self._d8_error_rate, self._d9_pipeline, self._d10_silence,
                         self._d11_fe_crash, self._d12_sentinel, self._d16_admission):
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

    # -- consume sentinel-judged conditions (L0/D1–D4/D7/D13–D15) ------------
    def _consume_sentinel_conditions(self, active: dict, overrides: dict) -> None:
        """The infra/liveness plane is detected by the 外置哨兵 (§6.1/§6.5). We do
        **not** re-compute it here — we ingest the conditions it already judged and
        feed them through the *same* alert lifecycle (dedup / ack / resolve /
        webhook), so there stays exactly one ledger. When the sentinel goes blind,
        ``SignalState.conditions`` returns [] (freshness gate) so these auto-resolve
        — and D12 fires to flag the blind spot itself."""
        for c in self._signals.conditions(self._s.signal_fresh_sec):
            rule_id = c.get("code")
            # honour the per-rule enable toggle (admin can mute a sentinel rule);
            # unknown codes are ignored so a forward-compatible sentinel can't inject
            # arbitrary rule_ids into the ledger.
            if not rule_id or rule_id not in rulemod.RULES_BY_ID:
                continue
            if not self._enabled(overrides, rule_id):
                continue
            layer = c.get("layer") or "monitor"
            target = c.get("target")
            active[(rule_id, layer, target)] = {
                "severity": c.get("severity", "warn"),
                "title": c.get("title", rule_id),
                "detail": c.get("detail"),
                "trace_id": c.get("trace_id"),
            }

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

    # -- D16 admission gate stuck (内存治理方案 WP5) --------------------------
    def _d16_admission(self, active: dict, overrides: dict) -> None:
        """准入闸滞留:闸自报的 stuck/timeout 标记升为告警。

        quant_admission 在 deferred 滞留 > Td / 排干 > Tr 后,每 ~60s 重报
        ``admission.deferred.stuck`` / ``admission.draining.timeout``(ERROR,
        经统一日志链落 app_log)。这里把近窗仍在出现的标记钉成 active 条件 ——
        闸放行后标记停发,现有 auto_resolve 生命周期自动关闭它(方案 WP5 D16)。
        """
        if not self._enabled(overrides, "D16.admission_stuck"):
            return
        since = dt.datetime.utcnow() - dt.timedelta(minutes=5)  # 标记每 ~60s 重报
        with self._sf() as db:
            rows = (db.query(AppLog)
                    .filter(AppLog.ts >= since,
                            AppLog.event.in_(("admission.deferred.stuck",
                                              "admission.draining.timeout")))
                    .order_by(AppLog.ts.desc()).limit(50).all())
        td = self._p(overrides, "D16.admission_stuck", "deferred_min", 10)
        tr = self._p(overrides, "D16.admission_stuck", "draining_min", 30)
        for r in rows:
            extra = r.extra or {}
            draining = r.event == "admission.draining.timeout"
            target = "draining" if draining else (extra.get("task_class") or "unknown")
            key = ("D16.admission_stuck", r.source or "data", target)
            if key in active:
                continue  # newest marker per target wins (rows are ts-desc)
            waited = extra.get("draining_s") if draining else extra.get("waited_s")
            active[key] = {
                "severity": "error" if draining else "warn",
                "title": (
                    f"准入闸排干超时 {waited}s (阈值 {tr}m)" if draining
                    else f"任务类 {target} deferred 滞留 {waited}s (阈值 {td}m)"
                ),
                "detail": {"event": r.event, **extra,
                           "deferred_min": td, "draining_min": tr},
                "trace_id": r.trace_id,
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


def _alert_level(severity: str) -> str:
    return {"critical": "CRITICAL", "error": "ERROR", "warn": "WARN"}.get(severity, "INFO")


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
