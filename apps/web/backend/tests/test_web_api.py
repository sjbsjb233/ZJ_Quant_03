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
    for path in ("/api/v1/factors", "/api/v1/strategies/backtests", "/api/v1/monitor/alerts"):
        r = client.get(path, headers=admin)
        assert r.status_code == 501 and r.json()["error"]["code"] == "NOT_IMPLEMENTED"


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
    assert "quant_data" in sources, "data-layer log did not inherit the request trace_id"
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


def test_logs_query_is_admin_only(client, viewer):
    assert client.get("/api/v1/logs", headers=viewer).status_code == 403
    assert client.get("/api/v1/logs/summary", headers=viewer).status_code == 403


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
