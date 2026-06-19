"""Alert lifecycle store + sentinel signal buffer (可观测层重构方案 §4.4 / §4.5).

``AlertStore`` owns the ``monitor_alert`` table state machine (firing → acked →
resolved) with de-duplication by ``(rule_id, layer, target)`` and time-based
auto-resolve. ``SignalState`` is the in-memory buffer of the哨兵's latest
container observations (+ a short restart history for windowed detection) — it is
the bridge between ``POST /monitor/signals`` and the evaluator.

Both are process singletons (one writer process), set up at app startup.
"""
from __future__ import annotations

import datetime as dt
import threading
from collections import defaultdict, deque
from collections.abc import Iterable

from sqlalchemy import func

from ..db.models import MonitorAlert


def _utcnow() -> dt.datetime:
    return dt.datetime.utcnow()


# 容器观测保活窗口: 超过这个时长没再被哨兵上报的容器名会被驱逐 (见 SignalState._evict_stale)。
# 取 6h —— 远大于正常上报间隔 (秒级), 只清理真正消失/一次性的容器名。
_CONTAINER_TTL_SEC = 6 * 3600


# ── alert lifecycle ──────────────────────────────────────────────────────────
class AlertStore:
    """CRUD + state machine over ``monitor_alert``. Thread-safe via the DB
    session; the evaluator thread and request threads share one factory."""

    def __init__(self, session_factory) -> None:
        self._sf = session_factory

    def _active_key(self, db, rule_id: str, layer: str, target: str | None) -> MonitorAlert | None:
        q = (db.query(MonitorAlert)
             .filter(MonitorAlert.rule_id == rule_id, MonitorAlert.layer == layer,
                     MonitorAlert.status.in_(("firing", "acked")))
             .order_by(MonitorAlert.id.desc()))
        q = q.filter(MonitorAlert.target.is_(None)) if target is None else q.filter(MonitorAlert.target == target)
        return q.first()

    def trigger(self, *, rule_id: str, severity: str, layer: str, target: str | None,
                title: str, detail: dict | None = None, trace_id: str | None = None) -> tuple[dict, bool]:
        """Upsert an alert by de-dupe key. Returns (alert_dict, is_new)."""
        now = _utcnow()
        with self._sf() as db:
            existing = self._active_key(db, rule_id, layer, target)
            if existing is not None:
                existing.count += 1
                existing.last_seen = now
                existing.severity = severity            # may escalate (D8)
                if detail is not None:
                    existing.detail = detail
                if trace_id and not existing.trace_id:
                    existing.trace_id = trace_id
                db.commit()
                return _alert_to_dict(existing), False
            row = MonitorAlert(
                rule_id=rule_id, severity=severity, layer=layer, target=target,
                title=title, detail=detail, status="firing", count=1,
                first_seen=now, last_seen=now, trace_id=trace_id,
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return _alert_to_dict(row), True

    def auto_resolve(self, active_keys: Iterable[tuple[str, str, str | None]],
                     clear_after_sec: int) -> list[dict]:
        """Resolve any firing/acked alert whose condition is no longer active and
        whose ``last_seen`` is older than ``clear_after_sec`` (可观测层方案 §4.4)."""
        active = set(active_keys)
        cutoff = _utcnow() - dt.timedelta(seconds=max(1, clear_after_sec))
        resolved: list[dict] = []
        with self._sf() as db:
            rows = db.query(MonitorAlert).filter(MonitorAlert.status.in_(("firing", "acked"))).all()
            for r in rows:
                if (r.rule_id, r.layer, r.target) in active:
                    continue
                if r.last_seen and r.last_seen <= cutoff:
                    r.status = "resolved"
                    r.resolved_at = _utcnow()
                    resolved.append(_alert_to_dict(r))
            if resolved:
                db.commit()
        return resolved

    def ack(self, alert_id: int, by: str) -> dict | None:
        with self._sf() as db:
            r = db.get(MonitorAlert, alert_id)
            if r is None:
                return None
            if r.status == "firing":
                r.status = "acked"
                r.acked_by = by
                r.acked_at = _utcnow()
                db.commit()
            return _alert_to_dict(r)

    def resolve(self, alert_id: int, by: str | None = None) -> dict | None:
        with self._sf() as db:
            r = db.get(MonitorAlert, alert_id)
            if r is None:
                return None
            if r.status != "resolved":
                r.status = "resolved"
                r.resolved_at = _utcnow()
                if by and not r.acked_by:
                    r.acked_by = by
                db.commit()
            return _alert_to_dict(r)

    def list(self, *, status: str | None = None, severity: str | None = None,
             layer: str | None = None, target: str | None = None, search: str | None = None,
             allowed_layers: set[str] | None = None, page: int = 1, page_size: int = 50) -> dict:
        with self._sf() as db:
            q = db.query(MonitorAlert)
            # RBAC 收窄 (可观测层方案 §8): 非全开角色只能看其有权层的告警 (viewer → frontend)。
            if allowed_layers is not None:
                q = q.filter(MonitorAlert.layer.in_(sorted(allowed_layers) or ["__none__"]))
            if status:
                q = q.filter(MonitorAlert.status.in_([s.strip() for s in status.split(",") if s.strip()]))
            if severity:
                q = q.filter(MonitorAlert.severity.in_([s.strip() for s in severity.split(",") if s.strip()]))
            if layer:
                q = q.filter(MonitorAlert.layer == layer)
            if target:
                q = q.filter(MonitorAlert.target.like(f"%{target}%"))
            if search:
                like = f"%{search}%"
                q = q.filter(MonitorAlert.title.like(like) | MonitorAlert.rule_id.like(like))
            total = q.count()
            page = max(1, page)
            page_size = max(1, min(page_size, 200))
            # firing first, then by recency / severity
            rows = (q.order_by(_status_rank(), MonitorAlert.last_seen.desc())
                    .offset((page - 1) * page_size).limit(page_size).all())
            return {"items": [_alert_to_dict(r) for r in rows], "total": total,
                    "page": page, "page_size": page_size}

    def active(self) -> list[dict]:
        with self._sf() as db:
            rows = (db.query(MonitorAlert)
                    .filter(MonitorAlert.status.in_(("firing", "acked")))
                    .order_by(MonitorAlert.last_seen.desc()).all())
            return [_alert_to_dict(r) for r in rows]

    def summary(self) -> dict:
        """firing count + by-severity + a small top list (for /monitor/health)."""
        with self._sf() as db:
            firing = (db.query(func.count(MonitorAlert.id))
                      .filter(MonitorAlert.status == "firing").scalar() or 0)
            by_sev: dict[str, int] = {}
            for sev, c in (db.query(MonitorAlert.severity, func.count(MonitorAlert.id))
                           .filter(MonitorAlert.status == "firing")
                           .group_by(MonitorAlert.severity).all()):
                by_sev[sev] = int(c)
            top = (db.query(MonitorAlert)
                   .filter(MonitorAlert.status == "firing")
                   .order_by(_sev_rank(), MonitorAlert.last_seen.desc()).limit(5).all())
            return {"firing": int(firing), "by_severity": by_sev,
                    "top": [{"id": r.id, "rule_id": r.rule_id, "layer": r.layer,
                             "severity": r.severity, "title": r.title, "target": r.target,
                             "trace_id": r.trace_id} for r in top]}

    def active_by_layer(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = defaultdict(list)
        for a in self.active():
            out[a["layer"]].append(a)
        return out


_SEV_RANK = {"critical": 0, "error": 1, "warn": 2, "info": 3}
_STATUS_RANK = {"firing": 0, "acked": 1, "resolved": 2}


def _sev_rank():
    from sqlalchemy import case
    return case(_SEV_RANK, value=MonitorAlert.severity, else_=9)


def _status_rank():
    from sqlalchemy import case
    return case(_STATUS_RANK, value=MonitorAlert.status, else_=9)


def _alert_to_dict(r: MonitorAlert) -> dict:
    return {
        "id": r.id, "rule_id": r.rule_id, "severity": r.severity, "layer": r.layer,
        "target": r.target, "title": r.title, "detail": r.detail, "status": r.status,
        "count": r.count, "first_seen": r.first_seen, "last_seen": r.last_seen,
        "resolved_at": r.resolved_at, "acked_by": r.acked_by, "acked_at": r.acked_at,
        "trace_id": r.trace_id,
    }


# ── sentinel signal buffer ───────────────────────────────────────────────────
class SignalState:
    """Latest container observations from the obs-sentinel + a short restart
    history per container (so the evaluator can window D2). In-memory, thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.observed_at: dt.datetime | None = None
        self.sentinel_last_seen: dt.datetime | None = None
        self.containers: dict[str, dict] = {}
        # v2 (可观测层重构方案 §7.4): the sentinel now also pushes host infra (disk)
        # and pre-judged alert conditions (L0/D1-D4/D7/D13-D15) it detected itself.
        self.infra: dict = {}
        self._conditions: list[dict] = []
        self._conditions_at: dt.datetime | None = None
        # name -> deque[(observed_at, restart_count)] trimmed to the storm window
        self._restart_hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=200))

    def ingest(self, payload: dict) -> int:
        now = _utcnow()
        observed = _parse_ts(payload.get("observed_at")) or now
        containers = payload.get("containers") or []
        with self._lock:
            self.observed_at = observed
            self.sentinel_last_seen = now
            for c in containers:
                name = c.get("name")
                if not name:
                    continue
                c = dict(c)
                c["_seen_at"] = now
                self.containers[name] = c
                rc = c.get("restart_count")
                if isinstance(rc, int):
                    self._restart_hist[name].append((observed, rc))
            self._evict_stale(now)
            # v2 extras (only-add: a v1 payload simply omits them, staying compatible)
            if isinstance(payload.get("infra"), dict):
                self.infra = payload["infra"]
            if "conditions" in payload:
                self._conditions = list(payload.get("conditions") or [])
                self._conditions_at = now
        return len(containers)

    def _evict_stale(self, now: dt.datetime) -> None:
        """容器名不是稳定身份 (重启/重部署/扩缩容/一次性容器都会产生新名字)。若只增不删,
        每个见过的名字都会永久占着一条 dict + 一个 200 槽 deque —— 长跑进程内存单调
        增长 (正是 CI 注释里 2026-06-01 崩溃循环那类隐患), 且早已消失的容器还会一直出现
        在 /monitor/topology。这里按 TTL 驱逐 ``_seen_at`` 过期的容器及其重启历史。
        TTL 取得足够宽 (远大于哨兵上报间隔), 不会误删正在监控的容器。"""
        cutoff = now - dt.timedelta(seconds=_CONTAINER_TTL_SEC)
        stale = [n for n, c in self.containers.items()
                 if (c.get("_seen_at") or now) < cutoff]
        for n in stale:
            self.containers.pop(n, None)
            self._restart_hist.pop(n, None)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "observed_at": self.observed_at,
                "sentinel_last_seen": self.sentinel_last_seen,
                "containers": {k: dict(v) for k, v in self.containers.items()},
                "infra": dict(self.infra),
            }

    def conditions(self, fresh_within_sec: int) -> list[dict]:
        """Sentinel-judged conditions, but only while still fresh — if the watcher
        went silent we must *not* keep asserting its last verdicts (D12 covers that
        blind spot). Stale ⇒ [] ⇒ the evaluator's auto_resolve closes them."""
        with self._lock:
            if self._conditions_at is None:
                return []
            if (_utcnow() - self._conditions_at).total_seconds() > max(1, fresh_within_sec):
                return []
            return [dict(c) for c in self._conditions]

    def restart_delta(self, name: str, window_min: int) -> int:
        """ΔRestartCount over the last ``window_min`` minutes for one container."""
        cutoff = _utcnow() - dt.timedelta(minutes=max(1, window_min))
        with self._lock:
            hist = [(t, rc) for (t, rc) in self._restart_hist.get(name, ()) if t and t >= cutoff]
        if len(hist) < 2:
            return 0
        return max(0, hist[-1][1] - hist[0][1])


def _parse_ts(value) -> dt.datetime | None:
    if isinstance(value, dt.datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str):
        try:
            s = value.rstrip("Z").replace("T", " ")
            # drop a trailing tz offset if present
            for sep in ("+", ):
                if sep in s[11:]:
                    s = s[:11] + s[11:].split(sep)[0]
            return dt.datetime.fromisoformat(s.strip())
        except ValueError:
            return None
    return None


# ── process singletons ───────────────────────────────────────────────────────
_alert_store: AlertStore | None = None
_signal_state: SignalState | None = None


def init_alert_store(session_factory) -> AlertStore:
    global _alert_store
    _alert_store = AlertStore(session_factory)
    return _alert_store


def init_signal_state() -> SignalState:
    global _signal_state
    _signal_state = SignalState()
    return _signal_state


def get_alert_store() -> AlertStore | None:
    return _alert_store


def get_signal_state() -> SignalState | None:
    return _signal_state
