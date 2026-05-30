"""Smoke tests for data-keeper (容器化重构 V3 §4).

Sets QUANT_DATA_ROOT / KEEPER_DB_URL to temp locations and disables the scheduler
+ log shipper *before* importing keeper_service (engine + settings are built at
import time), seeds the demo lake, and exercises auth via the trusted
X-Forwarded-User/-Role headers, the write commands, and a heavy-job submit.
"""
from __future__ import annotations

import os
import tempfile

import pytest

_DATA_ROOT = tempfile.mkdtemp(prefix="keeper_data_")
_DB_PATH = os.path.join(tempfile.mkdtemp(prefix="keeper_db_"), "keeper.db")
os.environ["QUANT_DATA_ROOT"] = _DATA_ROOT
os.environ["KEEPER_DB_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["ENABLE_SCHEDULER"] = "false"
os.environ["LOG_INGEST_URL"] = ""          # no shipper in tests

from fastapi.testclient import TestClient  # noqa: E402

from keeper_service.main import create_app  # noqa: E402
from keeper_service.seed_demo import seed  # noqa: E402


def _editor():
    return {"X-Forwarded-User": "alice", "X-Forwarded-Role": "editor"}


def _admin():
    return {"X-Forwarded-User": "root", "X-Forwarded-Role": "admin"}


def _viewer():
    return {"X-Forwarded-User": "bob", "X-Forwarded-Role": "viewer"}


@pytest.fixture(scope="module")
def client():
    seed(_DATA_ROOT)
    return TestClient(create_app())


def test_health(client):
    h = client.get("/health").json()
    assert h["status"] == "ok" and h["service"] == "data-keeper"
    assert h["scheduler"] is False


def test_write_requires_auth(client):
    # no forwarded identity → 401
    r = client.post("/sources", json={"source_id": "x", "display_name": "X"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"


def test_viewer_cannot_write(client):
    r = client.post("/sources", headers=_viewer(),
                    json={"source_id": "src_v", "display_name": "V"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "PERMISSION_DENIED"


def test_create_and_duplicate_source(client):
    r = client.post("/sources", headers=_editor(),
                    json={"source_id": "src_new", "display_name": "新源", "priority": 50})
    assert r.status_code == 200, r.text
    assert r.json()["source_id"] == "src_new"
    dup = client.post("/sources", headers=_editor(),
                      json={"source_id": "src_new", "display_name": "dup"})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "DUPLICATE_RESOURCE"


def test_invalidate_validation_and_trace_echo(client):
    # short reason rejected (same rule as web)
    r = client.post("/batches/does_not_exist/invalidate", headers=_editor(), json={"reason": "no"})
    assert r.status_code == 400
    # trace id echoed
    r2 = client.get("/health", headers={"X-Trace-Id": "t_keepertest0001"})
    assert r2.headers.get("x-trace-id") == "t_keepertest0001"


def test_migrate_dry_run(client):
    # gross_margin was promoted by the seed → a valid migrate dry-run target
    dr = client.post("/fields/raw.fundamentals/gross_margin/migrate",
                     headers=_admin(), json={"dry_run": True}).json()
    assert dr["affected_files"] >= 1 and "after_schema" in dr


def test_heavy_job_submit_and_query(client):
    # compact/execute submits a job to the in-process JobRunner
    r = client.post("/maintenance/compact/execute", headers=_admin(),
                    json={"tables": ["raw.bars_daily"], "confirm": "COMPACT"})
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    # the job is visible to its initiator
    jobs = client.get("/jobs", headers=_admin()).json()["items"]
    assert any(j["job_id"] == job_id for j in jobs)
    one = client.get(f"/jobs/{job_id}", headers=_admin()).json()
    assert one["job_id"] == job_id and one["trace_id"]
