"""T-WP1-001 — compute_adj_factors SQL 化 golden(《内存治理总体方案》WP1.1).

新旧实现全 symbols 输出逐值一致(rtol=1e-12);新实现不再通过
read_bars_daily 全量物化 bars(内存学科断言,缩限环境下替代 RSS 计量)。
"""
from __future__ import annotations

import datetime as dt
import math

import pytest

from quant_data import corporate, ingest
from quant_data.corporate import _compute_adj_factors_legacy, compute_adj_factors
from quant_data.reader import DataReader

from .conftest import daily_bar


def _build_world(root):
    """3 symbols × 跨年窗口 + 事件大礼包:现金/送股/拆分/配股/同日双事件/
    首bar前事件(跳过)/末bar后无对应 + 跨批次去重对抗。"""
    days = []
    d = dt.date(2024, 11, 25)
    while len(days) < 30:                      # 跨 2024→2025 (跨 hive 分区)
        if d.weekday() < 5:
            days.append(d)
        d += dt.timedelta(days=1)

    bars = []
    for i, day in enumerate(days):
        bars.append(daily_bar(day, symbol="AAA", close=20.0 + i * 0.1))
        bars.append(daily_bar(day, symbol="BBB", close=10.0 + (i % 7) * 0.5))
        bars.append(daily_bar(day, symbol="CCC", close=50.0 - i * 0.2))
    ingest.ingest_bars_daily(records=bars, apply_mapping=False, root=root)
    # 去重对抗:AAA 第 10 天重灌一条不同 close 的新批次 —— 当前视图赢家是它;
    # prev_close 必须取赢家(两实现都经 current view,golden 应一致)。
    ingest.ingest_bars_daily(
        records=[daily_bar(days[10], symbol="AAA", close=99.0)],
        apply_mapping=False, root=root,
    )

    actions = [
        # 首 bar 之前的事件 → 无 prev bar,两实现都跳过
        {"symbol": "AAA", "action_date": days[0] - dt.timedelta(days=3),
         "action_type": "cash_dividend", "cash_per_share": 9.9},
        {"symbol": "AAA", "action_date": days[5], "action_type": "cash_dividend",
         "cash_per_share": 1.0},
        # 同日双事件(现金+送股)→ 同一 ex-date 两个 ratio 连乘
        {"symbol": "AAA", "action_date": days[11], "action_type": "cash_dividend",
         "cash_per_share": 0.5},
        {"symbol": "AAA", "action_date": days[11], "action_type": "stock_dividend",
         "stock_per_10": 5.0},
        # 非交易日 ex-date(周六)→ prev = 上一交易日,区间语义照常
        {"symbol": "BBB", "action_date": days[8] + dt.timedelta(days=5),
         "action_type": "split", "split_ratio": 2.0},
        {"symbol": "BBB", "action_date": days[20], "action_type": "rights_issue",
         "rights_ratio": 0.3, "rights_price": 5.0},
        {"symbol": "CCC", "action_date": days[15], "action_type": "transfer",
         "transfer_per_10": 3.0},
        # 非价格事件 → 两实现都忽略
        {"symbol": "CCC", "action_date": days[16], "action_type": "name_change"},
    ]
    ingest.ingest_corporate_actions(records=actions, root=root)
    return days


def _as_map(tbl):
    return {(r["symbol"], r["date"]): r["adj_factor"] for r in tbl.to_pylist()}


def test_wp1_001_golden_new_vs_legacy(root):
    _build_world(root)
    new = _as_map(compute_adj_factors(root=root))
    old = _as_map(_compute_adj_factors_legacy(root=root))
    assert set(new) == set(old)
    assert len(new) == 30 * 3
    for key, ov in old.items():
        nv = new[key]
        assert math.isclose(nv, ov, rel_tol=1e-12), (key, nv, ov)
    # 事件确实改变了部分因子(测试不是在比较两个恒 1.0 的表)
    assert any(abs(v - 1.0) > 1e-9 for v in new.values())


def test_wp1_001_golden_symbol_subset(root):
    _build_world(root)
    new = _as_map(compute_adj_factors(symbols=["AAA", "BBB"], root=root))
    old = _as_map(_compute_adj_factors_legacy(symbols=["AAA", "BBB"], root=root))
    assert set(new) == set(old)
    assert all(s in ("AAA", "BBB") for s, _ in new)
    for key, ov in old.items():
        assert math.isclose(new[key], ov, rel_tol=1e-12)


