"""Smoke tests for the 运行透视 / Run Trace service path (设计 §2.3-§2.5).

Drives a real evaluation through ``execute_task`` (reader monkeypatched to the
in-memory FakeReader so the run actually computes), then asserts that
``GET /runs/{id}/trace`` lights up: 6-stage pipeline, full preflight checklist
(含通过项), universe funnel, and逐日 distribution过程. Also covers the empty-state
contract for a run that has no trace artifact yet.
"""
from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="module")
def trace_app(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("factor-store-trace")
    os.environ["FACTOR_STORE_ROOT"] = str(tmp)
    os.environ["FACTOR_DB_URL"] = f"sqlite:///{tmp}/factor.db"
    os.environ["JWT_SECRET"] = "test-secret"
    os.environ["QUANT_DATA_ROOT"] = "./data-test"
    os.environ["FACTOR_ROLE"] = "api"
    os.environ["TRUST_PROXY_USER_HEADERS"] = "true"
    # Unlike test_api_smoke, exercise the gate + trace for real.
    os.environ["PREFLIGHT_ENABLED"] = "true"
    os.environ["DEGRADATION_POLICY_PROFILE"] = "strict"
    os.environ["TRACE_ENABLED"] = "true"
    os.environ["TRACE_HISTOGRAM_ENABLED"] = "true"

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


def _hdr(role="admin", user="tester"):
    return {
        "X-Forwarded-User": user,
        "X-Forwarded-Role": role,
        "X-Trace-Id": "t_trace_test",
    }


_RUN_BODY = {
    "factor_id": "mom",
    "params": {"window": 10, "skip": 1},
    "run_config": {
        "universe_id": "preset:hs300",
        "start": "2024-01-02", "end": "2024-03-01",
        "rebalance": "W", "horizons": [1, 5], "primary_horizon": 5,
        "neutralize_size": True, "neutralize_industry": True,
        "industry_level": 1, "lane": "interactive",
    },
}


def _use_fake_reader(monkeypatch):
    """Route both the submit-time gate and the worker through FakeReader.

    The /runs POST runs a submit-time preflight (services.preflight_service) and
    the worker runs its own (services.run_executor); both build a DataReader from
    ``quant_data_root`` (empty ``./data-test`` → everything would block). Patching
    the bound name in each module lets the run pass the gate and compute for real.
    """
    from factor_service.services import preflight_service, run_executor
    from quant_factor.testing import FakeReader

    monkeypatch.setattr(run_executor, "DataReader", lambda root=None: FakeReader())
    monkeypatch.setattr(preflight_service, "DataReader", lambda root=None: FakeReader())


def test_trace_404_for_unknown_run(trace_app):
    r = trace_app.get("/runs/frun_nope/trace", headers=_hdr())
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "RUN_NOT_FOUND"


def test_trace_empty_state_before_execution(trace_app, monkeypatch):
    # A submitted-but-not-executed run exists, but has no trace artifact yet.
    _use_fake_reader(monkeypatch)   # pass the submit-time gate, but don't execute
    r = trace_app.post("/runs", json=_RUN_BODY, headers=_hdr(role="editor"))
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    rr = trace_app.get(f"/runs/{run_id}/trace", headers=_hdr())
    assert rr.status_code == 200
    body = rr.json()
    assert body["available"] is False
    assert "重跑" in body["reason"]


def test_trace_available_after_execution(trace_app, monkeypatch):
    _use_fake_reader(monkeypatch)
    from factor_service.services.run_executor import execute_task

    r = trace_app.post("/runs", json=_RUN_BODY, headers=_hdr(role="editor"))
    assert r.status_code == 200, r.text
    run_id, task_id = r.json()["run_id"], r.json()["task_id"]

    out = execute_task(task_id)
    assert out["status"] in ("success", "degraded"), out

    rr = trace_app.get(f"/runs/{run_id}/trace", headers=_hdr())
    assert rr.status_code == 200, rr.text
    t = rr.json()

    # ── envelope ──
    assert t["available"] is True
    assert t["schema_version"] == 1
    assert t["status"] in ("success", "degraded")

    # ── ① 6-stage pipeline (canonical set present) ──
    stages = {s["stage"]: s for s in t["stages"]}
    assert {
        "load", "preflight", "universe_resolve", "compute", "write", "eval"
    } <= set(stages)
    assert stages["load"]["duration_ms"] is not None
    assert stages["load"]["detail"]["config_resolved"]["universe_id"] == "preset:hs300"

    # ── ② preflight checklist含通过项 ──
    checks = stages["preflight"]["detail"]["checks"]
    assert checks, "preflight checklist must be recorded"
    by_id = {c["check_id"]: c for c in checks}
    assert any(c["status"] == "pass" for c in checks), "通过项必须列出"
    # the real engine codes (not the design mock's示意 codes)
    assert "PF_NEU_IND" in by_id and "PF_NEU_SIZE" in by_id
    # a passing coverage check carries coverage + threshold for the bar
    ind = by_id["PF_NEU_IND"]
    assert ind["status"] == "pass" and ind["coverage"] is not None
    assert ind["threshold"] is not None

    # ── ③ universe funnel ──
    funnel = stages["universe_resolve"]["detail"]["funnel"]
    steps = [f["step"] for f in funnel]
    assert steps[0] == "raw" and steps[-1] == "final"
    assert all("symbols" in f for f in funnel)

    # ── ④/⑤ per-rebalance-day过程 ──
    assert t["days"], "days must be non-empty for a computed run"
    assert t["days_truncated"] is False
    d0 = t["days"][0]
    for k in (
        "dist_raw", "dist_after_winsorize",
        "dist_after_standardize", "dist_after_neutralize",
    ):
        assert k in d0, f"missing {k} in day record"
    assert any("layer_returns" in d for d in t["days"])
    assert any("rank_ic" in d for d in t["days"])
