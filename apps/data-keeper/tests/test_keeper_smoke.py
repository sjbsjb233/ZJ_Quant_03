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


# ===========================================================================
# 任务自对账 / 回收 (可观测层重构方案 §6.5 · D5/D6) + 扩展 /health (§6.5)
# ===========================================================================
def test_health_exposes_uptime_and_scheduler_tasks(client):
    h = client.get("/health").json()
    assert h["uptime_seconds"] >= 0 and h["started_at"]
    for key in ("jobs_running", "jobs_failed_24h", "jobs_succeeded_24h"):
        assert key in h
    assert set(h["scheduler_tasks"]) == {"crawl", "quality", "maintenance"}
    for task in h["scheduler_tasks"].values():
        assert "last_run_at" in task and "last_success_at" in task


def test_reconcile_orphans_marks_failed_and_logs(client):
    """A leftover running Job (process died) → failed(interrupted) on the next boot."""
    import datetime as dt

    from keeper_service.db import base as db_base
    from keeper_service.db.models import Job
    from keeper_service.reconcile import reconcile_orphans

    with db_base.SessionLocal() as db:
        db.add(Job(job_id="job_orphan1", type="scheduled_maintenance", status="running",
                   initiated_by="scheduler", started_at=dt.datetime.utcnow()))
        db.commit()

    n = reconcile_orphans(db_base.SessionLocal)
    assert n >= 1
    with db_base.SessionLocal() as db:
        job = db.get(Job, "job_orphan1")
        assert job.status == "failed" and "interrupted" in (job.error or "")
        assert job.finished_at is not None


def test_reaper_reclaims_stuck_job_as_timeout(client):
    """running + over budget + no heartbeat → timeout; a fresh/heartbeating one is spared."""
    import datetime as dt

    from keeper_service.config import get_settings
    from keeper_service.db import base as db_base
    from keeper_service.db.models import Job
    from keeper_service.reaper import Reaper

    now = dt.datetime.utcnow()
    stale = now - dt.timedelta(hours=2)             # well past any budget + heartbeat window
    with db_base.SessionLocal() as db:
        db.add(Job(job_id="job_stuck", type="scheduled_maintenance", status="running",
                   initiated_by="scheduler", started_at=stale, progress_at=stale))
        db.add(Job(job_id="job_alive", type="scheduled_maintenance", status="running",
                   initiated_by="scheduler", started_at=stale, progress_at=now))  # heartbeating
        db.commit()

    reaped = Reaper(db_base.SessionLocal, get_settings()).reap_once()
    assert reaped >= 1
    with db_base.SessionLocal() as db:
        assert db.get(Job, "job_stuck").status == "timeout"
        assert db.get(Job, "job_alive").status == "running"   # spared (recent heartbeat)
