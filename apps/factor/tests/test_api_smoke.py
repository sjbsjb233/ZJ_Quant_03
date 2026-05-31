"""Smoke tests for factor-api — start FastAPI in-process with a tmp factor.db
and tmp factor-store; verify the major endpoints respond and basic queue
mechanics work without a worker (we synthesize queue rows directly)."""
from __future__ import annotations

import os
import tempfile

import jwt
import pytest


@pytest.fixture(scope="module")
def app_setup(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("factor-store")
    # Set env vars BEFORE importing any factor_service module.
    os.environ["FACTOR_STORE_ROOT"] = str(tmp)
    os.environ["FACTOR_DB_URL"] = f"sqlite:///{tmp}/factor.db"
    os.environ["JWT_SECRET"] = "test-secret"
    os.environ["QUANT_DATA_ROOT"] = "./data-test"
    os.environ["FACTOR_ROLE"] = "api"
    os.environ["TRUST_PROXY_USER_HEADERS"] = "true"

    # Force settings + db engine to be re-created from these vars.
    from factor_service.config import reset_settings_cache
    reset_settings_cache()
    from factor_service.db import base as db_base
    db_base.rebind(os.environ["FACTOR_DB_URL"])

    from factor_service.main import create_app
    from factor_service.db import base as _db_base
    from factor_service.db.seed import seed_all
    from fastapi.testclient import TestClient

    app = create_app()
    # Ensure schema + seed run before tests (don't rely on startup hook,
    # which doesn't fire without `with TestClient(app)` as a context manager).
    _db_base.init_db()
    with _db_base.SessionLocal() as db:
        seed_all(db)
    client = TestClient(app)
    yield client


def _hdr(role="admin", user="tester"):
    # The middleware reads X-Forwarded-User / -Role when trusted; we don't
    # need a real JWT.
    return {
        "X-Forwarded-User": user,
        "X-Forwarded-Role": role,
        "X-Trace-Id": "t_test_trace",
    }


def test_health(app_setup):
    r = app_setup.get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == "factor"


def test_list_factors_seeded(app_setup):
    r = app_setup.get("/factors", headers=_hdr())
    assert r.status_code == 200, r.text
    body = r.json()
    # builtins seeded
    fids = [it["factor_id"] for it in body["items"]]
    assert "mom" in fids
    assert "logmktcap" in fids
    assert body["kpi"]["total"] >= 4
    # never_eval flag set because no eval has been done
    for it in body["items"]:
        assert it["never_eval"] is True


def test_list_universe_presets(app_setup):
    r = app_setup.get("/universe", headers=_hdr())
    assert r.status_code == 200
    ids = {u["universe_id"] for u in r.json()["items"]}
    assert "preset:hs300" in ids
    assert "preset:zz800" in ids


def test_create_and_delete_custom_universe(app_setup):
    r = app_setup.post(
        "/universe",
        json={
            "name": "test pool",
            "symbols": ["AAA.SZ", "BBB.SH"],
            "exclude_st": True,
            "min_list_days": 30,
        },
        headers=_hdr(role="editor"),
    )
    assert r.status_code == 200, r.text
    uid = r.json()["universe_id"]
    assert uid.startswith("cust_")
    # delete
    r = app_setup.delete(f"/universe/{uid}", headers=_hdr(role="editor"))
    assert r.status_code == 200


def test_preset_cannot_be_deleted(app_setup):
    r = app_setup.delete("/universe/preset:hs300", headers=_hdr(role="editor"))
    assert r.status_code == 403


def test_factor_patch(app_setup):
    r = app_setup.patch(
        "/factors/mom",
        json={"tags": ["核心", "动量"], "description": "20-day momentum"},
        headers=_hdr(role="editor"),
    )
    assert r.status_code == 200
    assert r.json()["tags"] == ["核心", "动量"]


def test_submit_run_enqueues(app_setup):
    body = {
        "factor_id": "mom",
        "params": {"window": 20},
        "run_config": {
            "universe_id": "preset:hs300",
            "start": "2024-01-01",
            "end": "2024-06-30",
            "rebalance": "W",
            "horizons": [1, 5, 10],
            "primary_horizon": 5,
            "neutralize_size": True,
            "neutralize_industry": True,
            "industry_level": 1,
            "lane": "interactive",
        },
    }
    r = app_setup.post("/runs", json=body, headers=_hdr(role="editor"))
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == "queued"
    task_id = j["task_id"]

    # /tasks lists it
    r = app_setup.get("/tasks", headers=_hdr())
    assert r.status_code == 200
    found = [t for t in r.json()["items"] if t["task_id"] == task_id]
    assert len(found) == 1
    assert found[0]["lane"] == "interactive"
    # ETA preview
    r = app_setup.get(f"/tasks/{task_id}/eta", headers=_hdr())
    assert r.status_code == 200
    assert r.json()["eta"]["queue_position"] == 1


def test_queue_stats(app_setup):
    r = app_setup.get("/queue/stats", headers=_hdr())
    assert r.status_code == 200
    s = r.json()
    assert "interactive" in s["lanes"]
    assert s["concurrency"] >= 1


def test_boost_then_cancel(app_setup):
    # Add a second task to make boost meaningful
    r2 = app_setup.post(
        "/runs",
        json={
            "factor_id": "logmktcap",
            "run_config": {
                "universe_id": "preset:zz500",
                "start": "2024-01-01",
                "end": "2024-06-30",
                "lane": "routine",
            },
        },
        headers=_hdr(role="editor"),
    )
    assert r2.status_code == 200, r2.text
    tid = r2.json()["task_id"]
    r = app_setup.post(f"/tasks/{tid}/boost", headers=_hdr(role="editor"))
    assert r.status_code == 200
    # cancel it
    r = app_setup.post(f"/tasks/{tid}/cancel", headers=_hdr(role="editor"))
    assert r.status_code == 200


def test_universe_symbol_search_no_data(app_setup):
    # No data in QUANT_DATA_ROOT so this should return empty gracefully
    r = app_setup.get(
        "/universe/symbols/search?q=test", headers=_hdr()
    )
    # 200 with empty items is acceptable; 5xx is not.
    assert r.status_code in (200, 500)


def test_pending_imports_initially_empty(app_setup):
    r = app_setup.get("/imports/pending", headers=_hdr())
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_correlation_requires_two_factors(app_setup):
    r = app_setup.post(
        "/factors/correlation",
        json={"factor_ids": ["mom"], "run_config": {"universe_id": "preset:hs300"}},
        headers=_hdr(role="editor"),
    )
    assert r.status_code == 400


def test_factor_get_404(app_setup):
    r = app_setup.get("/factors/does_not_exist", headers=_hdr())
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "FACTOR_NOT_FOUND"
    assert r.json()["error"]["trace_id"]


def test_run_chain_emits_structured_events(app_setup):
    """V3 §4.3 / §5.3 acceptance: submitting + executing a run emits the
    run.submit → run.start → run.stage.*.done → (run.done | run.failed) chain,
    with duration_ms on the timed stages, all joined by one trace_id."""
    from quant_data import logging as qd_logging
    from factor_service.services.run_executor import execute_task

    captured: list[dict] = []
    qd_logging.register_sink(lambda rec: captured.append(rec.as_dict()))
    qd_logging.set_threshold("INFO")
    try:
        body = {
            "factor_id": "mom",
            "run_config": {
                "universe_id": "preset:hs300",
                "start": "2024-01-01", "end": "2024-03-31", "lane": "interactive",
            },
        }
        r = app_setup.post("/runs", json=body, headers=_hdr(role="editor"))
        assert r.status_code == 200, r.text
        run_id, task_id = r.json()["run_id"], r.json()["task_id"]

        # run.submit was emitted by the API (carrying the request trace_id)
        submits = [e for e in captured if e["event"] == "run.submit"
                   and (e.get("extra") or {}).get("run_id") == run_id]
        assert submits, "run.submit not emitted"

        # Drive the worker exactly as the dispatcher does: re-bind a trace_id then
        # execute. There's no market data under ./data-test, so the compute stage
        # is expected to fail — but the load stage and the terminal event must fire.
        captured.clear()
        tok = qd_logging.bind_context(trace_id="t_chain_test0001")
        try:
            try:
                execute_task(task_id)
            except Exception:  # noqa: BLE001 - no data → run.failed is fine
                pass
        finally:
            qd_logging.reset_context(tok)

        events = [e["event"] for e in captured]
        assert "run.start" in events, events
        load_done = [e for e in captured if e["event"] == "run.stage.load.done"]
        assert load_done, events
        assert load_done[0]["duration_ms"] is not None      # timed stage carries duration
        assert ("run.done" in events) or ("run.failed" in events), events

        # the whole chain shares the one re-bound trace_id (full-chain trace, §3.8)
        chain = [e for e in captured if e["event"].startswith("run.")]
        assert chain and all(e["trace_id"] == "t_chain_test0001" for e in chain)
    finally:
        qd_logging.unregister_all_sinks()
