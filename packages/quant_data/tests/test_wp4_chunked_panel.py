"""T-WP4-001…003 — chunked_panel 分批原语(《内存治理总体方案》WP4).

分块 vs 整读逐字节一致(显式覆盖块边界两侧)/ 去重对抗跨块边界 / 峰值内存
结构性约束(单块 ≤ 整读,块数 > 1 ⇒ 峰值互不叠加)。
"""
from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from quant_data import config as qd_config
from quant_data import ingest
from quant_data.chunking import Window, chunk_days_for_budget, chunked_panel, must_chunk

from . import _world
from .conftest import daily_bar

START, END = dt.date(2024, 1, 2), dt.date(2024, 6, 28)
SYMS = ["AAA.SZ", "BBB.SZ"]
W = 5            # 动量窗口 (lookback 支撑)
CHUNK_DAYS = 20  # 属主块宽(交易日)→ ~6 块


@pytest.fixture
def world(root):
    days = _world.trading_days(START, END)
    ingest.ingest_calendar(records=_world.gen_calendar(
        START - dt.timedelta(days=30), END + dt.timedelta(days=30)), root=root)
    bars = []
    for i, d in enumerate(days):
        bars.append(daily_bar(d, symbol="AAA.SZ", close=10.0 + (i % 11) * 0.3,
                              adj_factor=0.8 if i < 40 else 1.0))
        bars.append(daily_bar(d, symbol="BBB.SZ", close=30.0 + (i % 7) * 0.7))
    ingest.ingest_bars_daily(records=bars, apply_mapping=False, root=root)
    # T-WP4-002 去重对抗:同 (date,symbol) 多 batch,数据横跨块边界两侧 ——
    # 第 20/21 个交易日恰是第 1↔2 块边界 (CHUNK_DAYS=20)。
    ingest.ingest_bars_daily(records=[
        daily_bar(days[19], symbol="AAA.SZ", close=77.0, adj_factor=0.8),
        daily_bar(days[20], symbol="AAA.SZ", close=78.0, adj_factor=0.8),
    ], apply_mapping=False, root=root)
    return days


def _momentum_from_panel(df: pl.DataFrame) -> pl.DataFrame:
    """px = close × adj_factor(乘法复权,分块稳定);w 日动量。"""
    return (
        df.sort(["symbol", "date"])
        .with_columns((pl.col("close") * pl.col("adj_factor")).alias("px"))
        .with_columns(
            (pl.col("px") / pl.col("px").shift(W).over("symbol") - 1.0).alias("mom")
        )
        .select(["date", "symbol", "mom"])
    )


def _whole_read(reader) -> pl.DataFrame:
    return pl.from_arrow(reader.read_bars_daily(
        symbols=SYMS, start=START - dt.timedelta(days=30), end=END,
        adjust="none", columns=["close", "adj_factor"],
    ))


def test_wp4_001_chunked_equals_whole(world, reader):
    days = world
    # 整读参照:多读 lookback 支撑再裁回窗口
    whole = _momentum_from_panel(_whole_read(reader)).filter(
        (pl.col("date") >= START) & (pl.col("date") <= END)
    ).sort(["symbol", "date"])

    parts = []
    n_chunks = 0
    for w, ext_df in chunked_panel(
        reader, table="raw.bars_daily", symbols=SYMS, start=START, end=END,
        columns=["close", "adj_factor"], lookback_days=W, chunk=CHUNK_DAYS,
    ):
        n_chunks += 1
        assert isinstance(w, Window)
        # 扩展面板确实带重叠(除轴端外)
        assert w.ext_start <= w.owner_start and w.ext_end >= w.owner_end
        parts.append(
            _momentum_from_panel(ext_df).filter(
                (pl.col("date") >= w.owner_start) & (pl.col("date") <= w.owner_end)
            )
        )
    assert n_chunks >= 5
    chunked = pl.concat(parts).sort(["symbol", "date"])
    assert chunked.equals(whole)  # 逐字节一致

    # 显式断言块边界两侧日期 (days[19] | days[20]) 的因子值一致
    for d in (days[19], days[20]):
        cw = chunked.filter(pl.col("date") == d).sort("symbol")
        ww = whole.filter(pl.col("date") == d).sort("symbol")
        assert cw.equals(ww) and cw.height == 2


