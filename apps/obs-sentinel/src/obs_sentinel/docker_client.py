"""Tiny Docker Engine API client over the unix socket (stdlib only).

We deliberately avoid the ``docker`` SDK so the image is minimal and dependency-
free. The daemon serves HTTP over ``/var/run/docker.sock``; we只读 list / inspect /
stats. Version-less paths map to the daemon's latest supported API.
"""
from __future__ import annotations

import http.client
import json
import socket
import urllib.parse


class DockerError(RuntimeError):
    pass


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, socket_path: str, timeout: float = 5.0):
        super().__init__("localhost", timeout=timeout)
        self._socket_path = socket_path

    def connect(self) -> None:  # noqa: D401
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        sock.connect(self._socket_path)
        self.sock = sock


class DockerClient:
    def __init__(self, socket_path: str = "/var/run/docker.sock", timeout: float = 5.0):
        self._path = socket_path
        self._timeout = timeout

    def _get(self, path: str):
        conn = _UnixHTTPConnection(self._path, self._timeout)
        try:
            conn.request("GET", path)
            resp = conn.getresponse()
            body = resp.read()
            if resp.status != 200:
                raise DockerError(f"docker GET {path} → {resp.status}")
            return json.loads(body) if body else None
        finally:
            conn.close()

    def list_containers(self, label_filter: str | None = None) -> list[dict]:
        path = "/containers/json?all=1"
        if label_filter:
            filt = json.dumps({"label": [label_filter]})
            path += "&filters=" + urllib.parse.quote(filt)
        return self._get(path) or []

    def inspect(self, cid: str, size: bool = False) -> dict:
        # size=true returns SizeRw (可写层大小) — used for D15 core-dump detection.
        # It is *expensive* on the daemon, so callers opt in only when needed.
        path = f"/containers/{cid}/json"
        if size:
            path += "?size=true"
        return self._get(path) or {}

    def stats(self, cid: str) -> dict:
        # one-shot, non-streaming snapshot
        return self._get(f"/containers/{cid}/stats?stream=false&one-shot=true") or {}


def memory_used_and_limit(stats: dict) -> tuple[int | None, int | None]:
    """Compute working-set memory (usage minus inactive file cache) + limit, the
    same way the docker CLI does, across cgroup v1/v2."""
    mem = (stats or {}).get("memory_stats") or {}
    usage = mem.get("usage")
    limit = mem.get("limit")
    if usage is None:
        return None, limit
    detail = mem.get("stats") or {}
    inactive = detail.get("inactive_file", detail.get("total_inactive_file", 0)) or 0
    used = usage - inactive if usage >= inactive else usage
    return int(used), (int(limit) if limit else None)


def _online_cpus(cpu: dict) -> int:
    return cpu.get("online_cpus") or len((cpu.get("cpu_usage") or {}).get("percpu_usage") or []) or 1


def cpu_percent(stats: dict) -> float | None:
    """Per-container CPU% from one stats snapshot's cpu/precpu deltas — the exact
    formula `docker stats` uses (D14 饱和度). Returns None when the deltas aren't
    computable yet (first sample / paused container).

    NOTE: a ``one-shot=true`` stats read zeroes ``precpu_stats`` (system becomes
    None), so this returns None against the live daemon — the sentinel instead
    uses :func:`cpu_totals` + its own previous sample (see Sentinel._cpu_delta)."""
    s = stats or {}
    cpu = s.get("cpu_stats") or {}
    pre = s.get("precpu_stats") or {}
    cur_usage = (cpu.get("cpu_usage") or {}).get("total_usage")
    pre_usage = (pre.get("cpu_usage") or {}).get("total_usage")
    sys_cur = cpu.get("system_cpu_usage")
    sys_pre = pre.get("system_cpu_usage")
    if None in (cur_usage, pre_usage, sys_cur, sys_pre):
        return None
    cpu_delta = cur_usage - pre_usage
    sys_delta = sys_cur - sys_pre
    if sys_delta <= 0 or cpu_delta < 0:
        return None
    return round((cpu_delta / sys_delta) * _online_cpus(cpu) * 100.0, 1)


def cpu_totals(stats: dict) -> tuple[int, int, int] | None:
    """Extract (container_cpu_ns, system_cpu_ns, online_cpus) from a stats read.

    These cumulative counters ARE populated even with ``one-shot=true`` (unlike
    precpu), so the sentinel can diff them against its own previous sample to get
    an accurate CPU% over the deep-scan interval — no 1s-per-container blocking
    second read on the daemon."""
    s = stats or {}
    cpu = s.get("cpu_stats") or {}
    total = (cpu.get("cpu_usage") or {}).get("total_usage")
    system = cpu.get("system_cpu_usage")
    if total is None or system is None:
        return None
    return int(total), int(system), _online_cpus(cpu)
