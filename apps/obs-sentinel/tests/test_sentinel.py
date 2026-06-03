"""Sentinel unit tests with a fake Docker client + captured POSTs (no real socket).

Covers the two-plane refactor (可观测层重构方案 §5): v2 signals (containers + infra +
conditions), L0/D1-D4/D13/D14/D15 detection, the published status frame, the local
JSONL fallback, and the stdlib JWT verifier.
"""
from __future__ import annotations

import json

import pytest

from obs_sentinel import sentinel as sentinel_mod
from obs_sentinel.config import Config, ProbeTarget
from obs_sentinel.docker_client import cpu_percent, memory_used_and_limit
from obs_sentinel.jsonl import JsonlWriter
from obs_sentinel.jwtmini import verify as jwt_verify
from obs_sentinel.sentinel import Sentinel
from obs_sentinel.state import SentinelState


class FakeDocker:
    """Returns scripted list/inspect/stats. Each container dict carries its fields."""

    def __init__(self, containers):
        self._containers = containers

    def list_containers(self, label_filter=None):
        return [{"Id": c["name"], "Names": [f"/{c['name']}"], "State": c.get("status", "running")}
                for c in self._containers]

    def inspect(self, cid, size=False):
        c = next(x for x in self._containers if x["name"] == cid)
        insp = {"RestartCount": c.get("restart_count", 0),
                "State": {"Status": c.get("status", "running"),
                          "OOMKilled": c.get("oom_killed", False),
                          "StartedAt": "2026-06-02T00:00:00Z",
                          "Health": {"Status": c.get("health", "none")}}}
        if size:
            insp["SizeRw"] = c.get("rw_size_bytes", 0)
        return insp

    def stats(self, cid):
        c = next(x for x in self._containers if x["name"] == cid)
        return {"memory_stats": {"usage": c.get("mem_usage", 0), "limit": c.get("mem_limit", 0),
                                 "stats": {"inactive_file": 0}},
                "cpu_stats": {"cpu_usage": {"total_usage": c.get("_cpu_cur", 0)},
                              "system_cpu_usage": c.get("_sys_cur", 0), "online_cpus": 1},
                "precpu_stats": {"cpu_usage": {"total_usage": c.get("_cpu_pre", 0)},
                                 "system_cpu_usage": c.get("_sys_pre", 0)}}


@pytest.fixture
def captured(monkeypatch):
    posts = []

    class _Resp:
        status = 200

        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        posts.append((req.full_url, json.loads(req.data.decode()) if req.data else None,
                      dict(req.header_items())))
        return _Resp()

    monkeypatch.setattr(sentinel_mod.urllib.request, "urlopen", fake_urlopen)
    return posts


def _cfg(**kw):
    base = dict(signals_url="http://web/monitor/signals",
                log_ingest_url="http://web/logs/ingest", token="tok",
                restart_storm_count=3, restart_storm_window_min=10, mem_pressure_pct=90,
                cpu_sat_pct=90, cpu_sat_for_sec=0, mem_pressure_for_sec=0,
                core_dump_rw_gb=2.0, jsonl_dir="/tmp/obs-sentinel-test")
    base.update(kw)
    return Config(**base)


def _sentinel(containers, jsonl_dir, **cfg_kw):
    cfg = _cfg(jsonl_dir=str(jsonl_dir), **cfg_kw)
    return Sentinel(cfg, docker=FakeDocker(containers), state=SentinelState(),
                    jsonl=JsonlWriter(str(jsonl_dir)))


# ── docker helpers ────────────────────────────────────────────────────────────
def test_memory_working_set_subtracts_inactive_file():
    used, limit = memory_used_and_limit(
        {"memory_stats": {"usage": 1000, "limit": 2000, "stats": {"inactive_file": 200}}})
    assert used == 800 and limit == 2000


