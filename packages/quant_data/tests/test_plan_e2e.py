"""End-to-end workflow tests (test plan §5). These are CI gates."""
from __future__ import annotations

import datetime as dt

import pyarrow.parquet as pq
import pytest

from quant_data import governance, ingest, migration, storage
from quant_data.metadata import batches, field_registry, sources
from quant_data.schemas import get_spec

from .conftest import daily_bar, fundamental

pytestmark = pytest.mark.e2e

D = dt.date(2024, 1, 2)


# --- E2E-001 normal ingest (idempotent) -------------------------------------
def test_e2e001_normal_ingest_and_idempotency(root, reader):
    params = dict(symbols=["600519.SH"], start="2024-01-02", end="2024-01-02")
    r1 = ingest.ingest_bars_daily(records=[daily_bar(D, symbol="600519.SH")],
                                  apply_mapping=False, dedup=True, **params, root=root)
    # batch finalized success, data immediately readable
    assert batches.get_batch(r1.batch_id, root=root)["status"] == "success"
    assert reader.read_bars_daily(["600519.SH"], D, D).num_rows == 1
    # second call with identical parameters is idempotent: same batch, no new file
    n_files = len(storage.list_data_files(get_spec("raw.bars_daily"), root=root))
    r2 = ingest.ingest_bars_daily(records=[daily_bar(D, symbol="600519.SH")],
                                  apply_mapping=False, dedup=True, **params, root=root)
    assert r2.skipped and r2.batch_id == r1.batch_id
    assert len(storage.list_data_files(get_spec("raw.bars_daily"), root=root)) == n_files


# --- E2E-002 pollution correction -------------------------------------------
def test_e2e002_pollution_correction_full_flow(root, reader):
    good = [daily_bar(D, symbol=s, close=10.0 + i) for i, s in enumerate(["000001.SZ", "600519.SH"])]
    batch_a = ingest.ingest_bars_daily(records=good, apply_mapping=False, root=root)

    # pollution: close x10 for one symbol via a new batch
    bad = ingest.ingest_bars_daily(records=[daily_bar(D, symbol="000001.SZ", close=100.0)],
                                   apply_mapping=False, root=root)
    assert reader.read_bars_daily(["000001.SZ"], D, D).column("close").to_pylist() == [100.0]

    # invalidate + reload from an alternate source (corrective batch) + supersede link
    batches.invalidate_batch(bad.batch_id, "价格单位错误", root=root)
    sources.register_source("tushare_pro", priority=50, root=root)
    fix = ingest.ingest_bars_daily(records=[daily_bar(D, symbol="000001.SZ", close=10.0)],
                                   apply_mapping=False, source="tushare_pro", root=root)
    batches.supersede_batch(bad.batch_id, fix.batch_id, root=root)

    # 1) polluted rows still physically on disk (audit)
    files = storage.list_data_files(get_spec("raw.bars_daily"), root=root)
    on_disk = [r for f in files for r in pq.read_table(f).to_pylist()
               if r["_batch_id"] == bad.batch_id]
    assert len(on_disk) == 1
    # 2) current view shows corrected + unaffected
    got = {r["symbol"]: r["close"] for r in reader.read_bars_daily(["000001.SZ", "600519.SH"], D, D).to_pylist()}
    assert got == {"000001.SZ": 10.0, "600519.SH": 11.0}
    # 3) audit ledger
    assert bad.batch_id in {b["batch_id"] for b in governance.audit_invalidated(root=root)}
    chain = governance.lineage(bad.batch_id, root=root)
    assert [c["batch_id"] for c in chain] == [bad.batch_id, fix.batch_id]
    assert batches.get_batch(fix.batch_id, root=root)["supersedes"] == bad.batch_id


# --- E2E-003 field promotion extras -> core ---------------------------------
def test_e2e003_field_promotion_full_flow(root, reader):
    # Phase 1: first sight auto-registers
    ingest.ingest_fundamentals(records=[fundamental(symbol="600519.SH", _extras={"roe": "15.32"})],
                               root=root)
    assert field_registry.get_field("raw.fundamentals", "roe", root=root)["status"] == "extras"
    # Phase 2: promote (no schema change)
    field_registry.promote_field("raw.fundamentals", "roe", "promoted",
                                 consumers=["factor.roe"], root=root)
    f = storage.list_data_files(get_spec("raw.fundamentals"), root=root)[0]
    assert "roe" in dict(pq.read_table(f).to_pylist()[0]["_extras"])
    # Phase 3: migrate to core
    migration.promote_field_to_core("raw.fundamentals", "roe", root=root)
    row = pq.read_table(storage.list_data_files(get_spec("raw.fundamentals"), root=root)[0]).to_pylist()[0]
    assert row["roe"] == 15.32 and "roe" not in dict(row["_extras"])
    assert field_registry.get_field("raw.fundamentals", "roe", root=root)["status"] == "core"
    # compat: old extras_as_columns call still returns the value
    t = reader.read_fundamentals(["600519.SH"], as_of_date=dt.date(2025, 1, 1),
                                 extras_as_columns={"roe": "DOUBLE"})
    assert t.column("roe").to_pylist() == [15.32]


def test_e2e003_history_batch_missing_field_becomes_null(root, reader):
    # a batch predating the field has no 'roe' in _extras; after migration it's NULL
    ingest.ingest_fundamentals(records=[fundamental(symbol="A", report_period=dt.date(2024, 3, 31),
                                                    period_type="Q1")], root=root)  # no roe
    ingest.ingest_fundamentals(records=[fundamental(symbol="B", report_period=dt.date(2024, 3, 31),
                                                    period_type="Q1", _extras={"roe": "9.9"})], root=root)
    migration.promote_field_to_core("raw.fundamentals", "roe", root=root)
    t = reader.read_fundamentals(["A", "B"], as_of_date=dt.date(2025, 1, 1),
                                 extras_as_columns={"roe": "DOUBLE"})
    got = {r["symbol"]: r["roe"] for r in t.to_pylist()}
    assert got == {"A": None, "B": 9.9}


# --- E2E-004 switch primary source ------------------------------------------
def test_e2e004_switch_primary_source(root, reader):
    # A: parallel ingest, tushare lower priority
    sources.register_source("tushare_pro", priority=50, root=root)
    ingest.ingest_bars_daily(records=[daily_bar(D, symbol="600519.SH", close=100.0)],
                             source="akshare", apply_mapping=False, root=root)
    ingest.ingest_bars_daily(records=[daily_bar(D, symbol="600519.SH", close=100.2)],
                             source="tushare_pro", apply_mapping=False, root=root)
    # B: diff
    d = governance.diff_sources("raw.bars_daily", "akshare", "tushare_pro", root=root)
    assert d["n_mismatch"] == 1
    # C: switch priority -> takes effect with no service restart / no code change
    assert reader.read_bars_daily(["600519.SH"], D, D).column("close").to_pylist() == [100.0]
    sources.update_source_priority("tushare_pro", 300, root=root)
    assert reader.read_bars_daily(["600519.SH"], D, D).column("close").to_pylist() == [100.2]
    # D: deactivate old source
    sources.update_source("akshare", is_active=False, root=root)
    assert reader.read_bars_daily(["600519.SH"], D, D).column("close").to_pylist() == [100.2]
