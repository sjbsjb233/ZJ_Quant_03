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

    def inspect(self, cid: str) -> dict:
        return self._get(f"/containers/{cid}/json") or {}

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
