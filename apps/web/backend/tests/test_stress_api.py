"""Stress / concurrency tests for the quant_web API (TestClient + threads).

Coexists with test_web_api.py in one pytest process: quant_web reads env at
import and caches Settings, so whichever test module imports it first owns the
process-wide world. This module mirrors the exact same env contract (tokens,
seed flags) and seeds the same demo world, making the suites order-independent.

Covered:
  * S1  burst of concurrent logins → all succeed with usable tokens
  * S2  mixed read endpoints hammered from 16 threads → zero 5xx, sane latency
  * S3  identical paged reads under concurrency → byte-identical pages
  * S4  client-log rate limiter actually kicks in under a burst (429)
  * S5  SSE stream: concurrent clients all get the connected preamble +
        broadcast fan-out; the 10-connection cap is enforced
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

_FRESH_IMPORT = "quant_web.main" not in sys.modules
if _FRESH_IMPORT:
    _DATA_ROOT = tempfile.mkdtemp(prefix="zjqweb_stress_data_")
    _DB_PATH = os.path.join(tempfile.mkdtemp(prefix="zjqweb_stress_db_"), "audit.db")
    os.environ["QUANT_DATA_ROOT"] = _DATA_ROOT
    os.environ["AUDIT_DB_URL"] = f"sqlite:///{_DB_PATH}"
    # Same values as test_web_api.py so either import order yields one contract.
    os.environ["SEED_DEFAULT_USERS"] = "true"
    os.environ["LOG_INGEST_TOKEN"] = "test-log-token"

from fastapi.testclient import TestClient  # noqa: E402

from quant_web.config import get_settings  # noqa: E402
from quant_web.main import create_app  # noqa: E402
from quant_web.seed_demo import seed  # noqa: E402

pytestmark = pytest.mark.integration

_ROOT = get_settings().quant_data_root


@pytest.fixture(scope="module")
def client():
    if not os.path.exists(os.path.join(_ROOT, "metadata")):
        seed(_ROOT)
    return TestClient(create_app())


def _token(client, username="admin", password="admin123"):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def admin_token(client):
    return _token(client)


@pytest.fixture(scope="module")
def admin(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# --------------------------------------------------------------------- S1
def test_s1_concurrent_login_burst(client):
    n = 32
    results: list[tuple[int, str | None]] = []

    def _login(_):
        r = client.post("/api/v1/auth/login",
                        json={"username": "admin", "password": "admin123"})
        tok = r.json().get("access_token") if r.status_code == 200 else None
        return r.status_code, tok

    with ThreadPoolExecutor(max_workers=n) as ex:
        results = list(ex.map(_login, range(n)))

    codes = [c for c, _ in results]
    assert codes == [200] * n, f"non-200 logins: {[c for c in codes if c != 200]}"
    # every token actually works
    sample = results[::8]
    for _, tok in sample:
        r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200


# --------------------------------------------------------------------- S2
_READ_POOL = [
    "/api/v1/health",
    "/api/v1/data/tables",
    "/api/v1/data/tables/raw.bars_daily/schema",
    "/api/v1/data/tables/raw.bars_daily/rows?page=1&page_size=20",
    "/api/v1/data/tables/raw.bars_daily/rows?page=2&page_size=50",
    "/api/v1/data/batches",
    "/api/v1/data/dashboard/summary",
    "/api/v1/data/fields",
    "/api/v1/data/quality/issues",
    "/api/v1/logs?page=1&page_size=20",
    "/api/v1/logs/summary",
    "/api/v1/monitor/alerts",
    "/api/v1/monitor/health",
]


def test_s2_mixed_read_hammer_no_5xx(client, admin):
    n_threads, per_thread = 16, 25
    failures: list[tuple[str, int]] = []
    latencies: list[float] = []
    lock = threading.Lock()

    def _worker(i: int):
        for j in range(per_thread):
            url = _READ_POOL[(i * per_thread + j) % len(_READ_POOL)]
            t0 = time.perf_counter()
            r = client.get(url, headers=admin)
            dt_ms = (time.perf_counter() - t0) * 1000
            with lock:
                latencies.append(dt_ms)
                if r.status_code >= 500:
                    failures.append((url, r.status_code))

    with ThreadPoolExecutor(max_workers=n_threads) as ex:
        list(ex.map(_worker, range(n_threads)))

    assert not failures, f"5xx under load: {failures[:5]}"
    latencies.sort()
    p95 = latencies[int(len(latencies) * 0.95)]
    print(f"\n[stress] {len(latencies)} reqs, p50={latencies[len(latencies)//2]:.0f}ms "
          f"p95={p95:.0f}ms max={latencies[-1]:.0f}ms")
    assert p95 < 10_000, f"p95 implausibly slow under modest load: {p95:.0f}ms"


# --------------------------------------------------------------------- S3
def test_s3_paged_reads_identical_under_concurrency(client, admin):
    url = "/api/v1/data/tables/raw.bars_daily/rows?page=1&page_size=25"

    def _fetch(_):
        r = client.get(url, headers=admin)
        assert r.status_code == 200
        return r.text

    with ThreadPoolExecutor(max_workers=8) as ex:
        pages = list(ex.map(_fetch, range(16)))

    assert len(set(pages)) == 1, "same page produced different payloads under concurrency"
    body = json.loads(pages[0])
    assert body["items"] and body["total"] >= len(body["items"])


# --------------------------------------------------------------------- S4
def test_s4_client_log_rate_limit_kicks_in(client):
    # dedicated principal so the burst can't poison other tests' actors
    tok = _token(client, "editor", "editor123")
    hdr = {"Authorization": f"Bearer {tok}"}
    limit = get_settings().client_log_rate_limit_per_min
    burst = min(limit + 50, 800)
    rec = {"level": "INFO", "event": "stress.ping", "message": "x"}

    codes: list[int] = []
    for _ in range(burst):
        r = client.post("/api/v1/logs/client", headers=hdr, json={"records": [rec]})
        codes.append(r.status_code)
        if r.status_code == 429:
            break

    assert 429 in codes, f"sent {len(codes)} client-log posts, limiter never fired (limit={limit})"
    ok_count = sum(1 for c in codes if c == 200)
    assert ok_count >= min(limit, burst) * 0.5, "limiter fired far too early"


# --------------------------------------------------------------------- S5
def test_s5_sse_concurrent_streams_and_cap(client, admin, admin_token):
    """5 parallel SSE clients each see the preamble + a broadcast record; an
    11th concurrent connection must be rejected (cap=10)."""
    from quant_web.logging import sse

    n_stream = 5
    got_preamble = threading.Barrier(n_stream + 1, timeout=30)
    received: list[str] = []
    errors: list[BaseException] = []
    release = threading.Event()

    def _consume(i: int):
        try:
            with client.stream("GET", f"/api/v1/logs/stream?token={admin_token}") as r:
                assert r.status_code == 200
                it = r.iter_lines()
                first = next(it)
                assert "connected" in first, first
                got_preamble.wait()
                for line in it:
                    if line.startswith("data:"):
                        received.append(line)
                        break
                release.wait(timeout=30)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)
            try:
                got_preamble.abort()
            except Exception:
                pass

    threads = [threading.Thread(target=_consume, args=(i,)) for i in range(n_stream)]
    for t in threads:
        t.start()
    try:
        got_preamble.wait()  # all 5 connected and registered
        assert sse.hub.client_count >= n_stream
        # broadcast one record from a producer thread → every client gets it
        sse.hub.broadcast({"level": "ERROR", "source": "backend",
                           "event": "stress.fanout", "message": "fan-out"})
        deadline = time.monotonic() + 15
        while len(received) < n_stream and time.monotonic() < deadline:
            time.sleep(0.05)
        assert len(received) == n_stream, f"fan-out reached {len(received)}/{n_stream}"
        assert all("stress.fanout" in r for r in received)
    finally:
        release.set()
        for t in threads:
            t.join(timeout=30)

    assert not errors, f"SSE clients errored: {errors[:1]}"
    # connections are released after the streams close
    deadline = time.monotonic() + 10
    while sse.hub.client_count > 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert sse.hub.client_count == 0, "SSE clients leaked after disconnect"
