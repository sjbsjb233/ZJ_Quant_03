"""Table-level edge cases (test plan §2): the "关键用例详解" rows + adversarial inputs."""
from __future__ import annotations

import datetime as dt

import pyarrow.parquet as pq
import pytest

from quant_data import ingest, storage
from quant_data.schemas import get_spec

from .conftest import daily_bar

pytestmark = pytest.mark.integration


def _one_bar_file(root):
    return pq.read_table(storage.list_data_files(get_spec("raw.bars_daily"), root=root)[0]).to_pylist()


# --- §2.1 bars_daily --------------------------------------------------------
def test_bd007_limit_up_precision(root, reader):
    # T-BD-007: exact 10% limit-up price must not drift through serialize/read
    ingest.ingest_bars_daily(records=[daily_bar(dt.date(2024, 1, 2), symbol="600519.SH",
                                                pre_close=100.00, close=110.00)],
                             apply_mapping=False, root=root)
    assert reader.read_bars_daily(["600519.SH"]).column("close").to_pylist() == [110.00]


def test_bd008_null_adj_factor_not_zeroing(root, reader):
    # T-BD-008: NULL adj_factor must be treated as 1.0, never zero the price
    ingest.ingest_bars_daily(records=[daily_bar(dt.date(2024, 1, 2), symbol="A", close=50.0,
                                                adj_factor=None)], apply_mapping=False, root=root)
    pre = reader.read_bars_daily(["A"], adjust="pre").column("close").to_pylist()
    assert pre == [50.0]  # not None, not 0


def test_bd011_split_adjustment_continuity(root, reader):
    # T-BD-011: 10-for-10 bonus. pre-adjust factor normalised latest=1.0,
    # earlier day halved -> adjusted closes are continuous.
    ingest.ingest_bars_daily(records=[
        daily_bar(dt.date(2024, 6, 10), symbol="A", close=200.0, adj_factor=0.5),
        daily_bar(dt.date(2024, 6, 11), symbol="A", close=100.0, adj_factor=1.0),
    ], apply_mapping=False, root=root)
    assert reader.read_bars_daily(["A"], adjust="pre").column("close").to_pylist() == [100.0, 100.0]


def test_bd013_extras_roundtrip(root, reader):
    ingest.ingest_bars_daily(records=[daily_bar(dt.date(2024, 1, 2), symbol="A",
                                                _extras={"turnover": "2.5", "pe": "18.3"})],
                             apply_mapping=False, root=root)
    extras = dict(_one_bar_file(root)[0]["_extras"])
    assert extras == {"turnover": "2.5", "pe": "18.3"}


def test_bd014_extras_special_chars_and_injection(root, reader):
    # T-BD-014: weird keys (Chinese, spaces, SQL) survive intact, no injection
    payload = {"净资产收益率(%)": "15.32", "p/e ratio": "20.5", "k' OR 1=1--": "x"}
    ingest.ingest_bars_daily(records=[daily_bar(dt.date(2024, 1, 2), symbol="A", _extras=payload)],
                             apply_mapping=False, root=root)
    assert dict(_one_bar_file(root)[0]["_extras"]) == payload
    # reading the injection-style key as a column must be safe and return the value
    t = reader.read_bars_daily(["A"], extras_as_columns={"k' OR 1=1--": "VARCHAR"})
    assert t.column("k' OR 1=1--").to_pylist() == ["x"]


def test_adversarial_sql_injection_in_symbol(root, reader):
    ingest.ingest_bars_daily(records=[daily_bar(dt.date(2024, 1, 2), symbol="A")],
                             apply_mapping=False, root=root)
    # a malicious symbol must be safely parameter-escaped -> just no match, no harm
    out = reader.read_bars_daily(["'; DROP TABLE x; --"])
    assert out.num_rows == 0
    assert reader.read_bars_daily(["A"]).num_rows == 1  # table still intact