def test_wp1_001_new_impl_never_materialises_bars(root):
    """内存学科:新实现禁止经 read_bars_daily 全量物化(WP1.1 的本体)。"""
    _build_world(root)

    class NoMaterialiseReader(DataReader):
        def read_bars_daily(self, *a, **kw):  # noqa: ARG002
            raise AssertionError("WP1.1: compute_adj_factors 不得物化 bars 当前视图")

    out = compute_adj_factors(reader=NoMaterialiseReader(root=root))
    assert out.num_rows == 30 * 3
    # legacy 路径确实走 read_bars_daily(对照证明 spy 有效)
    with pytest.raises(AssertionError, match="不得物化"):
        _compute_adj_factors_legacy(reader=NoMaterialiseReader(root=root))


def test_wp1_001_empty_and_no_event_paths(root):
    # 空库:三列空表
    empty = compute_adj_factors(root=root)
    assert empty.num_rows == 0
    assert empty.column_names == ["symbol", "date", "adj_factor"]
    # 有 bars 无事件:全 1.0(与 legacy 一致)
    ingest.ingest_bars_daily(
        records=[daily_bar(dt.date(2025, 11, 3), symbol="Z", close=10.0)],
        apply_mapping=False, root=root,
    )
    out = corporate.compute_adj_factors(symbols=["Z"], root=root).to_pylist()
    assert out and all(r["adj_factor"] == 1.0 for r in out)


def test_wp1_001_dirty_events_byte_identical_plus_warned(root):
    """终审 🟠-1: 脏事件(负现金/0拆股/未来除权)下,新实现仍与 legacy 逐字节
    一致(共用 _event_ratio,不丢弃),且每个脏事件都有 corporate.event.invalid
    告警(不静默)。未来除权日是合法分红 —— 不再被当脏数据,与 legacy 同样折进。"""
    from quant_data import corporate
    from quant_data import logging as qd_logging
    from quant_data.corporate import _compute_adj_factors_legacy

    days = [dt.date(2025, 11, 3), dt.date(2025, 11, 4), dt.date(2025, 11, 5)]
    ingest.ingest_bars_daily(
        records=[daily_bar(d, symbol="D", close=20.0 - i) for i, d in enumerate(days)],
        apply_mapping=False, root=root,
    )
    ingest.ingest_corporate_actions(records=[
        # 负现金分红:legacy 折进 >1 因子 —— 新实现必须一致(不得丢成 1.0)
        {"symbol": "D", "action_date": days[1], "action_type": "cash_dividend",
         "cash_per_share": -2.0},
        # 0 拆股:_event_ratio 内建护栏两侧都 → 1.0
        {"symbol": "D", "action_date": days[2], "action_type": "split",
         "split_ratio": 0.0},
        # 未来除权(晚于末 bar):合法待派息,legacy 折进全部 bar,新实现亦然
        {"symbol": "D", "action_date": dt.date(2026, 1, 9),
         "action_type": "cash_dividend", "cash_per_share": 0.5},
    ], root=root)

    events = []
    qd_logging.register_sink(lambda rec: events.append(rec.event))
    try:
        new = {(r["symbol"], r["date"]): r["adj_factor"]
               for r in corporate.compute_adj_factors(symbols=["D"], root=root).to_pylist()}
    finally:
        qd_logging.unregister_all_sinks()
    old = {(r["symbol"], r["date"]): r["adj_factor"]
           for r in _compute_adj_factors_legacy(symbols=["D"], root=root).to_pylist()}

    assert set(new) == set(old)
    for k, ov in old.items():
        assert math.isclose(new[k], ov, rel_tol=1e-12), (k, new[k], ov)
    # 未来除权折进 ⇒ 末 bar 因子 ≠ 1.0(证明它没被当脏数据丢弃)
    assert abs(new[("D", days[-1])] - 1.0) > 1e-9
    # 不静默:负现金 + 0拆股各一条告警(共 2 条;未来除权不算脏,不告警)
    assert events.count("corporate.event.invalid") == 2