def test_wp4_002_dedup_winner_consistent_across_boundary(world, reader):
    days = world
    rows = {}
    for w, ext_df in chunked_panel(
        reader, table="raw.bars_daily", symbols=SYMS, start=START, end=END,
        columns=["close", "adj_factor"], lookback_days=W, chunk=CHUNK_DAYS,
    ):
        own = ext_df.filter(
            (pl.col("date") >= w.owner_start) & (pl.col("date") <= w.owner_end)
        )
        for r in own.iter_rows(named=True):
            key = (r["symbol"], r["date"])
            assert key not in rows, "属主窗口互斥:同一行不得归属两个块"
            rows[key] = r["close"]
    # 去重赢家 = 第二批的 77/78(与整读一致)
    assert rows[("AAA.SZ", days[19])] == 77.0
    assert rows[("AAA.SZ", days[20])] == 78.0
    whole = _whole_read(reader)
    wm = {(r["symbol"], r["date"]): r["close"] for r in whole.iter_rows(named=True)
          if START <= r["date"] <= END}
    assert rows == wm


def test_wp4_003_peak_memory_structural_bound(world, reader):
    """T-WP4-003: 任一单块物化 ≤ 整读;块间峰值互不叠加(逐块消费后丢弃)。

    缩限环境下以 estimated_size 做结构性断言:max(单块) × 1.5 ≤ 整读体量,
    即分批把峰值压到整读的 2/3 以下(生产同构,块数越多压得越狠)。
    """
    whole_bytes = _whole_read(reader).estimated_size()
    chunk_bytes = []
    for _w, ext_df in chunked_panel(
        reader, table="raw.bars_daily", symbols=SYMS, start=START, end=END,
        columns=["close", "adj_factor"], lookback_days=W, chunk=CHUNK_DAYS,
    ):
        chunk_bytes.append(ext_df.estimated_size())
    assert len(chunk_bytes) >= 5
    assert max(chunk_bytes) * 1.5 <= whole_bytes


def test_wp4_budget_derived_chunk_days(world, reader):
    # 预算反推 (v1.2):chunk_days = budget // (rows/day × n_sym × n_col × 16 × 3)
    assert chunk_days_for_budget(1, 1, 1) >= 1            # 下限 1 交易日
    big = chunk_days_for_budget(4500, 500, 6, table="raw.bars_minute")
    small = chunk_days_for_budget(450, 500, 6, table="raw.bars_minute")
    assert small * 10 <= big <= (small + 1) * 10          # 线性于预算 (容 floor 取整)
    # 分钟线行数 ×240 计入反推:同预算下日线块宽 ≈ 分钟线 ×240
    daily = chunk_days_for_budget(450, 500, 6, table="raw.bars_daily")
    assert small * 240 <= daily <= (small + 1) * 240
    # budget_mb 接管 chunk 参数;预算压到单日成本之下 ⇒ 夹到下限=逐交易日一块
    # (任务峰值内存是算法的选择,不是数据规模的函数 —— 方案 WP4)
    n = sum(1 for _ in chunked_panel(
        reader, table="raw.bars_daily", symbols=SYMS, start=START,
        end=dt.date(2024, 1, 31), columns=["close", "adj_factor"],
        lookback_days=0, budget_mb=0.0001,
    ))
    assert n == len(_world.trading_days(START, dt.date(2024, 1, 31)))


def test_wp4_static_trigger_and_guards(world, reader, monkeypatch):
    # 静态触发表 (CHUNK_TABLES, 方案 §5)
    assert must_chunk("raw.bars_minute")
    assert not must_chunk("raw.bars_daily")
    monkeypatch.setenv(qd_config.CHUNK_TABLES_ENV, "raw.bars_minute,raw.bars_daily")
    assert must_chunk("raw.bars_daily")
    # 守则 §2:大表必须投影 —— columns 必填
    with pytest.raises(ValueError, match="columns"):
        next(chunked_panel(reader, table="raw.bars_daily", symbols=SYMS,
                           start=START, end=END, columns=[]))
    # chunk 单位解析
    from quant_data.chunking import _chunk_to_trading_days
    assert _chunk_to_trading_days("1Y") == 244
    assert _chunk_to_trading_days("3M") == 63
    assert _chunk_to_trading_days("90D") == 90
    assert _chunk_to_trading_days(7) == 7
    with pytest.raises(ValueError):
        _chunk_to_trading_days("1Q")
