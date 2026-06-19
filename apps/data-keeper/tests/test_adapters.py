"""Adapter unit tests — column units must be source-independent.

Regression: akshare 成交量 单位=手 (100 股) must be converted to shares, matching
the tushare fallback (vol 手 *100). Otherwise the standardized ``volume`` column
silently changes scale depending on which source served the day.
"""
from __future__ import annotations

from keeper_service.ingest.adapters import akshare as ak_mod


class _FakeDF:
    """Minimal stand-in for the pandas frame akshare returns (no pandas dep)."""

    def __init__(self, records):
        self._records = records

    @property
    def empty(self):
        return not self._records

    def rename(self, columns=None):
        # Our fake records already use the post-rename standard names.
        return self

    def to_dict(self, orient):
        return list(self._records)


def test_akshare_volume_converted_to_shares(monkeypatch):
    # 成交量=5 手, 成交额=5250 元 (akshare 成交额 already 元 → no conversion).
    row = {"date": "2025-11-03", "open": 10.0, "high": 11.0, "low": 9.0,
           "close": 10.5, "volume": 5, "amount": 5250.0, "turnover": 1.2}

    class _FakeAk:
        @staticmethod
        def stock_zh_a_hist(**kw):
            return _FakeDF([row])

    monkeypatch.setattr(ak_mod, "_AK", _FakeAk)

    out = ak_mod.fetch_bars_daily(["600519.SH"])
    assert len(out) == 1
    bar = out[0]
    assert bar["volume"] == 500          # 5 手 × 100 = 500 股 (NOT raw 5)
    assert bar["amount"] == 5250.0       # 成交额 已是元, 不再换算
    assert bar["symbol"] == "600519.SH"


def test_akshare_volume_handles_missing(monkeypatch):
    row = {"date": "2025-11-03", "open": 10.0, "high": 11.0, "low": 9.0,
           "close": 10.5, "amount": 1000.0}  # no 'volume' key

    class _FakeAk:
        @staticmethod
        def stock_zh_a_hist(**kw):
            return _FakeDF([row])

    monkeypatch.setattr(ak_mod, "_AK", _FakeAk)
    out = ak_mod.fetch_bars_daily(["600519.SH"])
    assert out[0]["volume"] == 0         # None → 0, no crash
