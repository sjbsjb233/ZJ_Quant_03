"""FSQ builtins read the engine's factor_panel.parquet into [date, symbol, value]."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import polars as pl
import pytest


def _write_panel(path):
    pl.DataFrame(
        {
            "date": [dt.datetime(2025, 7, 1)] * 3 + [dt.datetime(2025, 7, 2)] * 3,
            "symbol": ["SH600519", "SZ000858", "SH600036"] * 2,
            "alpha_raw": [0.31, -0.12, 0.05, 0.28, -0.08, 0.02],
            "factor": [1.21, -1.05, -0.16, 1.18, -0.91, -0.27],
            "width": [0.081, 0.112, 0.064, 0.079, 0.108, 0.066],
        }
    ).write_parquet(path)


def _ctx(symbols, start=dt.date(2025, 7, 1), end=dt.date(2025, 7, 31)):
    return SimpleNamespace(symbols=symbols, start=start, end=end)


def test_registry_discovers_fsq_factors():
    from quant_factor.registry import discover_builtins

    builtins = discover_builtins()
    assert "fsqalpha" in builtins
    assert "fsqwidth" in builtins


def test_fsq_alpha_maps_symbols_and_filters_window(tmp_path, monkeypatch):
    from quant_factor.builtin.fsq import FSQAlpha

    panel = tmp_path / "factor_panel.parquet"
    _write_panel(panel)
    monkeypatch.setenv("FSQ_FACTOR_PANEL", str(panel))

    out = FSQAlpha().compute(
        _ctx(["600519.SH", "000858.SZ"], end=dt.date(2025, 7, 1))
    )
    assert out.columns == ["date", "symbol", "value"]
    assert out["date"].dtype == pl.Date
    assert sorted(out["symbol"].to_list()) == ["000858.SZ", "600519.SH"]
    by_sym = dict(zip(out["symbol"], out["value"]))
    assert by_sym["600519.SH"] == pytest.approx(1.21)


def test_fsq_width_reads_width_column(tmp_path, monkeypatch):
    from quant_factor.builtin.fsq import FSQWidth

    panel = tmp_path / "factor_panel.parquet"
    _write_panel(panel)
    monkeypatch.setenv("FSQ_FACTOR_PANEL", str(panel))

    out = FSQWidth().compute(_ctx(["600036.SH"]))
    assert out.height == 2
    assert out["value"].to_list() == pytest.approx([0.064, 0.066])


def test_missing_panel_raises_actionable_error(tmp_path, monkeypatch):
    from quant_factor.builtin.fsq import FSQAlpha

    monkeypatch.setenv("FSQ_FACTOR_PANEL", str(tmp_path / "absent.parquet"))
    with pytest.raises(FileNotFoundError, match="04_generate_factor"):
        FSQAlpha().compute(_ctx(["600519.SH"]))
