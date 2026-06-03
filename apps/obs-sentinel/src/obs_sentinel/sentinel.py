"""The sentinel scan loop (可观测层重构方案 §5).

每个间隔:
  1. 巡检容器 (docker inspect+stats) + L0 存活探测 + D13 磁盘 statvfs + D7 死人开关;
  2. 本地判定 L0 / D1 / D2 / D3 / D4 / D7 / D13 / D14 / D15 → 组装 ``conditions``;
  3. 组装并 **publish** 一帧状态 (驱动 /obs SSE — 绕开 backend 直推前端);
  4. 永远先 POST ``/monitor/signals`` (v2: containers + infra + conditions);
  5. 条件转移 (新触发 / 解除) → **先写本地 JSONL** 再 ship ``source=monitor`` 日志。

这是「第一层 · 心跳/基建」: 谁崩它都活,且不依赖 web-backend 的任何接口即可独立裁决。
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
import urllib.request
from collections import defaultdict, deque

from .config import Config
from .docker_client import (
    DockerClient,
    DockerError,
    cpu_totals,
    memory_used_and_limit,
)
from .jsonl import JsonlWriter
from .layers import layer_of_container, layer_rank
from .probes import disk_usage, probe_liveness
from .state import SentinelState

_GIB = 1024 ** 3
# layer health buckets used in the status frame (align with backend /monitor/health)
_OK, _WARN, _ERR = "ok", "warn", "error"
_SEV_LEVEL = {"critical": "CRITICAL", "error": "ERROR", "warn": "WARN", "info": "INFO"}


def _now_iso() -> str:
    return dt.datetime.utcnow().isoformat() + "Z"


def _log(msg: str) -> None:
    sys.stdout.write(f"[obs-sentinel] {_now_iso()} {msg}\n")
    sys.stdout.flush()


def _parse_started(value) -> float | None:
    if not value or value.startswith("0001"):
        return None
    try:
        s = value.replace("Z", "").split(".")[0]
        return dt.datetime.fromisoformat(s).timestamp()
    except ValueError:
        return None


def _parse_iso(value) -> float | None:
    if not value or not isinstance(value, str):
        return None
    try:
        s = value.replace("Z", "").split(".")[0].replace("T", " ")
        if "+" in s[11:]:
            s = s[:11] + s[11:].split("+")[0]
        return dt.datetime.fromisoformat(s.strip()).timestamp()
    except ValueError:
        return None


class Sentinel:
    def __init__(self, cfg: Config, docker: DockerClient | None = None,
                 state: SentinelState | None = None, jsonl: JsonlWriter | None = None):
        self.cfg = cfg
        self.docker = docker or DockerClient(cfg.docker_socket)
        self.state = state or SentinelState()
        self.jsonl = jsonl or JsonlWriter(cfg.jsonl_dir)
        self._restart_hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=240))
        # duration trackers: (code, target) -> first time the raw predicate held
        self._since: dict[tuple, float] = {}
        # condition keys currently shipped as monitor logs (for transition logging)
        self._shipped: set[tuple] = set()
        # carry expensive deep-scan metrics (cpu/mem/rw) into cheap shallow passes
        self._deep_cache: dict[str, dict] = {}
        # previous cumulative cpu counters per container → instantaneous CPU% over
        # the deep-scan interval (one-shot stats zero precpu, so we diff ourselves)
        self._cpu_prev: dict[str, tuple[int, int]] = {}

    # -- one scan ------------------------------------------------------------
    def scan_once(self, deep: bool = True) -> dict:
        """Run one observation cycle. ``deep`` does the expensive docker ``stats`` +
        ``inspect?size=true`` (cpu/mem/D14/D15); a shallow pass skips those and
        carries the last deep metrics, so the fast L0 liveness loop stays cheap."""
        obs = self._observe(deep)
        reach = self._probe_all(obs)
        for o in obs:
            o["reachable"] = reach.get(o["name"], o["status"] == "running")
        disks = self._disks()
        conditions = self._detect(obs, disks)
        frame = self._build_frame(obs, disks, conditions)
        self.state.publish(frame)
        self._post_signals(obs, disks, conditions)
        self._ship_transitions(conditions)
        return frame

    # -- observe containers --------------------------------------------------
    def _observe(self, deep: bool = True) -> list[dict]:
        out: list[dict] = []
        try:
            containers = self.docker.list_containers(self.cfg.label_filter)
        except (DockerError, OSError) as exc:
            _log(f"docker list failed: {exc}")
            return out
        now = time.time()
        for c in containers:
            cid = c.get("Id")
            name = (c.get("Names") or ["/?"])[0].lstrip("/")
            try:
                insp = self.docker.inspect(cid, size=deep)
            except (DockerError, OSError):
                insp = {}
            st = insp.get("State", {}) or {}
            status = st.get("Status") or c.get("State") or "unknown"
            cached = self._deep_cache.get(name, {})
            mem_usage = mem_limit = cpu_pct = None
            rw = insp.get("SizeRw") if deep else cached.get("rw_size_bytes")
            if deep and status == "running":
                try:
                    snap = self.docker.stats(cid)
                    mem_usage, mem_limit = memory_used_and_limit(snap)
                    cpu_pct = self._cpu_delta(name, snap)
                except (DockerError, OSError):
                    pass
            elif not deep and status == "running":
                # carry the last deep metrics so the frame keeps showing cpu/mem
                mem_usage, mem_limit = cached.get("mem_usage"), cached.get("mem_limit")
                cpu_pct = cached.get("cpu_pct")
            started = _parse_started(st.get("StartedAt"))
            o = {
                "name": name,
                "layer": layer_of_container(name),
                "status": status,
                "oom_killed": bool(st.get("OOMKilled")),
                "restart_count": int(insp.get("RestartCount", 0) or 0),
                "health": ((st.get("Health") or {}).get("Status") or "none"),
                "mem_usage": mem_usage,
                "mem_limit": mem_limit,
                "mem_pct": _pct(mem_usage, mem_limit),
                "cpu_pct": cpu_pct,
                "rw_size_bytes": int(rw) if isinstance(rw, (int, float)) else None,
                "uptime_s": int(now - started) if started else None,
            }
            out.append(o)
            self._restart_hist[name].append((now, o["restart_count"]))
            if deep and status == "running":
                self._deep_cache[name] = {"mem_usage": mem_usage, "mem_limit": mem_limit,
                                          "cpu_pct": cpu_pct, "rw_size_bytes": o["rw_size_bytes"]}
        return out

    def _cpu_delta(self, name: str, snap: dict) -> float | None:
        """CPU% over the deep-scan interval, diffing cumulative counters against the
        sentinel's own previous sample (one-shot stats zero precpu, §D14 fix)."""
        cur = cpu_totals(snap)
        if cur is None:
            return None
        total, system, n = cur
        prev = self._cpu_prev.get(name)
        self._cpu_prev[name] = (total, system)
        if prev is None:
            return None                          # first sample → establish baseline
        d_cpu, d_sys = total - prev[0], system - prev[1]
        if d_sys <= 0 or d_cpu < 0:
            return None
        return round((d_cpu / d_sys) * n * 100.0, 1)

    # -- L0 liveness ----------------------------------------------------------
    def _probe_all(self, obs: list[dict]) -> dict[str, bool]:
        """Probe every configured liveness target → {container_name: reachable}.
        This is the single truth source for 可达/不可达 (§5.1, never reads a
        business endpoint)."""
        reach: dict[str, bool] = {}
        for t in self.cfg.liveness_targets:
            try:
                reach[t.name] = probe_liveness(t.url, self.cfg.liveness_probe_timeout_sec)
            except Exception:  # noqa: BLE001 - one bad probe can't stall the scan
                reach[t.name] = False
        return reach

    # -- D13 disk -------------------------------------------------------------
    def _disks(self) -> list[dict]:
        out: list[dict] = []
        for mount, path in self.cfg.disk_mounts:
            try:
                usage = disk_usage(path)
            except Exception:  # noqa: BLE001
                usage = None
            if usage is None:
                continue
            pct = usage["used_pct"]
            state = _ERR if pct >= self.cfg.disk_crit_pct else _WARN if pct >= self.cfg.disk_warn_pct else _OK
            out.append({"mount": mount, "used_pct": pct, "state": state,
                        "used_bytes": usage["used_bytes"], "total_bytes": usage["total_bytes"]})
        return out

    # -- detection: build the alert conditions (§7.4) ------------------------
    def _detect(self, obs: list[dict], disks: list[dict]) -> list[dict]:
        conds: list[dict] = []
        now = time.time()
        targets_by_name = {o["name"]: o for o in obs}
        liveness_names = {t.name for t in self.cfg.liveness_targets}

        for o in obs:
            name, layer = o["name"], o["layer"]
            # L0 — unreachable (truth source). Skip during start-up grace.
            if name in liveness_names and not o["reachable"]:
                young = (o["status"] == "running" and o["uptime_s"] is not None
                         and o["uptime_s"] < self.cfg.liveness_grace_sec)
                if not young:
                    conds.append(self._cond("L0.unreachable", "error", layer, name,
                                            f"{name} 探测不可达",
                                            {"container": name, "status": o["status"]}))
            # D1 — OOMKilled
            if o["oom_killed"]:
                conds.append(self._cond("D1.oom_killed", "critical", layer, name,
                                        f"{name} 被内核 OOM 强杀",
                                        {"container": name, "restart_count": o["restart_count"]}))
            # D2 — restart storm
            delta = self._restart_delta(name)
            if delta >= self.cfg.restart_storm_count:
                conds.append(self._cond("D2.restart_storm", "critical", layer, name,
                                        f"{name} 在 {self.cfg.restart_storm_window_min}m 内重启 {delta} 次",
                                        {"container": name, "restart_delta": delta,
                                         "window_min": self.cfg.restart_storm_window_min,
                                         "oom_killed": o["oom_killed"]}))
            # D3 — unhealthy (duration)
            if self._held(("D3.unhealthy", name), o["health"] == "unhealthy", now,
                          self.cfg.unhealthy_for_sec):
                conds.append(self._cond("D3.unhealthy", "error", layer, name,
                                        f"{name} 持续不健康 ≥ {self.cfg.unhealthy_for_sec}s",
                                        {"container": name, "health": "unhealthy",
                                         "for_sec": self.cfg.unhealthy_for_sec}))
            # D4 — memory pressure (duration)
            pct = o["mem_pct"]
            if pct is not None and self._held(("D4.mem_pressure", name),
                                              pct >= self.cfg.mem_pressure_pct, now,
                                              self.cfg.mem_pressure_for_sec):
                conds.append(self._cond("D4.mem_pressure", "warn", layer, name,
                                        f"{name} 内存压力 {pct}% 持续 ≥ {self.cfg.mem_pressure_for_sec}s",
                                        {"container": name, "mem_pct": pct,
                                         "pct": self.cfg.mem_pressure_pct,
                                         "for_sec": self.cfg.mem_pressure_for_sec}))
            else:
                if pct is None or pct < self.cfg.mem_pressure_pct:
                    self._since.pop(("D4.mem_pressure", name), None)
            # D14 — CPU saturation (duration)
            cpu = o["cpu_pct"]
            if cpu is not None and self._held(("D14.cpu_saturation", name),
                                              cpu >= self.cfg.cpu_sat_pct, now,
                                              self.cfg.cpu_sat_for_sec):
                conds.append(self._cond("D14.cpu_saturation", "warn", layer, name,
                                        f"{name} CPU {cpu:.0f}% 持续 ≥ {self.cfg.cpu_sat_for_sec}s",
                                        {"container": name, "cpu_pct": cpu,
                                         "pct": self.cfg.cpu_sat_pct,
                                         "for_sec": self.cfg.cpu_sat_for_sec}))
            else:
                if cpu is None or cpu < self.cfg.cpu_sat_pct:
                    self._since.pop(("D14.cpu_saturation", name), None)
            # D15 — core-dump (可写层突增)
            rw = o["rw_size_bytes"]
            if rw is not None and rw >= self.cfg.core_dump_rw_gb * _GIB:
                conds.append(self._cond("D15.core_dump", "critical", layer, name,
                                        f"{name} 可写层暴涨 {rw / _GIB:.1f}G (疑似崩溃转储)",
                                        {"container": name, "rw_size_gb": round(rw / _GIB, 1),
                                         "threshold_gb": self.cfg.core_dump_rw_gb}))

        # D13 — disk water level (host-level → 归 backend 层, §7A.4)
        for d in disks:
            if d["state"] == _OK:
                continue
            sev = "critical" if d["state"] == _ERR else "warn"
            crit = self.cfg.disk_crit_pct if sev == "critical" else self.cfg.disk_warn_pct
            conds.append(self._cond("D13.disk", sev, "backend", d["mount"],
                                    f"{d['mount']} 磁盘 {d['used_pct']}%",
                                    {"mount": d["mount"], "used_pct": d["used_pct"], "crit": crit}))

        # D7 — dead-man's switch (哨兵直采 keeper, 不再经 backend)
        conds.extend(self._deadman())
        return conds

    def _deadman(self) -> list[dict]:
        if not self.cfg.keeper_health_url:
            return []
        try:
            req = urllib.request.Request(self.cfg.keeper_health_url, method="GET")
            with urllib.request.urlopen(req, timeout=self.cfg.liveness_probe_timeout_sec) as resp:  # noqa: S310
                if resp.status != 200:
                    return []
                health = json.loads(resp.read() or b"{}")
        except Exception:  # noqa: BLE001 - keeper down is D7's own concern, not a crash
            return []
        tasks = health.get("scheduler_tasks") or {}
        uptime = health.get("uptime_seconds") or 0
        hours = self.cfg.deadman_hours
        cutoff = time.time() - hours * 3600
        out: list[dict] = []
        for task, info in tasks.items():
            last = _parse_iso((info or {}).get("last_success_at")) if isinstance(info, dict) else None
            overdue = (last is not None and last < cutoff) or (last is None and uptime > hours * 3600)
            if overdue:
                out.append(self._cond("D7.deadman", "error", "data", task,
                                      f"死人开关 · {task} 超 {hours}h 无成功",
                                      {"task": task, "hours": hours,
                                       "last_success_at": (info or {}).get("last_success_at")}))
        return out

    @staticmethod
    def _cond(code: str, severity: str, layer: str, target: str, title: str, detail: dict) -> dict:
        return {"code": code, "severity": severity, "layer": layer,
                "target": target, "title": title, "detail": detail}

    def _held(self, key: tuple, predicate: bool, now: float, for_sec: int) -> bool:
        """Duration latch: true once ``predicate`` has held continuously ≥ for_sec."""
        if not predicate:
            self._since.pop(key, None)
            return False
        first = self._since.setdefault(key, now)
        return (now - first) >= for_sec

    def _restart_delta(self, name: str) -> int:
        cutoff = time.time() - self.cfg.restart_storm_window_min * 60
        hist = [(t, rc) for (t, rc) in self._restart_hist.get(name, ()) if t >= cutoff]
        if len(hist) < 2:
            return 0
        return max(0, hist[-1][1] - hist[0][1])

    # -- status frame (§7.1) -------------------------------------------------
    def _build_frame(self, obs: list[dict], disks: list[dict], conditions: list[dict]) -> dict:
        by_layer: dict[str, dict] = {}
        for o in sorted(obs, key=lambda x: layer_rank(x["layer"])):
            by_layer.setdefault(o["layer"], o)         # first container per layer
        # conditions grouped by layer + by container target
        worst_layer: dict[str, str] = {}
        for c in conditions:
            tone = c["severity"]
            st = _ERR if tone in ("critical", "error") else _WARN
            cur = worst_layer.get(c["layer"], _OK)
            worst_layer[c["layer"]] = _worst_tone(cur, st)

        layers: dict[str, dict] = {}
        for layer in ("frontend", "backend", "data", "factor"):
            c = by_layer.get(layer)
            reachable = bool(c and c.get("reachable"))
            if c is None:
                # configured liveness target without a docker row → use the probe
                target = next((t for t in self.cfg.liveness_targets if t.layer == layer), None)
                reachable = False if target else True
            # 不可达即 down — 无论是 c 在但探测失败, 还是容器被杀后从拓扑消失 (无 c 但有探测
            # 目标)。否则会出现「reachable=false 但 state=ok」的伪绿不一致 (AC-01 关键)。
            state = _ERR if not reachable else worst_layer.get(layer, _OK)
            entry: dict = {"state": state, "reachable": reachable}
            if c is not None:
                if c.get("cpu_pct") is not None:
                    entry["cpu_pct"] = c["cpu_pct"]
                if c.get("mem_pct") is not None:
                    entry["mem_pct"] = c["mem_pct"]
                if layer == "data":
                    entry["restart_count"] = c.get("restart_count")
                    entry["oom_killed"] = bool(c.get("oom_killed"))
            layers[layer] = entry
        layers["monitor"] = {"state": worst_layer.get("monitor", _OK), "self": _OK}

        core_dumps = [{"container": c["target"], "rw_size_gb": c["detail"].get("rw_size_gb"),
                       "state": _ERR} for c in conditions if c["code"] == "D15.core_dump"]
        infra_alerts = [{"code": c["code"], "severity": c["severity"], "target": c["target"],
                         "title": c["title"]}
                        for c in conditions
                        if c["code"] in ("L0.unreachable", "D13.disk", "D14.cpu_saturation", "D15.core_dump")]

        overall = _OK
        for layer in ("frontend", "backend", "data", "factor", "monitor"):
            overall = _worst_tone(overall, layers[layer]["state"])
        return {
            "v": 1, "source": "sentinel", "as_of": _now_iso(),
            "status": {_OK: "ok", _WARN: "degraded", _ERR: "down"}[overall],
            "layers": layers,
            "infra": {
                "disk": [{"mount": d["mount"], "used_pct": d["used_pct"], "state": d["state"]}
                         for d in disks],
                "core_dumps": core_dumps,
            },
            "infra_alerts": infra_alerts,
        }

    # -- always report raw observations + conditions to the evaluator (v2) ---
    def _post_signals(self, obs: list[dict], disks: list[dict], conditions: list[dict]) -> None:
        if not self.cfg.signals_url:
            return
        payload = {
            "v": 2, "observed_at": _now_iso(), "containers": obs,
            "infra": {"disk": [{"mount": d["mount"], "used_pct": d["used_pct"]} for d in disks]},
            "conditions": conditions,
        }
        self._post(self.cfg.signals_url, payload, headers={"X-Monitor-Token": self.cfg.token})

    # -- transition logging: jsonl-first then ship source=monitor logs -------
    def _ship_transitions(self, conditions: list[dict]) -> None:
        by_key = {(c["code"], c["target"]): c for c in conditions}
        active = set(by_key)
        for key in active - self._shipped:
            self._emit_monitor_log(by_key[key], resolved=False)
        for key in self._shipped - active:
            code, target = key
            self._emit_monitor_log({"code": code, "target": target, "severity": "info",
                                    "layer": "monitor", "title": f"{code} · {target} 已恢复",
                                    "detail": {"target": target}}, resolved=True)
        self._shipped = active

    def _emit_monitor_log(self, cond: dict, *, resolved: bool) -> None:
        level = "INFO" if resolved else _SEV_LEVEL.get(cond["severity"], "WARN")
        event = ("monitor.resolved." if resolved else "monitor.") + cond["code"]
        record = {"source": "monitor", "level": level, "logger": "monitor.sentinel",
                  "event": event, "message": cond["title"], "host": self.cfg.host,
                  "extra": {"code": cond["code"], "layer": cond.get("layer"),
                            "target": cond.get("target"), "severity": cond["severity"],
                            **(cond.get("detail") or {})}}
        # 本地优先: 先落 JSONL (backend 宕机也不丢), 再 ship。
        self.jsonl.write(record)
        if self.cfg.log_ingest_url:
            self._post(self.cfg.log_ingest_url, {"records": [record]},
                       headers={"X-Log-Token": self.cfg.token})
        _log(f"{level} {event} · {cond['title']}")

    def _post(self, url: str, payload: dict, headers: dict) -> None:
        try:
            body = json.dumps(payload, default=str).encode("utf-8")
            req = urllib.request.Request(url, data=body, method="POST",
                                         headers={"Content-Type": "application/json", **headers})
            urllib.request.urlopen(req, timeout=5)  # noqa: S310 - operator-configured URL
        except Exception as exc:  # noqa: BLE001 - 哨兵不可达不能影响业务, POST 失败只丢上报
            _log(f"POST {url} failed: {exc}")