# --- §2.2 bars_minute -------------------------------------------------------
def test_bm003_lunch_break_no_fabricated_minute(root, reader):
    # T-BM-003: 11:30 then 13:01 are stored as-is; the layer never invents 12:00
    rows = [
        {"datetime": dt.datetime(2024, 1, 2, 11, 30), "symbol": "A", "frequency": "1m",
         "open": 10, "high": 10, "low": 10, "close": 10, "volume": 100, "amount": 1000.0},
        {"datetime": dt.datetime(2024, 1, 2, 13, 1), "symbol": "A", "frequency": "1m",
         "open": 10, "high": 10, "low": 10, "close": 10, "volume": 100, "amount": 1000.0},
    ]
    ingest.ingest_bars_minute(records=rows, apply_mapping=False, root=root)
    times = [r["datetime"] for r in reader.read_bars_minute(["A"]).to_pylist()]
    assert times == [dt.datetime(2024, 1, 2, 11, 30), dt.datetime(2024, 1, 2, 13, 1)]


# --- §2.3 fundamentals ------------------------------------------------------
@pytest.mark.parametrize("raw,expected", [("15.32", 15.32), ("N/A", None), ("", None), (None, None)])
def test_fd008_extras_cast_is_lenient(root, reader, raw, expected):
    # T-FD-008: bad casts must not raise; unparseable -> NULL (None), never an error
    extras = {"roe": raw} if raw is not None else {}
    ingest.ingest_fundamentals(records=[{
        "symbol": "A", "report_period": dt.date(2024, 9, 30), "period_type": "Q3",
        "publish_date": dt.date(2024, 10, 25), "currency": "CNY", "_extras": extras}], root=root)
    t = reader.read_fundamentals(["A"], as_of_date=dt.date(2024, 12, 1),
                                 extras_as_columns={"roe": "DOUBLE"})
    assert t.column("roe").to_pylist() == [expected]


# --- §2.6 corporate_actions -------------------------------------------------
def test_co002_high_song_zhuan_multiple_action_types_coexist(root, reader):
    # T-CO-002: 送/转/派 on the same day -> 3 rows, PK includes action_type
    day = dt.date(2024, 6, 15)
    ingest.ingest_corporate_actions(records=[
        {"symbol": "A", "action_date": day, "action_type": "cash_dividend", "cash_per_share": 0.1},
        {"symbol": "A", "action_date": day, "action_type": "stock_dividend", "stock_per_10": 3.0},
        {"symbol": "A", "action_date": day, "action_type": "transfer", "transfer_per_10": 2.0},
    ], apply_mapping=False, root=root)
    t = reader.current_view("raw.corporate_actions")
    assert set(t.column("action_type").to_pylist()) == {"cash_dividend", "stock_dividend", "transfer"}
    assert t.num_rows == 3


# --- §2.7/2.8 reference -----------------------------------------------------
def test_sy002_name_history_and_unicode(root, reader):
    ingest.ingest_symbols([{"symbol": "600446.SH", "name": "金证股份", "exchange": "SH",
                            "currency": "CNY", "is_active": True,
                            "name_history": '[{"name":"金证科技","until":"2007-01-01"}]'}], root=root)
    # T-SY-004: emoji / 繁体 survive UTF-8 roundtrip
    ingest.ingest_symbols([{"symbol": "00700.HK", "name": "騰訊控股 🐧", "exchange": "HK",
                            "currency": "HKD", "is_active": True}], root=root)
    got = {r["symbol"]: r["name"] for r in reader.read_symbols().to_pylist()}
    assert got["00700.HK"] == "騰訊控股 🐧"
    assert "金证科技" in reader.read_symbols(["600446.SH"]).column("name_history").to_pylist()[0]


def test_se002_two_classification_systems_same_code(root, reader):
    # T-SE-002: SW and CITICS can share a code value without colliding (PK has system)
    ingest.ingest_sectors([
        {"code": "010101", "name": "白酒", "level": 3, "classification_system": "sw"},
        {"code": "010101", "name": "其他", "level": 3, "classification_system": "citics"},
    ], root=root)
    assert reader.read_sectors("sw").column("name").to_pylist() == ["白酒"]
    assert reader.read_sectors("citics").column("name").to_pylist() == ["其他"]
