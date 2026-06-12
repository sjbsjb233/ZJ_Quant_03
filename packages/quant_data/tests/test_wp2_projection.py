"""T-WP2-001…004 — 读取层列投影(《内存治理总体方案》WP2).

逐字节等价 / 未知列报错 / adjust 自动并集 / 调用守则入注存在性。
"""
from __future__ import annotations

import datetime as dt

import pytest

import quant_data.reader as reader_mod
from quant_data import ingest
from quant_data.reader import ColumnNotInViewError, DataReader

from .conftest import daily_bar

D1, D2, D3 = dt.date(2025, 3, 3), dt.date(2025, 3, 4), dt.date(2025, 3, 5)


@pytest.fixture
def world(root):
    ingest.ingest_bars_daily(records=[
        daily_bar(D1, symbol="AAA", close=10.0, adj_factor=0.5),
        daily_bar(D2, symbol="AAA", close=11.0, adj_factor=1.0),
        daily_bar(D3, symbol="AAA", close=12.0, adj_factor=1.0),
        daily_bar(D1, symbol="BBB", close=20.0),
        daily_bar(D2, symbol="BBB", close=21.0),
    ], apply_mapping=False, root=root)
    # dedup 对抗:同 (date,symbol) 第二批 close 不同 —— 赢家必须同时支配
    # 全列读与投影读 (T-WP2-001 含 dedup 对抗数据)。
    ingest.ingest_bars_daily(records=[
        daily_bar(D2, symbol="AAA", close=111.0, adj_factor=1.0),
    ], apply_mapping=False, root=root)
    return root


# ── T-WP2-001: 任意列子集逐字节等价 ─────────────────────────────────────────
@pytest.mark.parametrize("cols", [
    ["close"],
    ["symbol", "date", "close", "volume"],
    ["adj_factor", "close"],
    ["date", "symbol"],
])
def test_wp2_001_projection_equals_full_read_select(world, reader, cols):
    full = reader.read_bars_daily()
    projected = reader.read_bars_daily(columns=cols)
    # pk 自动并集,请求顺序在前、缺失 pk 追加在后
    want_cols = list(dict.fromkeys(cols))
    want_cols += [k for k in ("date", "symbol") if k not in want_cols]
    assert projected.column_names == want_cols
    assert projected.equals(full.select(want_cols))  # Arrow 逐字节等价(含 dedup 赢家)
    # dedup 对抗:赢家 close=111.0 在两条路径一致
    if "close" in cols:
        m = {(r["symbol"], r["date"]): r["close"] for r in projected.to_pylist()}
        assert m[("AAA", D2)] == 111.0


def test_wp2_001_filters_compose_with_projection(world, reader):
    full = reader.read_bars_daily(symbols=["AAA"], start=D2, end=D3)
    proj = reader.read_bars_daily(symbols=["AAA"], start=D2, end=D3,
                                  columns=["close", "date", "symbol"])
    assert proj.equals(full.select(["close", "date", "symbol"]))


# ── T-WP2-002: 未知列 → 类型化报错,不静默忽略 ──────────────────────────────
def test_wp2_002_unknown_column_raises(world, reader):
    # 注:测试世界经 normalize_to_arrow 写入,schema 声明列(如市值列)在文件
    # 中物理存在(全 NULL)→ 视图可服务;"声明但未入库"的事故形态(旧批次
    # 旧 schema)由空视图分支 + aggregate 的 binder 翻译测试覆盖。
    with pytest.raises(ColumnNotInViewError) as ei:
        reader.read_bars_daily(columns=["nope"])
    assert ei.value.column == "nope"
    assert ei.value.table == "raw.bars_daily"
    assert isinstance(ei.value, ValueError)  # 兼容既有 ValueError 调用方


def test_wp2_002_empty_view_projection_schema(root):
    r = DataReader(root=root)
    out = r.read_bars_daily(columns=["close"])
    assert out.num_rows == 0
    assert out.column_names == ["close", "date", "symbol"]
    with pytest.raises(ColumnNotInViewError):
        r.read_bars_daily(columns=["nope"])


# ── T-WP2-003: adjust/recompute 自动并集 adj_factor 且输出一致 ──────────────
def test_wp2_003_adjust_auto_unions_adj_factor(world, reader):
    full_post = reader.read_bars_daily(adjust="post")
    proj_post = reader.read_bars_daily(adjust="post", columns=["close"])
    assert "adj_factor" in proj_post.column_names  # 自动补列
    sel = ["close", "date", "symbol", "adj_factor"]
    assert proj_post.select(sel).equals(full_post.select(sel))


def test_wp2_003_recompute_adj_auto_unions(world, reader):
    proj = reader.read_bars_daily(recompute_adj=True, columns=["close"],
                                  symbols=["AAA"])
    assert "adj_factor" in proj.column_names
    full = reader.read_bars_daily(recompute_adj=True, symbols=["AAA"])
    sel = ["close", "date", "symbol", "adj_factor"]
    assert proj.select(sel).equals(full.select(sel))


def test_wp2_universe_fundamentals_columns(world, reader, root):
    ingest.ingest_universe(records=[
        {"date": D1, "symbol": "AAA", "is_listed": True, "is_suspended": False,
         "is_tradable": True, "st_status": "normal", "in_hs300": True,
         "in_zz500": False, "in_zz1000": False, "in_sse50": False},
    ], root=root)
    uni = reader.read_universe(columns=["is_tradable"])
    assert uni.column_names == ["is_tradable", "date", "symbol"]


# ── 投影缺失扫尾日志 (WP2.2 迁移期) ─────────────────────────────────────────
def test_wp2_projection_missing_log_fires(world, reader, monkeypatch):
    from quant_data import logging as qd_logging

    events: list[str] = []
    qd_logging.register_sink(lambda rec: events.append(rec.event))
    try:
        monkeypatch.setattr(reader_mod, "_PROJECTION_LOG_BYTES", 0)  # 缩限阈值
        monkeypatch.setenv("QUANT_READER_PROJECTION_LOG", "0")
        reader.read_bars_daily()
        assert "reader.projection.missing" not in events  # 开关关 ⇒ 静默
        monkeypatch.setenv("QUANT_READER_PROJECTION_LOG", "1")
        reader.read_bars_daily()
        assert "reader.projection.missing" in events      # 未投影大读 ⇒ 上报
        events.clear()
        reader.read_bars_daily(columns=["close"])
        assert "reader.projection.missing" not in events  # 已投影 ⇒ 不报
    finally:
        qd_logging.unregister_all_sinks()


# ── T-WP2-004: 调用守则入注存在性 (CI 镜像断言) ─────────────────────────────
def test_wp2_004_reader_charter_docstring_present():
    doc = reader_mod.__doc__ or ""
    assert "调用守则" in doc
    # 四条禁止条款关键词逐一在场 (防重构误删;与 CI grep 互为镜像)
    assert ".to_pylist()" in doc
    assert 'adjust="post"' in doc
    assert "重复读" in doc
    assert "invariants" in doc
    # 重读调用点清单 + 登记示例 (chunked_panel, WP4 走查)
    assert "重读调用点清单" in doc
    assert "chunked_panel" in doc
    # 方法级 columns 守则
    assert "大表必须过滤" in (DataReader.read_bars_daily.__doc__ or "")
    assert "大表必须过滤" in (DataReader.read_universe.__doc__ or "")
