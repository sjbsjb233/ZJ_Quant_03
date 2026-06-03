"""Integration tests for the quant_web FastAPI layer (doc §5).

Sets QUANT_DATA_ROOT / AUDIT_DB_URL to temp locations *before* importing
quant_web (the DB engine is created at import time), seeds the demo world, and
exercises auth, RBAC, the data routes, charts, the migration dry-run, and the
501 stubs through TestClient.
"""
from __future__ import annotations

import datetime as dt
import os
import tempfile

import pytest

_DATA_ROOT = tempfile.mkdtemp(prefix="zjqweb_data_")
_DB_PATH = os.path.join(tempfile.mkdtemp(prefix="zjqweb_db_"), "audit.db")
os.environ["QUANT_DATA_ROOT"] = _DATA_ROOT
os.environ["AUDIT_DB_URL"] = f"sqlite:///{_DB_PATH}"
os.environ["SEED_DEFAULT_USERS"] = "true"
# enable the service log-ingest endpoint (容器化重构 V3 §6.7)
os.environ["LOG_INGEST_TOKEN"] = "test-log-token"

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


@pytest.fixture(scope="module")
def editor(client):
    return {"Authorization": f"Bearer {_token(client, 'editor', 'editor123')}"}


def test_health(client):
    assert client.get("/api/v1/health").json()["status"] == "ok"


def test_health_liveness_fields(client):
    """Lightweight /health stays a pure liveness probe + carries env/cluster
    for the status bar / topbar (修复 A1 §3.2)."""
    h = client.get("/api/v1/health").json()
    assert h["service"] == "web-backend"
    # defaults when ENVIRONMENT/CLUSTER are unset in the test env
    assert h["environment"] == "DEV"
    assert h["cluster"] == "local"


def test_health_deps_requires_auth(client):
    # the aggregate view is login-gated (修复 A1 §3.3)
    assert client.get("/api/v1/health/deps").status_code == 401


def test_health_deps_shape(client, admin):
    """Aggregate view: local db/warehouse ok; keeper absent when KEEPER_BASE is
    unset (rollback/test); env+cluster echoed back (修复 A1 §3.3)."""
    body = client.get("/api/v1/health/deps", headers=admin).json()
    comp = body["components"]
    assert comp["db"]["state"] == "ok"
    assert comp["warehouse"]["state"] == "ok"      # tmp QUANT_DATA_ROOT exists + readable
    assert "keeper" not in comp                     # KEEPER_BASE unset → not probed/shown
    assert body["environment"] == "DEV" and body["cluster"] == "local"
    assert body["status"] in ("ok", "error")        # db+warehouse critical (keeper absent)


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


def test_schema_capability_contract_raw(client, admin):
    """raw.bars_daily schema declares batch lineage + symbols/date_range/adjust
    filters (方案 §2.1)."""
    s = client.get("/api/v1/data/tables/raw.bars_daily/schema", headers=admin).json()
    assert s["mutable"] is False
    assert s["supports_lineage"] is True and s["lineage_kind"] == "batch"
    assert s["has_symbol"] is True and s["time_col"] == "date"
    assert s["require_filter"] is True
    kinds = {f["kind"] for f in s["filters"]}
    assert {"symbols", "date_range", "adjust"} <= kinds


def test_schema_capability_contract_reference_sectors(client, admin):
    """reference.sectors: mutable, no batch lineage, no symbols/date filters --
    only classification_system (enum) + level (int) (方案 §2.2)."""
    s = client.get("/api/v1/data/tables/reference.sectors/schema", headers=admin).json()
    assert s["mutable"] is True
    assert s["supports_lineage"] is False and s["lineage_kind"] == "reference"
    assert s["has_symbol"] is False and s["time_col"] is None
    assert s["require_filter"] is False
    keys = {f["key"] for f in s["filters"]}
    assert keys == {"classification_system", "level"}
    assert "symbols" not in keys and "date" not in keys
    enum_f = next(f for f in s["filters"] if f["kind"] == "enum")
    assert enum_f["options_endpoint"] == "/tables/reference.sectors/enum/classification_system"


