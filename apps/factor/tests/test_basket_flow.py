"""End-to-end basket flow (因子层重构 §14.2 L2): create → full run → orchestrate
→ snapshot → grid → prune → selection → export. Drives the worker in-process
with a FakeReader (no on-disk data layer), simulating the dispatcher's advance
hook after each child finalizes."""
from __future__ import annotations

import datetime as dt
import os

import pytest


@pytest.fixture(scope="module")
def setup(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("bk-store")
    os.environ["FACTOR_STORE_ROOT"] = str(tmp)
    os.environ["FACTOR_DB_URL"] = f"sqlite:///{tmp}/factor.db"
    os.environ["JWT_SECRET"] = "test-secret"
    os.environ["QUANT_DATA_ROOT"] = "./data-test"
    os.environ["FACTOR_ROLE"] = "api"
    os.environ["TRUST_PROXY_USER_HEADERS"] = "true"
    os.environ["PREFLIGHT_ENABLED"] = "false"
    os.environ["TRACE_ENABLED"] = "false"

    from factor_service.config import reset_settings_cache
    reset_settings_cache()
    from factor_service.db import base as db_base
    db_base.rebind(os.environ["FACTOR_DB_URL"])
    db_base.init_db()

    from quant_factor.testing import FakeReader

    class TReader(FakeReader):
        def latest_date_in_view(self, table_name, on_or_before=None, time_col="date"):
            return self.dates[-1]

        def read_fundamentals(self, *a, **k):
            import pyarrow as pa
            return pa.table({"symbol": [], "publish_date": []})

    import factor_service.services.basket_executor as bx
    bx.DataReader = lambda root=None: TReader(n_days=90, n_symbols=40)

    from factor_service.db import base as _db_base
    from factor_service.db.seed import seed_all
    with _db_base.SessionLocal() as db:
        seed_all(db)

    from fastapi.testclient import TestClient
    from factor_service.main import create_app
    return TestClient(create_app())


H = {"X-Forwarded-User": "tester", "X-Forwarded-Role": "admin", "X-Trace-Id": "t_test"}


def _drain(brun_id: str):
    from factor_service.db import base as _db_base
    from factor_service.baskets import orchestrator
    from factor_service.services.run_executor import execute_task
    from factor_service.db.models import Task

    for _ in range(60):
        with _db_base.SessionLocal() as db:
            t = (db.query(Task).filter(Task.brun_id == brun_id, Task.status == "queued",
                                       Task.gate == "ready").order_by(Task.seq_no).first())
            tid = t.task_id if t else None
        if tid is None:
            with _db_base.SessionLocal() as db:
                orchestrator.advance(db, brun_id)
            with _db_base.SessionLocal() as db:
                t = (db.query(Task).filter(Task.brun_id == brun_id, Task.status == "queued",
                                           Task.gate == "ready").first())
                tid = t.task_id if t else None
            if tid is None:
                break
        res = execute_task(tid)
        with _db_base.SessionLocal() as db:
            tk = db.get(Task, tid)
            rs = res.get("status")
            tk.status = "success" if rs in ("success", "degraded") else (rs or "failed")
            tk.finished_at = dt.datetime.utcnow()
            db.commit()
            orchestrator.advance(db, brun_id)


def test_basket_full_flow(setup):
    client = setup
    shared = {
        "universe_id": "preset:all",
        "window": {"mode": "fixed", "start": "2024-01-02", "end": "2024-03-25"},
        "rebalance": "W", "horizons": [5, 10], "primary_horizon": 5, "ic_type": "rank",
        "return_price": "close", "winsorize": "mad", "winsorize_n": 5.0,
        "standardize": "zscore", "neutralize_size": False, "neutralize_industry": False,
        "quantiles": 5, "layer_weight": "equal", "cost_bps": 30.0,
    }
    body = {
        "name": "动量篮(测试)", "shared_config": shared, "run_now": False,
        "correlation_enabled": True, "correlation_threshold": 0.7, "corr_min_periods": 3,
        "items": [
            {"factor_id": "mom", "params": {"window": 20}, "label": "动量·20d"},
            {"factor_id": "mom", "params": {"window": 40}, "label": "动量·40d"},
            {"factor_id": "reversal", "params": {"window": 5}, "label": "反转·5d"},
            {"factor_id": "volatility", "params": {"window": 20}, "label": "波动·20d"},
        ],
    }
    r = client.post("/baskets", json=body, headers=H)
    assert r.status_code == 200, r.text
    bid = r.json()["basket_id"]
    assert r.json()["entry_count"] == 4

    r = client.post(f"/baskets/{bid}/runs", json={"mode": "full"}, headers=H)
    assert r.status_code == 200, r.text
    brun_id = r.json()["brun_id"]
    assert r.json()["total_children"] == 5  # 4 evals + 1 corr

    _drain(brun_id)

    run = client.get(f"/baskets/{bid}/runs/{brun_id}", headers=H).json()
    assert run["status_raw"] == "success", run
    sid = run["snapshot_id"]
    assert sid

    # snapshot has all 4 item summaries + stats
    snap = client.get(f"/baskets/{bid}/snapshots/{sid}", headers=H).json()
    assert len(snap["item_summaries"]) == 4
    assert snap["basket_stats"]["ok_count"] == 4

    # grid scores rows; collapse=best dedups the two mom variants
    grid = client.get(f"/baskets/{bid}/snapshots/{sid}/grid", headers=H).json()
    assert len(grid["rows"]) == 3
    assert all("score" in r for r in grid["rows"])

    # correlation computed
    corr = client.get(f"/baskets/{bid}/snapshots/{sid}/correlation", headers=H).json()
    assert "matrix" in corr and corr["order"]

    # selection: prune keeps low-correlation set
    crit = {"weights": "preset:spre_builtin_ic_quality", "collapse": "all",
            "order_mode": "score", "threshold": 0.7}
    r = client.post("/selections", json={"basket_id": bid, "snapshot_id": sid,
                                         "name": "测试精选", "criteria": crit}, headers=H)
    assert r.status_code == 200, r.text
    sel = r.json()
    assert len(sel["members"]) >= 2
    selid = sel["selection_id"]

    # finalize → export carries the full source window/config
    assert client.post(f"/selections/{selid}/finalize", json={}, headers=H).json()["status_raw"] == "final"
    exp = client.get(f"/selections/{selid}/export", headers=H).json()
    assert exp["source"]["window"] == "fixed:2024-01-02:2024-03-25"
    assert exp["source"]["shared_config"]["universe_id"] == "preset:all"


def test_keys_invalidation_matrix(setup):
    """§9.2: changing a common field → full; changing one item's params → items."""
    from factor_service.baskets import keys, revisions
    from factor_service.db import base as _db_base
    from factor_service.db.models import Basket, BasketRevision

    base_shared = {"universe_id": "preset:hs300",
                   "window": {"mode": "rolling", "span": "365d"}, "rebalance": "W",
                   "horizons": [5], "primary_horizon": 5}
    items = [{"item_id": "bit_x", "factor_id": "mom", "version": "v1", "params": {"window": 20}}]
    with _db_base.SessionLocal() as db:
        b = Basket(basket_id="bsk_inv_test", name="inv", active_revision=0)
        db.add(b)
        db.commit()
        revisions.save_revision(db, b, shared_config=base_shared, items=items,
                                reason="init", author="t")
        # change a common field → full
        s2 = dict(base_shared, rebalance="M")
        rev = revisions.save_revision(db, b, shared_config=s2, items=items,
                                      reason="freq", author="t")
        assert rev.invalidation == "full"
        # change one item's params → items:[...]
        items2 = [{"item_id": "bit_x", "factor_id": "mom", "version": "v1",
                   "params": {"window": 60}}]
        rev3 = revisions.save_revision(db, b, shared_config=s2, items=items2,
                                       reason="param", author="t")
        assert rev3.invalidation.startswith("items:")
