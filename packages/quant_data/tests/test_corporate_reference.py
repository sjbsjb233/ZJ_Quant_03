"""Corporate-action adjustment factors (doc §6.3/§9.9) + reference dicts (doc §5)."""
from __future__ import annotations

import datetime as dt

from quant_data import corporate, ingest

from .conftest import daily_bar


# --- corporate actions ------------------------------------------------------
def test_cash_dividend_factor(root):
    ingest.ingest_bars_daily(records=[
        daily_bar(dt.date(2025, 11, 3), symbol="X", close=20.0),
        daily_bar(dt.date(2025, 11, 4), symbol="X", close=19.0),
    ], apply_mapping=False, root=root)
    ingest.ingest_corporate_actions(records=[
        {"symbol": "X", "action_date": dt.date(2025, 11, 4), "action_type": "cash_dividend",
         "cash_per_share": 1.0}
    ], root=root)
    fac = {r["date"]: round(r["adj_factor"], 4) for r in
           corporate.compute_adj_factors(symbols=["X"], root=root).to_pylist()}
    # ex-date factor stays 1.0; prior day scaled by (20-1)/20 = 0.95
    assert fac[dt.date(2025, 11, 4)] == 1.0
    assert fac[dt.date(2025, 11, 3)] == 0.95


def test_stock_dividend_factor(root):
    # 10 -> sends 10 shares per 10 (1:1 bonus) on ex-date; prev close 10
    ingest.ingest_bars_daily(records=[
        daily_bar(dt.date(2025, 11, 3), symbol="Y", close=10.0),
        daily_bar(dt.date(2025, 11, 4), symbol="Y", close=5.0),
    ], apply_mapping=False, root=root)
    ingest.ingest_corporate_actions(records=[
        {"symbol": "Y", "action_date": dt.date(2025, 11, 4), "action_type": "stock_dividend",
         "stock_per_10": 10.0}
    ], root=root)
    fac = {r["date"]: round(r["adj_factor"], 4) for r in
           corporate.compute_adj_factors(symbols=["Y"], root=root).to_pylist()}
    # denom = 1 + 10/10 = 2, adj_prev = 10/2 = 5, ratio = 0.5
    assert fac[dt.date(2025, 11, 3)] == 0.5


def test_no_actions_factor_is_one(root):
    ingest.ingest_bars_daily(records=[daily_bar(dt.date(2025, 11, 3), symbol="Z", close=10.0)],
                             apply_mapping=False, root=root)
    fac = corporate.compute_adj_factors(symbols=["Z"], root=root).to_pylist()
    assert all(r["adj_factor"] == 1.0 for r in fac)


# --- reference dictionaries -------------------------------------------------
def test_symbols_upsert_and_read(root, reader):
    ingest.ingest_symbols([{"symbol": "000001.SZ", "name": "平安银行", "exchange": "SZ",
                            "currency": "CNY", "is_active": True}], root=root)
    ingest.ingest_symbols([{"symbol": "000002.SZ", "name": "万科A", "exchange": "SZ",
                            "currency": "CNY", "is_active": False}], root=root)
    assert reader.read_symbols().num_rows == 2
    assert reader.read_symbols(active_only=True).column("name").to_pylist() == ["平安银行"]


def test_symbols_upsert_overwrites_same_pk(root, reader):
    ingest.ingest_symbols([{"symbol": "000001.SZ", "name": "old", "exchange": "SZ",
                            "currency": "CNY", "is_active": True}], root=root)
    ingest.ingest_symbols([{"symbol": "000001.SZ", "name": "new", "exchange": "SZ",
                            "currency": "CNY", "is_active": True}], root=root)
    t = reader.read_symbols(["000001.SZ"])
    assert t.num_rows == 1 and t.column("name").to_pylist() == ["new"]


def test_symbols_extras_column(root, reader):
    ingest.ingest_symbols([{"symbol": "000001.SZ", "name": "n", "exchange": "SZ",
                            "currency": "CNY", "is_active": True, "_extras": {"isin": "CNE1"}}],
                          root=root)
    t = reader.read_symbols(["000001.SZ"], extras_as_columns={"isin": "VARCHAR"})
    assert t.column("isin").to_pylist() == ["CNE1"]


def test_sectors_multi_classification(root, reader):
    ingest.ingest_sectors([
        {"code": "801010", "name": "农林牧渔", "level": 1, "classification_system": "sw"},
        {"code": "01010001", "name": "种植业", "level": 3, "classification_system": "citics"},
    ], root=root)
    assert reader.read_sectors("sw").column("name").to_pylist() == ["农林牧渔"]
    assert reader.read_sectors("citics").num_rows == 1


def test_sectors_members_roster(root, reader):
    import json

    ingest.ingest_sectors([
        {"code": "801780", "name": "银行", "level": 1, "classification_system": "sw",
         "members": json.dumps(["600519.SH", "000001.SZ"]), "member_count": 2},
    ], root=root)
    row = reader.read_sectors("sw").to_pylist()[0]
    assert row["member_count"] == 2
    assert json.loads(row["members"]) == ["600519.SH", "000001.SZ"]


def test_sectors_members_optional(root, reader):
    # rows without a roster (e.g. the demo dict) land with NULL members.
    ingest.ingest_sectors([
        {"code": "801010", "name": "农林牧渔", "level": 1, "classification_system": "sw"},
    ], root=root)
    row = reader.read_sectors("sw").to_pylist()[0]
    assert row["members"] is None
    assert row["member_count"] is None