def _pct(usage, limit) -> int | None:
    if isinstance(usage, (int, float)) and isinstance(limit, (int, float)) and limit > 0:
        return round(100 * usage / limit)
    return None


def _worst_tone(a: str, b: str) -> str:
    order = {_OK: 0, _WARN: 1, _ERR: 2}
    return a if order.get(a, 0) >= order.get(b, 0) else b


def run(cfg: Config | None = None) -> None:
    cfg = cfg or Config.from_env()
    state = SentinelState()
    sentinel = Sentinel(cfg, state=state)
    # start the read-only HTTP/SSE status service (绕开 backend 直推前端)
    from .server import serve
    serve(cfg, state)
    fast = max(1, cfg.liveness_interval_sec)
    deep_every = max(fast, cfg.interval_sec)
    _log(f"up · deep={deep_every}s · liveness={fast}s · filter={cfg.label_filter} · "
         f"http=:{cfg.http_port} · targets={len(cfg.liveness_targets)} · "
         f"signals={'on' if cfg.signals_url else 'off'} · logs={'on' if cfg.log_ingest_url else 'off'}")
    next_deep = 0.0
    while True:
        # heavy stats/size scan every interval_sec; cheap liveness pass in between so a
        # killed layer turns red ≤ ~liveness_interval_sec (AC-1 ≤3s).
        deep = time.monotonic() >= next_deep
        try:
            frame = sentinel.scan_once(deep=deep)
            if deep:
                next_deep = time.monotonic() + deep_every
                _log(f"deep scan · {len(frame['layers'])} layers · status={frame['status']} · "
                     f"infra_alerts={len(frame['infra_alerts'])} · sse_clients={state.client_count}")
        except Exception as exc:  # noqa: BLE001 - the loop must never die
            _log(f"scan error: {exc}")
        time.sleep(fast)
