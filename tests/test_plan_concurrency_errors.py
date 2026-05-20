"""Error injection + concurrency semantics (test plan §7)."""
from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor

import pytest

from quant_data import DataReader, ingest, storage
from quant_data.metadata import batches
from quant_data.schemas import BARS_DAILY, get_spec

from .conftest import daily_bar, with_meta

pytestmark = pytest.mark.integration

D = dt.date(2024, 1, 2)


# --- §7.1 error injection ---------------------------------------------------
def test_ei001_failed_ingest_leaves_no_visible_data(root, reader, monkeypatch):
    def boom(spec, data, batch_id, root=None):
        raise RuntimeError("disk full")
    monkeypatch.setattr(storage, "write_append", boom)
    with pytest.raises(RuntimeError):
        ingest.ingest_bars_daily(records=[daily_bar(D, symbol="A")], apply_mapping=False, root=root)
    failed = batches.list_batches(target_table="raw.bars_daily", status="failed", root=root)
    assert len(failed) == 1
    assert reader.read_bars_daily(["A"], D, D).num_rows == 0  # nothing leaked


def test_ei006_corrupted_raw_parquet_raises(root, reader):
    ingest.ingest_bars_daily(records=[daily_bar(D, symbol="A")], apply_mapping=False, root=root)
    f = storage.list_data_files(get_spec("raw.bars_daily"), root=root)[0]
    with open(f, "wb") as fh:
        fh.write(b"corrupted-not-parquet")
    with pytest.raises(Exception):
        reader.read_bars_daily(["A"], D, D)


# --- §7.2 concurrency semantics ---------------------------------------------
def test_cc003_running_batch_not_visible(root, reader):
    # a batch left in 'running' (process died before finalize) must be excluded
    bid = batches.register_batch("akshare", "raw.bars_daily", root=root)
    storage.write_append(BARS_DAILY, with_meta([daily_bar(D, symbol="A")], bid), bid, root=root)
    # not finalized -> status running -> not in valid_batches
    assert reader.read_bars_daily(["A"], D, D).num_rows == 0
    batches.finalize_batch(bid, "success", row_count=1, root=root)
    assert reader.read_bars_daily(["A"], D, D).num_rows == 1


def test_cc001_002_parallel_ingests_coexist(root):
    # different and same partitions written concurrently -> distinct files, no clobber
    def job(i):
        d = dt.date(2024, 1, 2 + (i % 3))
        return ingest.ingest_bars_daily(records=[daily_bar(d, symbol=f"S{i}.SZ", close=float(i))],
                                        apply_mapping=False, root=root)
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(job, range(8)))
    assert all(r.row_count == 1 for r in results)
    files = storage.list_data_files(get_spec("raw.bars_daily"), root=root)
    assert len(files) == 8  # one immutable file per batch, none overwritten
    rd = DataReader(root=root)
    assert rd.read_bars_daily([f"S{i}.SZ" for i in range(8)]).num_rows == 8
