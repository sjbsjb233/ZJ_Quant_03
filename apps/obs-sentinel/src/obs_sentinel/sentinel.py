"""The sentinel scan loop (可观测层重构方案 §4.6).

每个间隔:
  1. 巡检容器 → 组装观测 → 永远先 POST ``/monitor/signals`` (评估器维护窗口 + 哨兵心跳);
  2. 本地判定 D1/D2/D4，命中即 ship ``source=monitor`` 日志到 ``/logs/ingest``。
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
import urllib.request
from collections import defaultdict, deque

from .config import Config
from .docker_client import DockerClient, DockerError, memory_used_and_limit

_HOST = "obs-sentinel"


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


class Sentinel:
    def __init__(self, cfg: Config, docker: DockerClient | None = None):
        self.cfg = cfg
        self.docker = docker or DockerClient(cfg.docker_socket)
        self._restart_hist: dict[str, deque] = defaultdict(lambda: deque(maxlen=240))
        self._oom_last: dict[str, int] = {}      # name -> restart_count at last OOM ship
        self._stormed: set[str] = set()          # names currently in a shipped storm
        self._mem_over: set[str] = set()         # names currently shipping mem-pressure

    # -- one scan ------------------------------------------------------------
    def scan_once(self) -> list[dict]:
        obs = self._observe()
        self._post_signals(obs)
        self._detect(obs)
        return obs

    def _observe(self) -> list[dict]:
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
                insp = self.docker.inspect(cid)
            except (DockerError, OSError):
                insp = {}
            state = insp.get("State", {}) or {}
            status = state.get("Status") or c.get("State") or "unknown"
            mem_usage = mem_limit = None
            if status == "running":
                try:
                    mem_usage, mem_limit = memory_used_and_limit(self.docker.stats(cid))
                except (DockerError, OSError):
                    pass
            started = _parse_started(state.get("StartedAt"))
            out.append({
                "name": name,
                "status": status,
                "oom_killed": bool(state.get("OOMKilled")),
                "restart_count": int(insp.get("RestartCount", 0) or 0),
                "health": ((state.get("Health") or {}).get("Status") or "none"),
                "mem_usage": mem_usage,
                "mem_limit": mem_limit,
                "uptime_s": int(now - started) if started else None,
            })
            self._restart_hist[name].append((now, int(insp.get("RestartCount", 0) or 0)))
        return out

    # -- always report raw observations to the evaluator ---------------------
    def _post_signals(self, obs: list[dict]) -> None:
        if not self.cfg.signals_url:
            return
        payload = {"observed_at": _now_iso(), "containers": obs}
        self._post(self.cfg.signals_url, payload,
                   headers={"X-Monitor-Token": self.cfg.token})

    # -- local D1/D2/D4 → ship a source=monitor log --------------------------
    def _detect(self, obs: list[dict]) -> None:
        for o in obs:
            name = o["name"]
            # D1 — OOMKilled (ship once per fresh kill)
            if o["oom_killed"] and self._oom_last.get(name) != o["restart_count"]:
                self._oom_last[name] = o["restart_count"]
                self._ship_log("CRITICAL", "monitor.container.oom_killed",
                               f"{name} 被内核 OOM 强杀", o,
                               {"container": name, "restart_count": o["restart_count"]})
            elif not o["oom_killed"]:
                self._oom_last.pop(name, None)

            # D2 — restart storm
            delta = self._restart_delta(name)
            if delta >= self.cfg.restart_storm_count:
                if name not in self._stormed:
                    self._stormed.add(name)
                    self._ship_log("CRITICAL", "monitor.container.restart_storm",
                                   f"{name} 在 {self.cfg.restart_storm_window_min}m 内重启 {delta} 次", o,
                                   {"container": name, "restart_delta": delta,
                                    "window_min": self.cfg.restart_storm_window_min,
                                    "oom_killed": o["oom_killed"]})
            else:
                self._stormed.discard(name)

            # D4 — memory pressure (early warning before OOM)
            pct = self._mem_pct(o)
            if pct is not None and pct >= self.cfg.mem_pressure_pct:
                if name not in self._mem_over:
                    self._mem_over.add(name)
                    self._ship_log("WARN", "monitor.container.mem_pressure",
                                   f"{name} 内存压力 {pct}%", o,
                                   {"container": name, "mem_pct": pct,
                                    "threshold": self.cfg.mem_pressure_pct})
            else:
                self._mem_over.discard(name)

    def _restart_delta(self, name: str) -> int:
        cutoff = time.time() - self.cfg.restart_storm_window_min * 60
        hist = [(t, rc) for (t, rc) in self._restart_hist.get(name, ()) if t >= cutoff]
        if len(hist) < 2:
            return 0
        return max(0, hist[-1][1] - hist[0][1])

    @staticmethod
    def _mem_pct(o: dict) -> int | None:
        u, l = o.get("mem_usage"), o.get("mem_limit")
        if isinstance(u, (int, float)) and isinstance(l, (int, float)) and l > 0:
            return round(100 * u / l)
        return None

    # -- shipping ------------------------------------------------------------
    def _ship_log(self, level: str, event: str, message: str, obs: dict, extra: dict) -> None:
        if not self.cfg.log_ingest_url:
            _log(f"{level} {event} {message}")
            return
        record = {"source": "monitor", "level": level, "logger": "monitor.sentinel",
                  "event": event, "message": message, "host": _HOST,
                  "extra": {**extra, "observed": obs}}
        self._post(self.cfg.log_ingest_url, {"records": [record]},
                   headers={"X-Log-Token": self.cfg.token})
        _log(f"shipped {level} {event} · {message}")

    def _post(self, url: str, payload: dict, headers: dict) -> None:
        try:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=body, method="POST",
                                         headers={"Content-Type": "application/json", **headers})
            urllib.request.urlopen(req, timeout=5)  # noqa: S310 - operator-configured URL
        except Exception as exc:  # noqa: BLE001 - 哨兵不可达不能影响业务, POST 失败只丢上报
            _log(f"POST {url} failed: {exc}")


def run(cfg: Config | None = None) -> None:
    cfg = cfg or Config.from_env()
    sentinel = Sentinel(cfg)
    _log(f"up · interval={cfg.interval_sec}s · filter={cfg.label_filter} · "
         f"signals={'on' if cfg.signals_url else 'off'} · logs={'on' if cfg.log_ingest_url else 'off'}")
    while True:
        try:
            obs = sentinel.scan_once()
            _log(f"scanned {len(obs)} containers")
        except Exception as exc:  # noqa: BLE001 - the loop must never die
            _log(f"scan error: {exc}")
        time.sleep(max(5, cfg.interval_sec))
