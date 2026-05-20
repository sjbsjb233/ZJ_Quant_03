"""Source switching, diffing, and provenance audit (doc §7.2, §7.4)."""
from __future__ import annotations

import datetime as dt

from quant_data import governance, ingest
from quant_data.metadata import batches, sources

from .conftest import daily_bar

D = dt.date(2025, 11, 3)


def test_diff_sources_reports_mismatches_and_exclusives(root):
    sources.register_source("tushare_pro", priority=50, root=root)
    ingest.ingest_bars_daily(records=[daily_bar(D, symbol="A", close=10.0),
                                      daily_bar(D, symbol="B", close=20.0)],
                             source="akshare", apply_mapping=False, root=root)
    ingest.ingest_bars_daily(records=[daily_bar(D, symbol="A", close=10.5),  # mismatch
                                      daily_bar(D, symbol="C", close=30.0)],  # only in b
                             source="tushare_pro", apply_mapping=False, root=root)
    d = governance.diff_sources("raw.bars_daily", "akshare", "tushare_pro", root=root)
    assert d["n_mismatch"] == 1
    assert d["mismatches"][0]["diffs"]["close"] == (10.0, 10.5)
    assert d["n_only_in_a"] == 1 and any(k[1] == "B" for k in d["only_in_a"])
    assert d["n_only_in_b"] == 1 and any(k[1] == "C" for k in d["only_in_b"])


def test_switch_primary_source_is_priority_only(root, reader):
    sources.register_source("tushare_pro", priority=50, root=root)
    ingest.ingest_bars_daily(records=[daily_bar(D, close=10.0)], source="akshare", root=root)
    ingest.ingest_bars_daily(records=[daily_bar(D, close=20.0)], source="tushare_pro", root=root)
    before = reader.read_bars_daily(["000001.SZ"], D, D).column("close").to_pylist()
    sources.update_source_priority("tushare_pro", 200, root=root)
    after = reader.read_bars_daily(["000001.SZ"], D, D).column("close").to_pylist()
    assert before == [10.0] and after == [20.0]


def test_audit_invalidated_sorted(root):
    b1 = ingest.ingest_bars_daily(records=[daily_bar(D, close=1.0)], root=root)
    b2 = ingest.ingest_bars_daily(records=[daily_bar(dt.date(2025, 11, 4), close=2.0)], root=root)
    batches.invalidate_batch(b1.batch_id, "r1", root=root)
    batches.invalidate_batch(b2.batch_id, "r2", root=root)
    aud = governance.audit_invalidated(root=root)
    assert {a["batch_id"] for a in aud} == {b1.batch_id, b2.batch_id}
    assert aud[0]["invalidated_at"] >= aud[1]["invalidated_at"]  # newest first


def test_lineage_follows_supersession_chain(root):
    old = ingest.ingest_bars_daily(records=[daily_bar(D, close=1.0)], root=root)
    new = ingest.ingest_bars_daily(records=[daily_bar(D, close=2.0)], root=root)
    batches.invalidate_batch(old.batch_id, "bad", root=root)
    batches.supersede_batch(old.batch_id, new.batch_id, root=root)
    chain = governance.lineage(old.batch_id, root=root)
    assert [c["batch_id"] for c in chain] == [old.batch_id, new.batch_id]