def test_cpu_percent_from_deltas():
    snap = {"cpu_stats": {"cpu_usage": {"total_usage": 200}, "system_cpu_usage": 2000, "online_cpus": 2},
            "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1000}}
    # (100/1000) * 2 * 100 = 20.0
    assert cpu_percent(snap) == 20.0


def test_cpu_percent_none_on_first_sample():
    assert cpu_percent({"cpu_stats": {}, "precpu_stats": {}}) is None


# ── signals v2 ────────────────────────────────────────────────────────────────
def test_scan_posts_v2_signals_with_conditions(captured, tmp_path):
    s = _sentinel([{"name": "zj-quant-data-keeper", "mem_usage": int(4e9), "mem_limit": int(8e9)}], tmp_path)
    s.scan_once()
    sig = [p for p in captured if p[0].endswith("/monitor/signals")]
    assert len(sig) == 1
    payload = sig[0][1]
    assert payload["v"] == 2
    assert payload["containers"][0]["name"] == "zj-quant-data-keeper"
    assert "conditions" in payload and "infra" in payload
    assert sig[0][2].get("X-monitor-token") == "tok"


def test_oom_emits_condition_and_ships_once(captured, tmp_path):
    s = _sentinel([{"name": "zj-quant-data-keeper", "oom_killed": True, "restart_count": 5,
                    "mem_usage": int(8e9), "mem_limit": int(8e9)}], tmp_path)
    frame = s.scan_once()
    s.scan_once()                                   # still oom → no re-ship
    # condition present in the v2 signal payload
    sig = [p for p in captured if p[0].endswith("/monitor/signals")][0][1]
    codes = [c["code"] for c in sig["conditions"]]
    assert "D1.oom_killed" in codes
    # shipped exactly one monitor log for the new D1 transition
    oom_logs = [p for p in captured if p[0].endswith("/logs/ingest")
                and p[1]["records"][0]["event"] == "monitor.D1.oom_killed"]
    assert len(oom_logs) == 1
    rec = oom_logs[0][1]["records"][0]
    assert rec["source"] == "monitor" and rec["level"] == "CRITICAL"


def test_restart_storm_emits_condition(captured, tmp_path):
    docker = FakeDocker([{"name": "zj-quant-data-keeper", "restart_count": 0,
                          "mem_usage": int(1e9), "mem_limit": int(8e9)}])
    s = Sentinel(_cfg(jsonl_dir=str(tmp_path)), docker=docker, state=SentinelState(),
                 jsonl=JsonlWriter(str(tmp_path)))
    s.scan_once()
    docker._containers[0]["restart_count"] = 4      # +4 over window ≥ 3
    s.scan_once()
    sig = [p for p in captured if p[0].endswith("/monitor/signals")][-1][1]
    storm = [c for c in sig["conditions"] if c["code"] == "D2.restart_storm"]
    assert storm and storm[0]["detail"]["restart_delta"] == 4


def test_core_dump_d15_when_rw_layer_balloons(captured, tmp_path):
    s = _sentinel([{"name": "zj-quant-web-backend", "rw_size_bytes": 67 * 1024 ** 3,
                    "mem_usage": int(1e9), "mem_limit": int(8e9)}], tmp_path)
    frame = s.scan_once()
    codes = [c["code"] for c in [c for p in captured if p[0].endswith("/monitor/signals")
                                 for c in p[1]["conditions"]]]
    assert "D15.core_dump" in codes
    assert frame["infra"]["core_dumps"] and frame["infra"]["core_dumps"][0]["rw_size_gb"] == 67.0


def test_cpu_saturation_d14(captured, tmp_path):
    # CPU% is diffed across two scans (cumulative counters); a single sample yields None.
    docker = FakeDocker([{"name": "zj-quant-factor-worker", "mem_usage": int(1e9),
                          "mem_limit": int(8e9), "_cpu_cur": 0, "_sys_cur": 0}])
    s = Sentinel(_cfg(jsonl_dir=str(tmp_path), cpu_sat_pct=90, cpu_sat_for_sec=0),
                 docker=docker, state=SentinelState(), jsonl=JsonlWriter(str(tmp_path)))
    s.scan_once()                                   # baseline sample (cpu None)
    docker._containers[0].update(_cpu_cur=950, _sys_cur=1000)   # Δ = 95% of 1 cpu
    s.scan_once()
    sig = [p for p in captured if p[0].endswith("/monitor/signals")][-1][1]
    assert any(c["code"] == "D14.cpu_saturation" for c in sig["conditions"])


def test_d14_not_until_sustained_and_resets_on_dip(captured, tmp_path, monkeypatch):
    """DET-14a/c: CPU high but not sustained ≥ for_sec → no fire; a dip mid-window
    resets the duration latch so it doesn't false-trigger. CPU% is computed from
    cumulative-counter deltas across scans."""
    clock = {"t": 1000.0}
    monkeypatch.setattr(sentinel_mod.time, "time", lambda: clock["t"])
    docker = FakeDocker([{"name": "zj-quant-factor-worker", "mem_usage": int(1e9),
                          "mem_limit": int(8e9), "_cpu_cur": 0, "_sys_cur": 0}])
    s = Sentinel(_cfg(jsonl_dir=str(tmp_path), cpu_sat_pct=90, cpu_sat_for_sec=10),
                 docker=docker, state=SentinelState(), jsonl=JsonlWriter(str(tmp_path)))

    def codes():
        return [c["code"] for c in [p for p in captured if p[0].endswith("/monitor/signals")][-1][1]["conditions"]]

    def busy():  # +95% of one cpu since the last sample
        docker._containers[0]["_cpu_cur"] += 950
        docker._containers[0]["_sys_cur"] += 1000

    def idle():  # 0% cpu since last sample
        docker._containers[0]["_sys_cur"] += 1000

    s.scan_once()                       # t=1000 baseline (prev set)
    busy(); clock["t"] = 1002; s.scan_once()   # 95% but 0s elapsed → not yet
    assert "D14.cpu_saturation" not in codes()
    busy(); clock["t"] = 1006; s.scan_once()   # 4s < 10s → still not
    assert "D14.cpu_saturation" not in codes()
    idle(); clock["t"] = 1008; s.scan_once()   # dip to 0% → latch resets
    busy(); clock["t"] = 1010; s.scan_once()   # re-high (0s elapsed)
    assert "D14.cpu_saturation" not in codes()
    busy(); clock["t"] = 1014; s.scan_once()   # 4s since re-high (<10s) → not
    assert "D14.cpu_saturation" not in codes()
    busy(); clock["t"] = 1022; s.scan_once()   # 12s sustained (≥10s) → fires
    assert "D14.cpu_saturation" in codes()


def test_d15_boundary(captured, tmp_path):
    """DET-15a/b: rw just below threshold → no D15; at/over → CRITICAL."""
    gib = 1024 ** 3
    below = _sentinel([{"name": "zj-quant-data-writer", "rw_size_bytes": int(0.99 * gib),
                        "mem_usage": int(1e9), "mem_limit": int(8e9)}], tmp_path, core_dump_rw_gb=1.0)
    below.scan_once()
    c1 = [p for p in captured if p[0].endswith("/monitor/signals")][-1][1]["conditions"]
    assert not any(c["code"] == "D15.core_dump" for c in c1)
    over = _sentinel([{"name": "zj-quant-data-writer", "rw_size_bytes": int(1.0 * gib),
                       "mem_usage": int(1e9), "mem_limit": int(8e9)}], tmp_path, core_dump_rw_gb=1.0)
    over.scan_once()
    c2 = [p for p in captured if p[0].endswith("/monitor/signals")][-1][1]["conditions"]
    d15 = [c for c in c2 if c["code"] == "D15.core_dump"]
    assert d15 and d15[0]["severity"] == "critical"


# ── L0 liveness ───────────────────────────────────────────────────────────────
def test_l0_unreachable_emits_condition(captured, tmp_path, monkeypatch):
    monkeypatch.setattr(sentinel_mod, "probe_liveness", lambda url, t: False)
    cfg = _cfg(jsonl_dir=str(tmp_path), liveness_grace_sec=0,
               liveness_targets=[ProbeTarget("data", "zj-quant-data-keeper",
                                             "http://data-keeper:8300/health")])
    s = Sentinel(cfg, docker=FakeDocker([{"name": "zj-quant-data-keeper",
                                          "mem_usage": int(1e9), "mem_limit": int(8e9)}]),
                 state=SentinelState(), jsonl=JsonlWriter(str(tmp_path)))
    frame = s.scan_once()
    sig = [p for p in captured if p[0].endswith("/monitor/signals")][-1][1]
    assert any(c["code"] == "L0.unreachable" for c in sig["conditions"])
    assert frame["layers"]["data"]["reachable"] is False
    assert frame["layers"]["data"]["state"] == "error"
    assert frame["status"] == "down"


def test_killed_container_gone_but_target_configured_is_down(captured, tmp_path):
    """Regression: a killed container disappears from the docker list, but a
    configured liveness target for that layer means the frame must still show it
    down (reachable=false AND state=error) — never a false-green (AC-01)."""
    cfg = _cfg(jsonl_dir=str(tmp_path),
               liveness_targets=[ProbeTarget("backend", "zj-quant-web-backend",
                                             "http://web-backend:8000/health")])
    # docker lists NO backend container (it was killed/removed); only data is up.
    s = Sentinel(cfg, docker=FakeDocker([{"name": "zj-quant-data-keeper",
                                          "mem_usage": int(1e9), "mem_limit": int(8e9)}]),
                 state=SentinelState(), jsonl=JsonlWriter(str(tmp_path)))
    frame = s.scan_once()
    assert frame["layers"]["backend"]["reachable"] is False
    assert frame["layers"]["backend"]["state"] == "error"
    assert frame["status"] == "down"


# ── D13 disk ──────────────────────────────────────────────────────────────────
def test_d13_disk_over_crit(captured, tmp_path, monkeypatch):
    monkeypatch.setattr(sentinel_mod, "disk_usage",
                        lambda path: {"used_pct": 96, "used_bytes": 96, "total_bytes": 100})
    cfg = _cfg(jsonl_dir=str(tmp_path), disk_mounts=[("/", "/host-root")],
               disk_warn_pct=85, disk_crit_pct=95)
    s = Sentinel(cfg, docker=FakeDocker([{"name": "zj-quant-web-backend",
                                          "mem_usage": int(1e9), "mem_limit": int(8e9)}]),
                 state=SentinelState(), jsonl=JsonlWriter(str(tmp_path)))
    frame = s.scan_once()
    disk = frame["infra"]["disk"]
    assert disk and disk[0]["mount"] == "/" and disk[0]["state"] == "error"
    sig = [p for p in captured if p[0].endswith("/monitor/signals")][-1][1]
    d13 = [c for c in sig["conditions"] if c["code"] == "D13.disk"]
    assert d13 and d13[0]["severity"] == "critical"


# ── status frame + state ──────────────────────────────────────────────────────
def test_shallow_scan_carries_deep_metrics(captured, tmp_path):
    """The fast liveness pass (deep=False) skips docker stats but must carry the
    last deep cpu/mem/rw so the frame keeps showing them (and L0 still flips fast)."""
    docker = FakeDocker([{"name": "zj-quant-data-keeper", "mem_usage": int(4e9),
                          "mem_limit": int(8e9), "rw_size_bytes": 123,
                          "_cpu_pre": 0, "_cpu_cur": 100, "_sys_pre": 0, "_sys_cur": 1000}])
    s = Sentinel(_cfg(jsonl_dir=str(tmp_path)), docker=docker, state=SentinelState(),
                 jsonl=JsonlWriter(str(tmp_path)))
    deep = s.scan_once(deep=True)
    assert deep["layers"]["data"]["mem_pct"] == 50
    # shallow pass: stats skipped, but carried metrics persist in the frame
    shallow = s.scan_once(deep=False)
    assert shallow["layers"]["data"]["mem_pct"] == 50
    assert shallow["layers"]["data"].get("cpu_pct") == deep["layers"]["data"].get("cpu_pct")


def test_frame_published_to_state(captured, tmp_path):
    state = SentinelState()
    s = Sentinel(_cfg(jsonl_dir=str(tmp_path)),
                 docker=FakeDocker([{"name": "zj-quant-web-backend",
                                     "mem_usage": int(1e9), "mem_limit": int(8e9)}]),
                 state=state, jsonl=JsonlWriter(str(tmp_path)))
    s.scan_once()
    cur = state.current()
    assert cur is not None and cur["source"] == "sentinel" and cur["v"] == 1
    assert "frontend" in cur["layers"] and "monitor" in cur["layers"]


# ── JSONL fallback ────────────────────────────────────────────────────────────
def test_jsonl_fallback_written_before_ship(captured, tmp_path):
    s = _sentinel([{"name": "zj-quant-data-keeper", "oom_killed": True, "restart_count": 1,
                    "mem_usage": int(1e9), "mem_limit": int(8e9)}], tmp_path)
    s.scan_once()
    path = tmp_path / "monitor.jsonl"
    assert path.exists()
    line = json.loads(path.read_text().strip().splitlines()[0])
    assert line["source"] == "monitor" and line["event"] == "monitor.D1.oom_killed"


def test_jsonl_survives_post_failure(tmp_path, monkeypatch):
    # POSTs fail, but the on-disk record must still be there (the P0 fix).
    def boom(req, timeout=None):
        raise OSError("backend down")
    monkeypatch.setattr(sentinel_mod.urllib.request, "urlopen", boom)
    s = _sentinel([{"name": "zj-quant-data-keeper", "oom_killed": True, "restart_count": 1,
                    "mem_usage": int(1e9), "mem_limit": int(8e9)}], tmp_path)
    s.scan_once()
    assert (tmp_path / "monitor.jsonl").exists()


# ── JWT verifier ──────────────────────────────────────────────────────────────
def _make_jwt(secret, claims):
    import base64
    import hashlib
    import hmac

    def b64(b):
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    h = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    p = b64(json.dumps(claims).encode())
    sig = hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    return f"{h}.{p}.{b64(sig)}"


def test_jwt_verify_accepts_valid_and_rejects_tampered():
    tok = _make_jwt("s3cret", {"sub": "alice", "exp": 9999999999})
    assert jwt_verify(tok, "s3cret")["sub"] == "alice"
    assert jwt_verify(tok, "wrong-secret") is None
    assert jwt_verify(tok + "x", "s3cret") is None
    assert jwt_verify(_make_jwt("s3cret", {"exp": 1}), "s3cret") is None  # expired
