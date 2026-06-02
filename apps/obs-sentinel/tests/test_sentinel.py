"""Sentinel unit tests with a fake Docker client + captured POSTs (no real socket)."""
from __future__ import annotations

import json

import pytest

from obs_sentinel import sentinel as sentinel_mod
from obs_sentinel.config import Config
from obs_sentinel.docker_client import memory_used_and_limit
from obs_sentinel.sentinel import Sentinel


class FakeDocker:
    """Returns scripted list/inspect/stats. ``inspects`` maps name → State dict."""

    def __init__(self, containers):
        self._containers = containers

    def list_containers(self, label_filter=None):
        return [{"Id": c["name"], "Names": [f"/{c['name']}"], "State": c.get("status", "running")}
                for c in self._containers]

    def inspect(self, cid):
        c = next(x for x in self._containers if x["name"] == cid)
        return {"RestartCount": c.get("restart_count", 0),
                "State": {"Status": c.get("status", "running"),
                          "OOMKilled": c.get("oom_killed", False),
                          "StartedAt": "2026-06-02T00:00:00Z",
                          "Health": {"Status": c.get("health", "none")}}}

    def stats(self, cid):
        c = next(x for x in self._containers if x["name"] == cid)
        return {"memory_stats": {"usage": c.get("mem_usage", 0), "limit": c.get("mem_limit", 0),
                                 "stats": {"inactive_file": 0}}}


@pytest.fixture
def captured(monkeypatch):
    posts = []

    class _Resp:
        def read(self):
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        posts.append((req.full_url, json.loads(req.data.decode()),
                      dict(req.header_items())))
        return _Resp()

    monkeypatch.setattr(sentinel_mod.urllib.request, "urlopen", fake_urlopen)
    return posts


def _cfg():
    return Config(signals_url="http://web/monitor/signals",
                  log_ingest_url="http://web/logs/ingest", token="tok",
                  restart_storm_count=3, restart_storm_window_min=10, mem_pressure_pct=90)


def test_memory_working_set_subtracts_inactive_file():
    used, limit = memory_used_and_limit(
        {"memory_stats": {"usage": 1000, "limit": 2000, "stats": {"inactive_file": 200}}})
    assert used == 800 and limit == 2000


def test_scan_always_posts_signals(captured):
    s = Sentinel(_cfg(), docker=FakeDocker([
        {"name": "zj-quant-data-keeper", "mem_usage": int(4e9), "mem_limit": int(8e9)},
    ]))
    s.scan_once()
    signal_posts = [p for p in captured if p[0].endswith("/monitor/signals")]
    assert len(signal_posts) == 1
    payload = signal_posts[0][1]
    assert payload["containers"][0]["name"] == "zj-quant-data-keeper"
    # the signal carries the service token
    assert signal_posts[0][2].get("X-monitor-token") == "tok"


def test_oom_ships_monitor_log_once_per_kill(captured):
    docker = FakeDocker([{"name": "zj-quant-data-keeper", "oom_killed": True,
                          "restart_count": 5, "mem_usage": int(8e9), "mem_limit": int(8e9)}])
    s = Sentinel(_cfg(), docker=docker)
    s.scan_once()
    s.scan_once()                                   # same restart_count → no re-ship
    oom = [p for p in captured if p[0].endswith("/logs/ingest")
           and p[1]["records"][0]["event"] == "monitor.container.oom_killed"]
    assert len(oom) == 1
    rec = oom[0][1]["records"][0]
    assert rec["source"] == "monitor" and rec["level"] == "CRITICAL"


def test_restart_storm_fires_after_threshold(captured):
    docker = FakeDocker([{"name": "zj-quant-data-keeper", "restart_count": 0,
                          "mem_usage": int(1e9), "mem_limit": int(8e9)}])
    s = Sentinel(_cfg(), docker=docker)
    s.scan_once()                                   # rc=0
    docker._containers[0]["restart_count"] = 4      # +4 over the window ≥ 3
    s.scan_once()
    storm = [p for p in captured if p[0].endswith("/logs/ingest")
             and p[1]["records"][0]["event"] == "monitor.container.restart_storm"]
    assert len(storm) == 1
    assert storm[0][1]["records"][0]["extra"]["restart_delta"] == 4
