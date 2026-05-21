"""Integration tests for the quant_web FastAPI layer (doc §5).

Sets QUANT_DATA_ROOT / AUDIT_DB_URL to temp locations *before* importing
quant_web (the DB engine is created at import time), seeds the demo world, and
exercises auth, RBAC, the data routes, charts, the migration dry-run, and the
501 stubs through TestClient.
"""
from __future__ import annotations

import os
import tempfile

import pytest

_DATA_ROOT = tempfile.mkdtemp(prefix="zjqweb_data_")
_DB_PATH = os.path.join(tempfile.mkdtemp(prefix="zjqweb_db_"), "audit.db")
os.environ["QUANT_DATA_ROOT"] = _DATA_ROOT
os.environ["AUDIT_DB_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["SEED_DEFAULT_USERS"] = "true"

from fastapi.testclient import TestClient  # noqa: E402

from quant_web.main import create_app  # noqa: E402
from quant_web.seed_demo import seed  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client():
    seed(_DATA_ROOT)
    return TestClient(create_app())


def _token(client, username, password):
    r = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


@pytest.fixture(scope="module")
def admin(client):
    return {"Authorization": f"Bearer {_token(client, 'admin', 'admin123')}"}


@pytest.fixture(scope="module")
def viewer(client):
    return {"Authorization": f"Bearer {_token(client, 'viewer', 'viewer123')}"}


def test_health(client):
    assert client.get("/api/v1/health").json()["status"] == "ok"


def test_login_bad_password(client):
    r = client.post("/api/v1/auth/login", json={"username": "admin", "password": "nope"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "UNAUTHENTICATED"


def test_requires_auth(client):
    assert client.get("/api/v1/data/dashboard/summary").status_code == 401


def test_dashboard_summary(client, admin):
    d = client.get("/api/v1/data/dashboard/summary", headers=admin).json()
    assert d["kpis"]["batches_today"]["total"] >= 1
    assert len(d["tables"]) == 8
    assert {h["type"] for h in d["hotspots"]} == {"stale_running", "stale_extras", "vacuumable"}


def test_batches_list_and_detail(client, admin):
    lst = client.get("/api/v1/data/batches", headers=admin, params={"page_size": 100}).json()
    assert lst["total"] >= 1
    assert {"failed", "running", "invalidated", "success"} & {b["status"] for b in lst["items"]}
    inv = next(b for b in lst["items"] if b["status"] == "invalidated")
    assert inv["superseded_by"]
    lin = client.get(f"/api/v1/data/batches/{inv['batch_id']}/lineage", headers=admin).json()
    assert lin["current_node_id"]


def test_invalidate_rbac(client, admin, viewer):
    bid = client.get("/api/v1/data/batches", headers=admin,
                     params={"status": "success", "page_size": 1}).json()["items"][0]["batch_id"]
    # viewer is denied
    r = client.post(f"/api/v1/data/batches/{bid}/invalidate", headers=viewer,
                    json={"reason": "x" * 12})
    assert r.status_code == 403 and r.json()["error"]["code"] == "PERMISSION_DENIED"
    # short reason rejected
    r2 = client.post(f"/api/v1/data/batches/{bid}/invalidate", headers=admin, json={"reason": "no"})
    assert r2.status_code == 400


def test_tables_rows_and_lineage(client, admin):
    assert len(client.get("/api/v1/data/tables", headers=admin).json()["items"]) == 8
    rows = client.get("/api/v1/data/tables/raw.bars_daily/rows", headers=admin,
                      params={"symbols": "600519.SH", "limit": 5}).json()
    assert rows["items"], "expected at least one row for 600519.SH"
    assert any(c["name"] == "close" for c in rows["columns"]), "close column missing"
    last_date = rows["items"][-1]["date"]
    lin = client.get("/api/v1/data/tables/raw.bars_daily/rows/lineage", headers=admin,
                     params={"symbol": "600519.SH", "date": last_date}).json()
    assert "history" in lin and "diff_columns" in lin


def test_full_view_has_status_column(client, admin):
    fv = client.get("/api/v1/data/tables/raw.bars_daily/rows/all", headers=admin,
                    params={"symbols": "600519.SH"}).json()
    assert any(c["name"] == "_status" for c in fv["columns"])


def test_fundamentals_requires_as_of(client, admin):
    r = client.get("/api/v1/data/tables/raw.fundamentals/rows", headers=admin)
    assert r.status_code == 400 and r.json()["error"]["code"] == "MISSING_PARAMETER"


def test_extras_keys(client, admin):
    keys = client.get("/api/v1/data/tables/raw.fundamentals/extras-keys", headers=admin).json()["keys"]
    assert "roe" in keys


def test_fields_and_migrate_dry_run(client, admin):
    fields = client.get("/api/v1/data/fields", headers=admin,
                        params={"table": "raw.fundamentals"}).json()
    assert fields["total"] >= 1
    # gross_margin was promoted by the seed -> a valid migrate dry-run target
    dr = client.post("/api/v1/data/fields/raw.fundamentals/gross_margin/migrate",
                     headers=admin, json={"dry_run": True}).json()
    assert dr["affected_files"] >= 1 and "after_schema" in dr


def test_kline_and_marks(client, admin):
    k = client.get("/api/v1/data/charts/kline", headers=admin,
                   params={"symbol": "600519.SH", "frequency": "1d", "count": 60}).json()
    assert k["candles"] and "_batch_id" in k["candles"][0]
    marks = client.get("/api/v1/data/charts/kline/marks", headers=admin,
                       params={"symbol": "600519.SH"}).json()
    assert "corporate_actions" in marks and "batch_revisions" in marks


def test_fundamentals_chart(client, admin):
    fd = client.get("/api/v1/data/charts/fundamentals", headers=admin, params={
        "symbols": "600519.SH", "metrics": "revenue,roe", "as_of_date": "2026-05-21",
        "period_type": "annual", "derivatives": "yoy"}).json()
    assert fd["series"]


def test_symbol_search(client, admin):
    items = client.get("/api/v1/data/charts/symbols/search", headers=admin,
                       params={"q": "茅台"}).json()["items"]
    assert any(i["symbol"] == "600519.SH" for i in items)


def test_quality_issues(client, admin):
    q = client.get("/api/v1/data/quality/issues", headers=admin).json()
    assert q["total"] >= 1


def test_maintenance_requires_admin(client, viewer):
    r = client.post("/api/v1/data/maintenance/vacuum/preview", headers=viewer,
                    json={"older_than_days": 90})
    assert r.status_code == 403


def test_stub_501(client, admin):
    for path in ("/api/v1/factors", "/api/v1/strategies/backtests", "/api/v1/monitor/alerts"):
        r = client.get(path, headers=admin)
        assert r.status_code == 501 and r.json()["error"]["code"] == "NOT_IMPLEMENTED"
