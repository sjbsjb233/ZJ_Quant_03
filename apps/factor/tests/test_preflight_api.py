"""API + worker tests for the preflight gate + degradation台账 (设计 §10/§7).

Covers:
  - POST /runs/preflight dry-run (would_block / would_degrade / runnable)
  - POST /runs 422 block at the source (设计 §10.3)
  - worker status persistence: success / degraded / failed-by-preflight
  - GET /runs/{id} degradation block + factor_run_degradation rows
"""
from __future__ import annotations

import datetime as dt
import os
import uuid

import pytest


@pytest.fixture(scope="module")
def app_setup(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("factor-store-pf")
    os.environ["FACTOR_STORE_ROOT"] = str(tmp)
    os.environ["FACTOR_DB_URL"] = f"sqlite:///{tmp}/factor.db"
    os.environ["JWT_SECRET"] = "test-secret"
    os.environ["QUANT_DATA_ROOT"] = "./data-test-empty"   # genuinely empty
    os.environ["FACTOR_ROLE"] = "api"
    os.environ["TRUST_PROXY_USER_HEADERS"] = "true"
    os.environ["PREFLIGHT_ENABLED"] = "true"
    os.environ["DEGRADATION_POLICY_PROFILE"] = "strict"

    from factor_service.config import reset_settings_cache
    reset_settings_cache()
    from factor_service.db import base as db_base
    db_base.rebind(os.environ["FACTOR_DB_URL"])

    from factor_service.main import create_app
    from factor_service.db import base as _db_base
    from factor_service.db.seed import seed_all
    from fastapi.testclient import TestClient

    app = create_app()
    _db_base.init_db()
    with _db_base.SessionLocal() as db:
        seed_all(db)
    yield TestClient(app)


def _hdr(role="editor", user="tester"):
    return {"X-Forwarded-User": user, "X-Forwarded-Role": role, "X-Trace-Id": "t_pf_test"}


def _patch_reader(monkeypatch, fake):
    """Make both the submit-guard and the worker see `fake` instead of disk."""
    import factor_service.services.preflight_service as ps
    import factor_service.services.run_executor as rx
    monkeypatch.setattr(ps, "DataReader", lambda root=None: fake)
    monkeypatch.setattr(rx, "DataReader", lambda root=None: fake)


def _run_config(**kw):
    base = dict(
        universe_id="preset:hs300",
        start="2024-01-02", end="2024-03-01",
        horizons=[1, 5], primary_horizon=5, rebalance="W",
        neutralize_size=True, neutralize_industry=True,
        return_price="close", winsorize="mad", standardize="zscore",
    )
    base.update(kw)
    return base


# ─────────────────────────────────────────────────────────────────────────────
# Dry-run endpoint
# ─────────────────────────────────────────────────────────────────────────────
def test_dry_run_full_data_runnable(app_setup, monkeypatch):
    from quant_factor.testing import FakeReader
    _patch_reader(monkeypatch, FakeReader())
    r = app_setup.post(
        "/runs/preflight",
        json={"factor_id": "mom", "run_config": _run_config()},
        headers=_hdr(),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["runnable"] is True
    assert body["would_block"] == []


def test_dry_run_missing_industry_blocks(app_setup, monkeypatch):
    from quant_factor.testing import FakeReader
    _patch_reader(monkeypatch, FakeReader(with_sectors=False, sector_coverage=0.0))
    r = app_setup.post(
        "/runs/preflight",
        json={"factor_id": "mom", "run_config": _run_config()},
        headers=_hdr(),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["runnable"] is False
    ids = [b["check_id"] for b in body["would_block"]]
    assert "PF_NEU_IND" in ids
    # each block carries human-facing fields for the UI
    blk = next(b for b in body["would_block"] if b["check_id"] == "PF_NEU_IND")
    assert blk["severity"] == "critical" and blk["title"] and blk["suggested_action"]


# ─────────────────────────────────────────────────────────────────────────────
# Submit-time 422 gate (设计 §10.3)
# ─────────────────────────────────────────────────────────────────────────────
def test_submit_blocked_returns_422(app_setup, monkeypatch):
    from quant_factor.testing import FakeReader
    _patch_reader(monkeypatch, FakeReader(with_sectors=False, sector_coverage=0.0))
    r = app_setup.post(
        "/runs",
        json={"factor_id": "mom", "run_config": _run_config()},
        headers=_hdr(),
    )
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "PREFLIGHT_BLOCKED"
    ids = [b["check_id"] for b in err["details"]["would_block"]]
    assert "PF_NEU_IND" in ids


def test_submit_full_data_enqueues(app_setup, monkeypatch):
    from quant_factor.testing import FakeReader
    _patch_reader(monkeypatch, FakeReader())
    r = app_setup.post(
        "/runs",
        json={"factor_id": "mom", "params": {"window": 10}, "run_config": _run_config()},
        headers=_hdr(),
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued"


# ─────────────────────────────────────────────────────────────────────────────
# Worker status persistence
# ─────────────────────────────────────────────────────────────────────────────
def _make_run(factor_id="mom", params=None, **cfg_kw) -> str:
    """Create a FactorRun + queued Task directly (bypasses the submit gate)."""
    from factor_service.db import base as _db_base
    from factor_service.db.models import FactorRun
    from factor_service.queue import store

    run_id = f"frun_{uuid.uuid4().hex[:12]}"
    rc = _run_config(**cfg_kw)
    with _db_base.SessionLocal() as db:
        db.add(FactorRun(
            run_id=run_id, factor_id=factor_id, version="v1",
            params=params or {}, run_config=rc, status="queued",
            initiated_by="tester", trace_id="t_pf_worker",
        ))
        db.commit()
        t = store.enqueue(
            db, task_type="evaluation",
            payload={"run_id": run_id, "factor_id": factor_id, "version": "v1",
                     "params": params or {}, "run_config": rc},
            lane="interactive", run_id=run_id, factor_id=factor_id,
        )
        return run_id, t.task_id


def _get_run(run_id: str):
    from factor_service.db import base as _db_base
    from factor_service.db.models import FactorRun
    with _db_base.SessionLocal() as db:
        return db.get(FactorRun, run_id)


def test_worker_full_data_success(app_setup, monkeypatch):
    from quant_factor.testing import FakeReader
    from factor_service.services.run_executor import execute_task
    _patch_reader(monkeypatch, FakeReader())
    run_id, task_id = _make_run(params={"window": 10})
    out = execute_task(task_id)
    assert out["status"] == "success", out
    r = _get_run(run_id)
    assert r.status == "success"
    assert r.degradation_count == 0
    assert r.degradation_level == "none"


def test_worker_missing_industry_strict_failed(app_setup, monkeypatch):
    from quant_factor.testing import FakeReader
    from factor_service.services.run_executor import execute_task
    _patch_reader(monkeypatch, FakeReader(with_sectors=False, sector_coverage=0.0))
    run_id, task_id = _make_run(params={"window": 10})
    out = execute_task(task_id)
    assert out["status"] == "failed"
    assert out["reason"] == "preflight_blocked"
    r = _get_run(run_id)
    assert r.status == "failed"
    assert r.error == "preflight_blocked"
    assert r.preflight and any(b["check_id"] == "PF_NEU_IND" for b in r.preflight)
    # red line: NEVER success when industry data is missing
    assert r.status != "success"


def test_worker_missing_industry_permissive_degraded(app_setup, monkeypatch):
    from quant_factor.testing import FakeReader
    from factor_service.config import get_settings
    from factor_service.services.run_executor import execute_task
    from factor_service.db.models import FactorRunDegradation
    from factor_service.db import base as _db_base

    monkeypatch.setattr(get_settings(), "degradation_policy_profile", "permissive")
    _patch_reader(monkeypatch, FakeReader(with_sectors=False, sector_coverage=0.0))
    run_id, task_id = _make_run(params={"window": 10})
    out = execute_task(task_id)
    assert out["status"] == "degraded", out
    r = _get_run(run_id)
    assert r.status == "degraded"
    assert r.degradation_count > 0
    assert r.has_critical and r.degradation_level == "severe"
    # rows persisted
    with _db_base.SessionLocal() as db:
        rows = (
            db.query(FactorRunDegradation)
            .filter(FactorRunDegradation.run_id == run_id)
            .all()
        )
        codes = {x.code for x in rows}
    assert "DG_IND_NEUTRAL_SKIPPED" in codes

    # GET /runs/{id} surfaces the degradation block
    resp = app_setup.get(f"/runs/{run_id}", headers=_hdr(role="admin"))
    assert resp.status_code == 200
    deg = resp.json()["degradation"]
    assert deg["level"] == "severe" and deg["count"] > 0 and deg["has_critical"] is True
    assert any(e["code"] == "DG_IND_NEUTRAL_SKIPPED" for e in deg["events"])

    # GET /runs/{id}/degradations returns the full artifact
    resp2 = app_setup.get(f"/runs/{run_id}/degradations", headers=_hdr(role="admin"))
    assert resp2.status_code == 200
    assert resp2.json()["status"] == "degraded"


def test_worker_partial_industry_permissive_degraded(app_setup, monkeypatch):
    from quant_factor.testing import FakeReader
    from factor_service.config import get_settings
    from factor_service.services.run_executor import execute_task

    monkeypatch.setattr(get_settings(), "degradation_policy_profile", "permissive")
    _patch_reader(monkeypatch, FakeReader(sector_coverage=0.60))
    run_id, task_id = _make_run(params={"window": 10})
    out = execute_task(task_id)
    assert out["status"] == "degraded", out
    r = _get_run(run_id)
    assert r.status == "degraded"


def test_severe_factor_blocks_correlation_downstream(app_setup, monkeypatch):
    """REQ-07 / 设计 §9.2: a factor whose latest eval was severe-degraded is
    refused for correlation unless acknowledge_degraded is set."""
    from quant_factor.testing import FakeReader
    from factor_service.config import get_settings
    from factor_service.services.run_executor import execute_task

    # Produce a severe-degraded latest eval for 'mom' (permissive + no sectors).
    monkeypatch.setattr(get_settings(), "degradation_policy_profile", "permissive")
    _patch_reader(monkeypatch, FakeReader(with_sectors=False, sector_coverage=0.0))
    _, task_id = _make_run(params={"window": 10})
    execute_task(task_id)

    body = {
        "factor_ids": ["mom", "logmktcap"],
        "run_config": _run_config(),
    }
    # default: blocked
    r = app_setup.post("/factors/correlation", json=body, headers=_hdr())
    assert r.status_code == 422, r.text
    err = r.json()["error"]
    assert err["code"] == "DEGRADED_INPUT_BLOCKED"
    assert "mom" in err["details"]["severe_factors"]
    # acknowledged: allowed through (enqueues)
    r2 = app_setup.post(
        "/factors/correlation", json={**body, "acknowledge_degraded": True}, headers=_hdr()
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["status"] == "queued"