def test_reference_symbols_lineage_no_longer_500(client, admin):
    """Regression for 方案 §0.3: reference.symbols row lineage used to raise a
    DuckDB binder error (_batch_id missing) -> INTERNAL_ERROR 500. It must now
    return a clean 200 provenance payload."""
    r = client.get("/api/v1/data/tables/reference.symbols/rows/lineage", headers=admin,
                   params={"symbol": "600519.SH"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lineage_kind"] == "reference" and body["mutable"] is True
    assert body["history"] == []
    assert body["current"] is not None
    assert body["current"]["values"]["name"] == "贵州茅台"
    assert body["current"]["source"] is not None


def test_reference_sectors_lineage_provenance(client, admin):
    r = client.get("/api/v1/data/tables/reference.sectors/rows/lineage", headers=admin,
                   params={"code": "801120", "classification_system": "sw"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lineage_kind"] == "reference"
    assert body["current"]["values"]["name"] == "食品饮料"


def test_enum_values_whitelist_blocks_symbol(client, admin):
    """enum endpoint must reject a high-cardinality column like symbol (方案 §4.2):
    the symbol dimension goes through typeahead, never a full-table DISTINCT."""
    r = client.get("/api/v1/data/tables/raw.bars_daily/enum/symbol", headers=admin)
    assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_PARAMETER"


def test_enum_values_returns_distinct(client, admin):
    r = client.get("/api/v1/data/tables/reference.symbols/enum/exchange", headers=admin)
    assert r.status_code == 200
    vals = r.json()["values"]
    assert "SH" in vals and "SZ" in vals
    # reference.sectors classification_system enum
    cs = client.get("/api/v1/data/tables/reference.sectors/enum/classification_system",
                    headers=admin).json()["values"]
    assert "sw" in cs


def test_enum_values_unknown_field_400(client, admin):
    r = client.get("/api/v1/data/tables/reference.symbols/enum/nope", headers=admin)
    assert r.status_code == 400


def test_full_view_has_status_column(client, admin):
    fv = client.get("/api/v1/data/tables/raw.bars_daily/rows/all", headers=admin,
                    params={"symbols": "600519.SH"}).json()
    assert any(c["name"] == "_status" for c in fv["columns"])


def test_rows_requires_filter_for_large_tables(client, admin):
    """Browsing bars_daily / bars_minute / universe without symbols or a date
    window must 400 -- not silently scan the whole table (the 2026-05-23 OOM)."""
    for name in ("raw.bars_daily", "raw.bars_minute", "raw.universe"):
        r = client.get(f"/api/v1/data/tables/{name}/rows", headers=admin)
        assert r.status_code == 400, name
        assert r.json()["error"]["code"] == "MISSING_PARAMETER"


def test_rows_pagination_total_and_concat(client, admin):
    """SQL-pushdown pagination must return correct total and stable order:
    page 1 ++ page 2 of size N == page 1 of size 2N."""
    full = client.get("/api/v1/data/tables/raw.bars_daily/rows", headers=admin,
                     params={"symbols": "600519.SH", "limit": 10, "offset": 0}).json()
    assert full["total"] >= 5
    assert full["page_size"] == 10 and full["page"] == 1

    half = full["total"] // 2 if full["total"] >= 4 else 2
    p1 = client.get("/api/v1/data/tables/raw.bars_daily/rows", headers=admin,
                    params={"symbols": "600519.SH", "limit": half, "offset": 0}).json()
    p2 = client.get("/api/v1/data/tables/raw.bars_daily/rows", headers=admin,
                    params={"symbols": "600519.SH", "limit": half, "offset": half}).json()
    big = client.get("/api/v1/data/tables/raw.bars_daily/rows", headers=admin,
                     params={"symbols": "600519.SH", "limit": half * 2, "offset": 0}).json()
    assert p1["total"] == p2["total"] == big["total"]
    assert (p1["items"] + p2["items"]) == big["items"]


def test_rows_source_post_filter_does_not_resurrect_loser(client, admin):
    """seed_demo writes akshare's full year of 600519 bars, plus on the most
    recent 5 days both a 'bad' akshare batch AND a 'good' tushare_pro batch.
    tushare_pro is registered with priority 200 vs akshare 100, so it WINS
    those overlapping days and akshare wins everything before.

    The post_filter-after-dedup invariant says: asking ``source=akshare`` for
    the days tushare won must return zero rows for those dates, because
    akshare's row for them lost the dedup rank and must not resurface. If
    post_filter were applied BEFORE ``WHERE rn = 1`` the losing akshare row
    would silently come back (the bug this test pins shut)."""
    full = client.get(
        "/api/v1/data/tables/raw.bars_daily/rows", headers=admin,
        params={"symbols": "600519.SH", "limit": 1000},
    ).json()
    by_source: dict[str, set[str]] = {}
    for r in full["items"]:
        by_source.setdefault(r["_source"], set()).add(r["date"])
    tushare_dates = by_source.get("tushare_pro", set())
    akshare_dates = by_source.get("akshare", set())
    assert tushare_dates, "seed expected tushare_pro to win on the recent 5 days"
    assert akshare_dates, "seed expected akshare to win on earlier days"
    assert not (tushare_dates & akshare_dates), \
        "each date has exactly one winner in the current view"

    # Ask source=akshare for the date window tushare won -> none of those
    # dates may come back, because for them akshare lost the dedup rank.
    days = sorted(tushare_dates)
    akshare_only = client.get(
        "/api/v1/data/tables/raw.bars_daily/rows", headers=admin,
        params={"symbols": "600519.SH", "source": "akshare",
                "start": days[0], "end": days[-1], "limit": 1000},
    ).json()
    returned_dates = {row["date"] for row in akshare_only["items"]}
    assert not (returned_dates & tushare_dates), (
        "akshare rows resurfaced for dates tushare won — "
        "post_filter was applied before dedup"
    )
    # Every row that DID come back must indeed be sourced from akshare.
    for r in akshare_only["items"]:
        assert r["_source"] == "akshare"


def test_rows_filter_rejects_empty_symbols_value(client, admin):
    """A non-empty-but-effectively-empty ``symbols`` value (``symbols=,`` or
    ``symbols= , ``) used to bypass the large-table filter guard because
    bool(',') is truthy yet _split_filters produces no SQL predicate -- the
    request then scanned the entire table (the original OOM vector). The
    guard now uses the same parser as _split_filters."""
    for bypass in (",", " ,", " , ,  ,"):
        r = client.get("/api/v1/data/tables/raw.bars_daily/rows", headers=admin,
                       params={"symbols": bypass})
        assert r.status_code == 400, bypass
        assert r.json()["error"]["code"] == "MISSING_PARAMETER"


def test_batch_rows_pagination(client, admin):
    """batch_rows pushes limit/offset into SQL and returns the real total."""
    bid = client.get("/api/v1/data/batches", headers=admin,
                     params={"status": "success", "page_size": 1}).json()["items"][0]["batch_id"]
    p1 = client.get(f"/api/v1/data/batches/{bid}/rows", headers=admin,
                    params={"limit": 3, "offset": 0}).json()
    p2 = client.get(f"/api/v1/data/batches/{bid}/rows", headers=admin,
                    params={"limit": 3, "offset": 3}).json()
    assert p1["total"] == p2["total"] >= 1
    assert p1["page_size"] == 3
    # When the batch has more than 6 rows, the two pages must be disjoint.
    # Compare by primary key only -- batch rows include _extras which may be a
    # dict and so isn't hashable as part of a set element.
    def _pk_set(items):
        return {tuple((r.get(c) for c in ("date", "symbol", "datetime", "report_period")))
                for r in items}
    if p1["total"] >= 6:
        assert len(p1["items"]) == 3 and len(p2["items"]) == 3
        assert not (_pk_set(p1["items"]) & _pk_set(p2["items"]))


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


def test_kline_with_before_cursor(client, admin):
    # full window first, then a `before` cursor must only return earlier candles
    full = client.get("/api/v1/data/charts/kline", headers=admin,
                      params={"symbol": "600519.SH", "frequency": "1d"}).json()
    assert full["candles"]
    mid = full["candles"][len(full["candles"]) // 2]["timestamp"]
    cutoff = dt.datetime.utcfromtimestamp(mid / 1000).date()
    r = client.get("/api/v1/data/charts/kline", headers=admin, params={
        "symbol": "600519.SH", "frequency": "1d",
        "before": cutoff.isoformat(), "count": 20,
    }).json()
    cutoff_ms = int((dt.datetime(cutoff.year, cutoff.month, cutoff.day)
                     - dt.datetime(1970, 1, 1)).total_seconds() * 1000)
    assert r["candles"]
    assert all(c["timestamp"] < cutoff_ms for c in r["candles"])
    assert len(r["candles"]) <= 20


def test_kline_view_all(client, admin):
    r = client.get("/api/v1/data/charts/kline", headers=admin, params={
        "symbol": "600519.SH", "frequency": "1d", "view": "all",
    }).json()
    assert r["candles"]  # includes rows from non-current batches


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
    # /factors/* is now proxied to factor-api (see test_factors_proxy_*);
    # /monitor/* is now the real 监控 / 外部观察 layer (see test_monitor_*).
    # 策略管理 remains a 501 stub this period.
    r = client.get("/api/v1/strategies/backtests", headers=admin)
    assert r.status_code == 501 and r.json()["error"]["code"] == "NOT_IMPLEMENTED"


def test_factors_proxy_attempts_upstream(client, admin, monkeypatch):
    """Without a running factor-api, the proxy returns a clean INTERNAL error
    wrapped in our standard envelope (not raw 500/503)."""
    monkeypatch.setenv("FACTOR_API_BASE", "http://127.0.0.1:1")  # unreachable
    r = client.get("/api/v1/factors", headers=admin)
    assert r.status_code in (400, 500, 502, 503)
    body = r.json()
    assert "error" in body and body["error"]["code"] in (
        "INTERNAL",
        "PROXY_ERROR",
    )
    assert body["error"]["trace_id"]


# ===========================================================================
# Logging platform (全链路日志方案 v2.0). Shares the module-scoped client/db so
# we never re-bind the import-time engine.
# ===========================================================================
def _flush_logs(client):
    client.app.state.app_log_store.flush()


def test_trace_id_echoed_when_supplied(client, admin):
    tid = "t_provided00000001"
    r = client.get("/api/v1/data/tables", headers={**admin, "X-Trace-Id": tid})
    assert r.headers.get("x-trace-id") == tid


def test_trace_id_generated_when_absent(client, admin):
    r = client.get("/api/v1/data/tables", headers=admin)
    gen = r.headers.get("x-trace-id")
    assert gen and gen.startswith("t_") and len(gen) == 18


def test_trace_spans_backend_and_data_layer(client, admin):
    """One trace_id stitches the http request log to the quant_data reader log
    (proves the contextvar reaches the threadpool + data layer; doc §G1)."""
    tid = "t_chain0000000001"
    client.get("/api/v1/data/tables/raw.bars_daily/rows", headers={**admin, "X-Trace-Id": tid},
               params={"symbols": "600519.SH", "limit": 3})
    _flush_logs(client)
    rows = client.get("/api/v1/logs", headers=admin,
                      params={"trace_id": tid, "page_size": 100}).json()["items"]
    sources = {r["source"] for r in rows}
    assert "backend" in sources, rows
    assert "data" in sources, "data-layer log did not inherit the request trace_id"
    assert any(r["event"] == "reader.query.executed" for r in rows)
    assert any(r["event"] == "http.request.completed" for r in rows)


def test_login_success_and_failure_logged(client):
    client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123"})
    client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong"})
    _flush_logs(client)
    admin_hdr = {"Authorization": f"Bearer {_token(client, 'admin', 'admin123')}"}
    ok = client.get("/api/v1/logs", headers=admin_hdr,
                    params={"event": "auth.login.success", "page_size": 5}).json()
    bad = client.get("/api/v1/logs", headers=admin_hdr,
                     params={"event": "auth.login.failed", "page_size": 5}).json()
    assert ok["total"] >= 1
    assert bad["total"] >= 1 and bad["items"][0]["level"] == "WARN"


def test_client_log_ingest_redacts_and_overrides_actor(client, viewer, admin):
    body = {"records": [{
        "level": "ERROR", "event": "react.boundary", "logger": "fe.boundary",
        "session_id": "s_unit01", "route": "/data/viz/kline", "message": "boom",
        "actor": "i-am-lying", "source": "backend",     # both must be ignored
        "extra": {"password": "hunter2", "note": "ok"}, "stack": "at x",
    }]}
    res = client.post("/api/v1/logs/client", headers=viewer, json=body).json()
    assert res["accepted"] == 1
    _flush_logs(client)
    rows = client.get("/api/v1/logs", headers=admin,
                      params={"session_id": "s_unit01"}).json()["items"]
    assert rows, "frontend log not stored"
    row = rows[0]
    assert row["source"] == "frontend"          # server-forced, not "backend"
    assert row["actor"] == "viewer"             # server-forced, not "i-am-lying"
    assert row["extra"]["password"] == "***"    # redacted (doc §7.6)
    assert row["extra"]["note"] == "ok"


def test_logs_layered_access_viewer_frontend_only(client, viewer, admin):
    """Layered log visibility (V3 §4.2): a viewer may enter the log centre but
    only ever sees the ``frontend`` layer — every other source is a 403, even
    via ?source=, /export, /summary and the trace detail."""
    # seed at least one frontend row so the positive path is non-trivial
    client.post("/api/v1/logs/client", headers=viewer, json={"records": [
        {"level": "INFO", "event": "frontend.event", "logger": "fe.t",
         "session_id": "s_layered01", "message": "hi"}]})
    _flush_logs(client)

    # viewer can now query (no longer admin-only) but is narrowed to frontend
    r = client.get("/api/v1/logs", headers=viewer, params={"page_size": 200})
    assert r.status_code == 200, r.text
    assert all(row["source"] == "frontend" for row in r.json()["items"]), \
        "viewer leaked a non-frontend source"

    # explicitly asking for the frontend layer is fine
    assert client.get("/api/v1/logs", headers=viewer,
                      params={"source": "frontend"}).status_code == 200
    # asking for a forbidden layer is a hard 403 (防越权)
    for forbidden in ("data", "factor", "backend", "data,factor"):
        rr = client.get("/api/v1/logs", headers=viewer, params={"source": forbidden})
        assert rr.status_code == 403, f"{forbidden}: {rr.status_code}"
    # export can't exfiltrate a forbidden layer either
    assert client.get("/api/v1/logs/export", headers=viewer,
                      params={"source": "data"}).status_code == 403
    # summary is allowed and only counts the visible layer
    assert client.get("/api/v1/logs/summary", headers=viewer).status_code == 200


def test_logs_layered_access_editor_sees_data_and_factor(client, editor):
    """An editor sees data / factor / backend / frontend (V3 §4.2 default map)."""
    for src in ("data", "factor", "backend", "frontend", "data,factor"):
        r = client.get("/api/v1/logs", headers=editor, params={"source": src})
        assert r.status_code == 200, f"{src}: {r.text}"
    assert client.get("/api/v1/logs/summary", headers=editor).status_code == 200
    assert client.get("/api/v1/logs/export", headers=editor,
                      params={"source": "factor", "limit": 5}).status_code == 200


def test_logs_layered_access_admin_unrestricted(client, admin):
    """Admin keeps the full cross-layer view (every source, no narrowing)."""
    for src in ("data", "factor", "backend", "frontend"):
        assert client.get("/api/v1/logs", headers=admin,
                          params={"source": src}).status_code == 200
    # no source filter → all layers visible to admin
    items = client.get("/api/v1/logs", headers=admin, params={"page_size": 500}).json()["items"]
    assert {r["source"] for r in items} - {"data", "factor", "backend", "frontend"} == set()


def test_log_source_scoping_helpers_unit():
    """Pure-unit coverage of the source-narrowing logic (V3 §4.2), incl. the
    no-access edge that the three demo roles can't exercise."""
    from types import SimpleNamespace

    from quant_web import deps
    from quant_web.config import Settings
    from quant_web.errors import ApiError
    from quant_web.logging.routes import _scoped_source

    cfg = Settings()
    # editor now also sees the "monitor" layer (可观测层重构方案 §6.1); viewer never does.
    assert cfg.log_sources_by_role == {
        "editor": {"data", "factor", "backend", "frontend", "monitor"},
        "viewer": {"frontend"},
    }

    admin = SimpleNamespace(role="admin")
    editor = SimpleNamespace(role="editor")
    viewer = SimpleNamespace(role="viewer")
    nobody = SimpleNamespace(role="ghost")          # role absent from the map

    assert deps.allowed_sources_for(admin) == deps.ALL_SOURCES
    assert deps.allowed_sources_for(viewer) == {"frontend"}
    assert deps.allowed_sources_for(nobody) == set()

    # admin: pass through unchanged (None stays None = no filter, future-proof)
    assert _scoped_source(None, admin) is None
    assert _scoped_source("data", admin) == "data"
    # viewer: None → only-allowed; forbidden request → 403; allowed request → kept
    assert _scoped_source(None, viewer) == "frontend"
    assert _scoped_source("frontend", viewer) == "frontend"
    for forbidden in ("data", "factor", "backend"):
        with pytest.raises(ApiError):
            _scoped_source(forbidden, viewer)
    # mixed request keeps only the allowed intersection
    assert _scoped_source("data,frontend", viewer) == "frontend"
    # editor intersection sorts deterministically
    assert _scoped_source("factor,data", editor) == "data,factor"


def test_logs_summary_shape(client, admin):
    _flush_logs(client)
    s = client.get("/api/v1/logs/summary", headers=admin).json()
    assert set(s["kpis"]) == {"today_total", "error", "warn", "slow", "5xx", "frontend_crash"}
    assert s["health"]["db_sink"] == "ok"
    assert isinstance(s["timeline_24h"], list)


def test_trace_detail_joins_audit_and_logs(client, admin):
    """An invalidate writes both an audit_log row and app_log lines under one
    trace_id; the trace endpoint returns them together (doc §8.7)."""
    bid = client.get("/api/v1/data/batches", headers=admin,
                     params={"status": "success", "page_size": 1}).json()["items"][0]["batch_id"]
    tid = "t_traceaudit00001"
    client.post(f"/api/v1/data/batches/{bid}/invalidate",
                headers={**admin, "X-Trace-Id": tid}, json={"reason": "logging-test-reason"})
    _flush_logs(client)
    detail = client.get(f"/api/v1/logs/trace/{tid}", headers=admin).json()
    assert detail["summary"]["total"] >= 1
    assert any(a["action"] == "batch.invalidate" for a in detail["audit"])
    assert any(log["event"] == "audit.batch.invalidate" for log in detail["logs"])


def test_export_jsonl(client, admin):
    _flush_logs(client)
    r = client.get("/api/v1/logs/export", headers=admin, params={"format": "jsonl", "limit": 10})
    assert r.status_code == 200
    assert "application/x-ndjson" in r.headers["content-type"]


def test_logs_ingest_requires_service_token(client):
    """The service log-ingest endpoint rejects a missing/wrong token (it is a
    service token, never a user JWT) — V3 §6.7."""
    body = {"records": [{"level": "INFO", "event": "x", "logger": "t", "source": "data"}]}
    assert client.post("/api/v1/logs/ingest", json=body).status_code == 401
    assert client.post("/api/v1/logs/ingest", json=body,
                       headers={"X-Log-Token": "wrong"}).status_code == 401


def test_logs_ingest_accepts_and_coerces_source(client, admin):
    """A trusted service batch lands in the same app_log ledger; a spoofed
    source is coerced into the allowed set and host is preserved (V3 §6.6/§6.7)."""
    tid = "t_ingest000000001"
    body = {"records": [
        {"level": "INFO", "event": "keeper.write.done", "logger": "quant_data.ingest",
         "source": "data", "trace_id": tid, "host": "zj-quant-data-keeper",
         "duration_ms": 12},
        {"level": "ERROR", "event": "task.failed", "logger": "factor.dispatcher",
         "source": "frontend",        # spoofed -> coerced to "data"
         "trace_id": tid, "host": "zj-quant-factor-worker"},
    ]}
    res = client.post("/api/v1/logs/ingest", json=body,
                      headers={"X-Log-Token": "test-log-token"}).json()
    assert res["accepted"] == 2 and res["received"] == 2
    _flush_logs(client)
    rows = client.get("/api/v1/logs", headers=admin,
                      params={"trace_id": tid, "page_size": 50}).json()["items"]
    by_event = {r["event"]: r for r in rows}
    assert by_event["keeper.write.done"]["source"] == "data"
    assert by_event["keeper.write.done"]["host"] == "zj-quant-data-keeper"
    # spoofed "frontend" was coerced to an allowed service source
    assert by_event["task.failed"]["source"] == "data"


def test_app_log_store_overflow_keeps_warn_and_self_reports(tmp_path):
    """Saturating the queue drops DEBUG/INFO but keeps WARN+ to file and emits a
    single CRITICAL log.queue.dropped (doc §7.2 back-pressure)."""
    from quant_web.config import Settings
    from quant_web.logging.store import AppLogStore

    settings = Settings(log_to_db=False, log_to_file=True, log_file_dir=str(tmp_path))
    store = AppLogStore(None, settings, queue_max=1)  # no .start() -> queue fills
    store.enqueue({"level": "INFO", "event": "a", "logger": "t", "source": "backend"})
    store.enqueue({"level": "INFO", "event": "b", "logger": "t", "source": "backend"})  # dropped
    store.enqueue({"level": "ERROR", "event": "kept.err", "logger": "t", "source": "backend"})
    assert store._dropped >= 2
    today = __import__("datetime").date.today().isoformat()
    content = (tmp_path / f"quant_web-{today}.log").read_text()
    assert "log.queue.dropped" in content       # self-report written to file
    assert "kept.err" in content                # WARN+ bypassed the full queue


# ===========================================================================
# 监控 / 外部观察层 (可观测层重构方案). The evaluator thread is inert under a
# short-lived TestClient (first tick is delayed a full interval), so we drive
# ``_tick()`` directly where we need detection to run.
# ===========================================================================
def test_monitor_health_shape(client, viewer):
    r = client.get("/api/v1/monitor/health", headers=viewer)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded", "down")
    assert set(body["layers"]) == {"frontend", "backend", "data", "factor", "monitor"}
    assert "firing" in body["alerts"]


def test_monitor_rules_list_and_admin_override(client, admin, editor, viewer):
    # log-access roles may read the rule table; viewer has ≥1 source so it's allowed.
    r = client.get("/api/v1/monitor/rules", headers=viewer)
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 12
    ids = {it["id"] for it in items}
    assert {"D1.oom_killed", "D2.restart_storm", "D7.deadman", "D12.sentinel_offline"} <= ids

    # admin overrides a threshold → persisted + reflected
    up = client.put("/api/v1/monitor/rules/D2.restart_storm",
                    json={"params": {"count": 7}, "enabled": True}, headers=admin)
    assert up.status_code == 200
    d2 = up.json()
    assert any(p["key"] == "count" and p["value"] == 7 for p in d2["params"])

    # non-admin cannot edit rules
    assert client.put("/api/v1/monitor/rules/D2.restart_storm",
                      json={"params": {"count": 3}}, headers=editor).status_code == 403

    # restore the default so the persisted override doesn't leak into other tests
    # (the evaluator now honors DB overrides, so a stale count=7 would suppress D2).
    client.put("/api/v1/monitor/rules/D2.restart_storm",
               json={"params": {"count": 3}}, headers=admin)


def test_monitor_signals_requires_service_token(client):
    sig = {"observed_at": "2026-06-02T01:23:45Z",
           "containers": [{"name": "zj-quant-data-keeper", "status": "running",
                           "oom_killed": False, "restart_count": 0,
                           "mem_usage": 1e9, "mem_limit": 8e9}]}
    # no / wrong token → 401
    assert client.post("/api/v1/monitor/signals", json=sig).status_code == 401
    assert client.post("/api/v1/monitor/signals", json=sig,
                       headers={"X-Monitor-Token": "nope"}).status_code == 401
    # correct shared token (reuses LOG_INGEST_TOKEN) → accepted
    ok = client.post("/api/v1/monitor/signals", json=sig,
                     headers={"X-Log-Token": "test-log-token"})
    assert ok.status_code == 200 and ok.json()["accepted"] == 1


def test_monitor_oom_signal_creates_alert_and_marks_layer_down(client, admin):
    from quant_web.monitor import routes as monitor_routes

    sig = {"observed_at": "2026-06-02T02:00:00Z",
           "containers": [{"name": "zj-quant-data-keeper", "status": "running",
                           "oom_killed": True, "restart_count": 91, "health": "none",
                           "mem_usage": 8_589_934_592, "mem_limit": 8_589_934_592}]}
    client.post("/api/v1/monitor/signals", json=sig,
                headers={"X-Log-Token": "test-log-token"})

    # topology reflects the raw observation (mem 100% + OOM flag)
    topo = client.get("/api/v1/monitor/topology", headers=admin).json()
    keeper = next(c for c in topo["containers"] if c["layer"] == "data")
    assert keeper["oom_killed"] is True and keeper["mem_pct"] == 100

    # drive one evaluation pass → D1 fires
    client.app.state.monitor_evaluator._tick()
    alerts = client.get("/api/v1/monitor/alerts",
                        params={"status": "firing"}, headers=admin).json()
    oom = [a for a in alerts["items"] if a["rule_id"] == "D1.oom_killed"]
    assert oom and oom[0]["layer"] == "data" and oom[0]["severity"] == "critical"

    # the monitor layer's verdict overrides liveness: data.state == down
    monitor_routes._health_cache["data"] = None
    health = client.get("/api/v1/monitor/health", headers=admin).json()
    assert health["layers"]["data"]["state"] == "down"
    assert health["alerts"]["firing"] >= 1

    # ack then resolve the alert
    aid = oom[0]["id"]
    assert client.post(f"/api/v1/monitor/alerts/{aid}/ack", headers=admin).json()["status"] == "acked"
    assert client.post(f"/api/v1/monitor/alerts/{aid}/resolve", headers=admin).json()["status"] == "resolved"


def test_monitor_logs_ingest_accepts_monitor_source(client, admin):
    """The sentinel ships source=monitor through the same /logs/ingest path."""
    r = client.post("/api/v1/logs/ingest",
                    json={"records": [{"source": "monitor", "level": "CRITICAL",
                                       "logger": "monitor.sentinel",
                                       "event": "monitor.container.oom_killed",
                                       "message": "data-keeper OOM"}]},
                    headers={"X-Log-Token": "test-log-token"})
    assert r.status_code == 200 and r.json()["accepted"] == 1
    _flush_logs(client)
    rows = client.get("/api/v1/logs", params={"source": "monitor"}, headers=admin).json()
    assert any(x["event"] == "monitor.container.oom_killed" for x in rows["items"])


def test_monitor_restart_storm_fires_d2(client, admin):
    """重启风暴 D2 — the detector that turns the last OOM crash loop from '10h
    无人发现' into '重启第 3 次即 CRITICAL'. Two observations build the window."""
    name = "zj-quant-data-keeper-stormtest"
    for rc in (0, 5):                               # ΔRestartCount = 5 ≥ N(3)
        client.post("/api/v1/monitor/signals",
                    json={"containers": [{"name": name, "status": "running",
                                          "restart_count": rc, "mem_usage": 1e9, "mem_limit": 8e9}]},
                    headers={"X-Log-Token": "test-log-token"})
    client.app.state.monitor_evaluator._tick()
    al = client.get("/api/v1/monitor/alerts", params={"status": "firing"}, headers=admin).json()
    storm = [a for a in al["items"] if a["rule_id"] == "D2.restart_storm" and a["target"] == name]
    assert storm and storm[0]["severity"] == "critical"
    assert storm[0]["detail"]["restart_delta"] == 5


def test_monitor_tasks_degrades_without_keeper(client, admin):
    """No KEEPER_BASE (本地写模式) → tasks tab degrades gracefully, never 500s."""
    body = client.get("/api/v1/monitor/tasks", headers=admin).json()
    assert body["keeper"] is False
    assert body["scheduler_tasks"] == {} and body["jobs"] == []
    assert body["deadman_hours"] >= 1


def test_monitor_rule_param_honors_db_override():
    """rules.param() must apply admin DB overrides. Regression guard: it used to
    pass the {"params":...,"enabled":...} wrapper straight to effective_params,
    so every threshold edit was silently ignored by the evaluator while the rules
    table still showed the new value (可观测层重构方案 §6.2)."""
    from quant_web.config import Settings
    from quant_web.monitor import rules as rulemod

    s = Settings()
    # no override → env/code default (restart_storm_count)
    assert rulemod.param(s, {}, "D2.restart_storm", "count", 0) == s.restart_storm_count
    # override (in the load_overrides wrapper shape) → honored, not the default
    overrides = {"D2.restart_storm": {"params": {"count": 7}, "enabled": True}}
    assert rulemod.param(s, overrides, "D2.restart_storm", "count", 0) == 7
    # unrelated keys in the override must not leak through
    assert rulemod.param(s, overrides, "D2.restart_storm", "window_min", 0) == s.restart_storm_window_min


def test_monitor_read_rbac_narrowing(client, admin, viewer):
    """折中收窄 (可观测层方案 §8): viewer 的告警列表只见 frontend 层, data/monitor/factor
    层告警被收窄掉; 任务台账(data 层视图)对 viewer 返回空。admin (全开) 不收窄。"""
    from quant_web.monitor.store import get_alert_store

    store = get_alert_store()
    store.trigger(rule_id="D1.oom_killed", severity="critical", layer="data",
                  target="rbac-data-ct", title="rbac data alert")
    store.trigger(rule_id="D11.frontend_crash", severity="warn", layer="frontend",
                  target="rbac-fe-sess", title="rbac fe alert")

    admin_items = client.get("/api/v1/monitor/alerts", params={"page_size": 200},
                             headers=admin).json()["items"]
    assert {"data", "frontend"} <= {a["layer"] for a in admin_items}   # admin 看全层

    viewer_items = client.get("/api/v1/monitor/alerts", params={"page_size": 200},
                              headers=viewer).json()["items"]
    assert viewer_items, "viewer 仍应看到自己有权的 frontend 层告警"
    assert all(a["layer"] == "frontend" for a in viewer_items)          # 只见 frontend
    assert not any(a.get("target") == "rbac-data-ct" for a in viewer_items)

    vt = client.get("/api/v1/monitor/tasks", headers=viewer).json()     # 任务台账对 viewer 空
    assert vt["scheduler_tasks"] == {} and vt["jobs"] == []


# --- OOM-hardening regression tests (2026-06 memory audit) -------------------
def test_sources_diff_requires_window_for_large_tables(client, admin):
    """A full-source diff materialises both sources in memory. Large tables must
    require a date window; omitting dates previously skipped the 90-day cap."""
    no_dates = client.post("/api/v1/data/sources/diff", headers=admin, json={
        "table": "raw.bars_daily", "source_a": "akshare", "source_b": "tushare_pro"})
    assert no_dates.status_code == 400
    assert no_dates.json()["error"]["code"] == "MISSING_PARAMETER"

    too_wide = client.post("/api/v1/data/sources/diff", headers=admin, json={
        "table": "raw.bars_daily", "source_a": "akshare", "source_b": "tushare_pro",
        "start": "2024-01-01", "end": "2024-12-31"})
    assert too_wide.status_code == 400
    assert too_wide.json()["error"]["code"] == "INVALID_DATE_RANGE"

    ok = client.post("/api/v1/data/sources/diff", headers=admin, json={
        "table": "raw.bars_daily", "source_a": "akshare", "source_b": "tushare_pro",
        "start": "2024-01-01", "end": "2024-02-01"})
    assert ok.status_code == 200
    assert "n_a" in ok.json() and "n_b" in ok.json()


def test_rows_all_requires_filter_for_unbounded_tables(client, admin):
    """/rows/all uses read_full_view (no LIMIT); fundamentals & corporate_actions
    were unguarded and could full-scan into the Python heap."""
    for name in ("raw.fundamentals", "raw.corporate_actions"):
        r = client.get(f"/api/v1/data/tables/{name}/rows/all", headers=admin)
        assert r.status_code == 400, name
        assert r.json()["error"]["code"] == "MISSING_PARAMETER", name
    # with a bounding filter it works again
    ok = client.get("/api/v1/data/tables/raw.corporate_actions/rows/all", headers=admin,
                    params={"symbols": "600519.SH"})
    assert ok.status_code == 200


def test_explorer_aggregate_pushdown(client, admin):
    """Chart explorer aggregates inside the engine over the current view rather
    than materialising the whole table; result shape must be intact."""
    r = client.post("/api/v1/data/charts/explorer/preview", headers=admin, json={
        "table": "raw.bars_daily", "x_axis": "date", "y_axes": ["close"],
        "aggregation": "monthly_avg", "chart_type": "line"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "x" in body and "series" in body
    assert body["series"] and len(body["series"][0]["data"]) == len(body["x"])
