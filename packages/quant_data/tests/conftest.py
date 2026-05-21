"""Shared fixtures + sample-data builders for the quant_data test suite."""
from __future__ import annotations

import datetime as dt

import pytest

import quant_data as q
from quant_data import DataReader


@pytest.fixture
def root(tmp_path):
    """A fresh, bootstrapped data root for each test."""
    r = str(tmp_path / "data")
    q.bootstrap(r)
    return r


@pytest.fixture
def reader(root):
    return DataReader(root=root)


@pytest.fixture
def standard_world(root):
    """Bootstrapped root preloaded with the deterministic standard world (§1.1)."""
    from . import _world

    ctx = _world.load_standard_world(root)
    ctx["root"] = root
    return ctx


# --- sample-data builders ---------------------------------------------------
def with_meta(rows, batch_id="b", source="akshare"):
    """Stamp the raw metadata columns that ingest() normally adds (doc §1.1).

    Needed when a test drives storage.write_append() directly, bypassing ingest.
    """
    out = []
    for r in rows:
        r = dict(r)
        r.update(_batch_id=batch_id, _source=source, _ingested_at=dt.datetime(2025, 1, 1))
        out.append(r)
    return out


def daily_bar(date, symbol="000001.SZ", close=10.0, **kw):
    row = {
        "date": date,
        "symbol": symbol,
        "open": kw.get("open", close),
        "high": kw.get("high", close + 0.5),
        "low": kw.get("low", close - 0.5),
        "close": close,
        "pre_close": kw.get("pre_close", close),
        "volume": kw.get("volume", 1000),
        "amount": kw.get("amount", close * 1000),
        "adj_factor": kw.get("adj_factor", 1.0),
    }
    if "_extras" in kw:
        row["_extras"] = kw["_extras"]
    return row


def minute_bar(ts, symbol="000001.SZ", close=10.0, volume=100, **kw):
    return {
        "datetime": ts,
        "symbol": symbol,
        "frequency": "1m",
        "open": kw.get("open", close),
        "high": kw.get("high", close + 0.05),
        "low": kw.get("low", close - 0.05),
        "close": close,
        "volume": volume,
        "amount": kw.get("amount", close * volume),
        "adj_factor": kw.get("adj_factor", 1.0),
    }


def fundamental(symbol="000001.SZ", report_period=dt.date(2024, 9, 30), period_type="Q3",
                publish_date=dt.date(2024, 10, 25), **kw):
    row = {
        "symbol": symbol,
        "report_period": report_period,
        "period_type": period_type,
        "publish_date": publish_date,
        "currency": "CNY",
        "revenue": kw.get("revenue", 1.0e8),
        "net_income": kw.get("net_income", 1.0e7),
    }
    if "_extras" in kw:
        row["_extras"] = kw["_extras"]
    return row
