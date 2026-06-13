"""T-WP3-001…003 — 因子层一次读 + 复用(《内存治理总体方案》WP3).

golden(投影路径 vs 全列回退路径输出逐值一致)/ reader spy 全量读 == 1 /
preflight 零物化 + 行为回归。
"""
from __future__ import annotations

import datetime as dt

import polars as pl
import pytest

from quant_factor.base import Factor, FactorContext
from quant_factor.config import EvalConfig
from quant_factor.degradation import DegradationLedger
from quant_factor.evaluate import FactorEvaluator
from quant_factor.preflight import evaluate_checks, run_preflight
from quant_factor.returns import forward_returns, price_columns
from quant_factor.testing import FakeReader

UNI = {"universe_id": "preset:hs300", "kind": "preset_index", "preset_flag": "in_hs300",
       "exclude_st": True, "exclude_suspended": True, "min_list_days": 0}


def _cfg(**kw) -> EvalConfig:
    base = dict(
        universe_id="preset:hs300",
        start=dt.date(2024, 1, 2), end=dt.date(2024, 3, 1),
        rebalance="W", horizons=(1, 3, 5), primary_horizon=5,
        neutralize_size=True, neutralize_industry=True,
        size_field="circ_mv", classification_system="SW", industry_level=1,
    )
    base.update(kw)
    return EvalConfig(**base)


class SpyReader(FakeReader):
    """计数 reader:区分全窗口 bars 读与单日截面读(ln_size 每调仓日一次)。"""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.full_bars_reads = 0
        self.bars_reads = 0
        self.universe_reads = 0
        self._window: tuple | None = None

    def arm(self, start, end):
        self._window = (start, end)

    def read_bars_daily(self, symbols=None, start=None, end=None, **kw):
        self.bars_reads += 1
        if self._window and (start, end) == self._window:
            self.full_bars_reads += 1
        return super().read_bars_daily(symbols=symbols, start=start, end=end, **kw)

    def read_universe(self, symbols=None, start=None, end=None, **kw):
        self.universe_reads += 1
        return super().read_universe(symbols=symbols, start=start, end=end, **kw)


class Mom5(Factor):
    category = "momentum"
    direction = 1
    dependencies = ("raw.bars_daily",)
    required_columns = ("close",)          # WP3.1 已声明 → 投影面板

    def compute(self, ctx: FactorContext) -> pl.DataFrame:
        bars = ctx.bars(adjust="post").sort(["symbol", "date"])
        return (
            bars.with_columns(
                (pl.col("close") / pl.col("close").shift(5).over("symbol") - 1.0)
                .alias("value")
            ).select(["date", "symbol", "value"]).drop_nulls("value")
        )


class Mom5NoDecl(Mom5):
    required_columns = ()                  # 未声明 → 回退全列 (legacy 形态)


def _evaluate(reader, factor, cfg):
    ledger = DegradationLedger()
    ev = FactorEvaluator(reader, ledger=ledger)
    out = ev.evaluate(factor, cfg, UNI)
    return out, ledger


# ── T-WP3-001: golden — 投影/缓存路径 vs 全列回退路径 输出逐值一致 ───────────
def test_wp3_001_projected_equals_fullcolumn_golden():
    cfg = _cfg()
    out_p, led_p = _evaluate(FakeReader(), Mom5(), cfg)
    out_f, led_f = _evaluate(FakeReader(), Mom5NoDecl(), cfg)

    assert out_p["summary"] == out_f["summary"]                       # 全部标量
    assert out_p["ic_series"].equals(out_f["ic_series"])              # 序列逐字节
    assert out_p["ic_by_horizon"] == out_f["ic_by_horizon"]
    assert out_p["layers"] == out_f["layers"]
    assert out_p["turnover"] == out_f["turnover"]
    assert out_p["clean_values"].equals(out_f["clean_values"])
    assert out_p["rebalance_dates"] == out_f["rebalance_dates"]
    # 台账事件序列一致 (性能行为不进台账 — 方案 §2.4)
    assert [e.code for e in led_p.events] == [e.code for e in led_f.events]


# ── T-WP3-002: reader spy — 每评估 bars 全量读恰为 1 ─────────────────────────
def test_wp3_002_exactly_one_full_bars_read():
    cfg = _cfg()
    spy = SpyReader()
    spy.arm(cfg.start, cfg.end)
    _evaluate(spy, Mom5(), cfg)
    # 旧实现 = ctx.bars 1 + H(3 个 horizon) + step4 1 = 5 次全窗口读;现在 1 次
    assert spy.full_bars_reads == 1
    # ln_size 的单日截面读不计入全窗口读 (date==start==end)
    assert spy.bars_reads >= 1


def test_wp3_002_pit_factor_caches_none_panel():
    """PIT 因子的 ctx.bars('none') 每调仓日重读 → 缓存后全窗口读折叠为常数。"""
    from quant_factor.builtin.value import BookToPrice

    cfg = _cfg(horizons=(1, 3), primary_horizon=3, neutralize_size=False,
               neutralize_industry=False)
    spy = SpyReader()
    spy.arm(cfg.start, cfg.end)
    _evaluate(spy, BookToPrice(), cfg)
    # post 面板 1 次 + none 面板 1 次 (缓存命中所有调仓日),与调仓日数无关
    assert spy.full_bars_reads == 2


# ── T-WP3-003: preflight 聚合下推 — 零物化 + 行为回归 ────────────────────────
def test_wp3_003_preflight_zero_materialisation():
    cfg = _cfg()
    spy = SpyReader()
    symbols = spy.symbols
    results, policy = evaluate_checks(spy, cfg, UNI, symbols, cfg.start, cfg.end,
                                      include_pass=True)
    assert spy.bars_reads == 0, "覆盖率检查必须聚合下推,不得物化 bars"
    assert spy.universe_reads == 0, "universe 检查必须聚合下推"
    by_id = {r.check_id: r for r in results}
    # 完整体检清单照常产出 (含通过项),口径与阈值不变
    for cid in ("PF_NEU_IND", "PF_NEU_SIZE", "PF_RET_PRICE", "PF_UNI_FLAG",
                "PF_UNI_ST", "PF_UNI_SUSPEND", "PF_PREP_METHOD", "PF_WINDOW"):
        assert cid in by_id
    assert by_id["PF_NEU_SIZE"].status == "pass"
    assert by_id["PF_NEU_SIZE"].info["coverage"] == 1.0
    assert by_id["PF_WINDOW"].status == "pass"


def test_wp3_003_preflight_block_and_degrade_semantics():
    # 缺市值列 ⇒ strict 阻断 (聚合路径下 ColumnNotInView → 覆盖率 0,语义同物化路径)
    from quant_factor.preflight import PreflightBlocked

    r = FakeReader(with_circ_mv=False, with_total_mv=False)
    cfg = _cfg()
    with pytest.raises(PreflightBlocked) as ei:
        run_preflight(r, cfg, UNI, r.symbols, cfg.start, cfg.end, profile="strict")
    assert any(res.check_id == "PF_NEU_SIZE" for res in ei.value.results)
    # permissive ⇒ 记台账放行
    _, ledger = run_preflight(r, cfg, UNI, r.symbols, cfg.start, cfg.end,
                              profile="permissive")
    assert ledger.has_code("PF_NEU_SIZE")
    # 窗口过短 ⇒ PF_WINDOW 仍 block (聚合 distinct-date 路径)
    r2 = FakeReader(n_days=3)
    with pytest.raises(PreflightBlocked):
        run_preflight(r2, _cfg(end=dt.date(2024, 1, 4)), UNI, r2.symbols,
                      dt.date(2024, 1, 2), dt.date(2024, 1, 4))


# ── forward_returns 面板派生等价 (WP3.2) ────────────────────────────────────
def test_wp3_forward_returns_projected_panel_equivalence():
    r = FakeReader()
    full_panel = pl.from_arrow(r.read_bars_daily(adjust="post"))
    thin_panel = pl.from_arrow(r.read_bars_daily(adjust="post", columns=["close"]))
    for h in (1, 5):
        a = forward_returns(full_panel, h, "close")
        b = forward_returns(thin_panel, h, "close")
        assert a.equals(b)


def test_wp3_price_columns_map():
    assert price_columns("close") == {"close"}
    assert price_columns("vwap") == {"vwap", "close"}
    assert price_columns("open_next") == {"open", "close"}


def test_wp3_missing_projected_column_falls_back_like_legacy():
    """投影列缺失 (vwap 未入库) ⇒ 读取层剔列重试,DG_PRICE_FALLBACK 照旧入台账。"""
    cfg = _cfg(return_price="vwap", neutralize_size=False, neutralize_industry=False)
    out, ledger = _evaluate(FakeReader(with_vwap=False), Mom5(), cfg)
    assert ledger.has_code("DG_PRICE_FALLBACK")
    # 对照:vwap 在场则不降级
    out2, ledger2 = _evaluate(FakeReader(), Mom5(), cfg)
    assert not ledger2.has_code("DG_PRICE_FALLBACK")


def test_wp3_preflight_size_field_sql_injection_blocked():
    """安全审查 P1: size_field 流入聚合下推 SQL,必须只接受列标识符。
    WP3.3 把 polars 列访问改为 SQL 文本后,恶意 size_field 不得拼进引擎。"""
    from quant_factor.preflight import PreflightContext, _safe_column

    # 首道防线:_safe_column 拒绝非标识符
    for bad in ("circ_mv; DROP TABLE x", "x OR 1=1", "a/**/b", "1; SELECT", ""):
        with pytest.raises(ValueError, match="非法列名|列标识符"):
            _safe_column(bad)
    assert _safe_column("circ_mv") == "circ_mv"
    assert _safe_column("total_mv") == "total_mv"

    # 端到端:恶意 size_field 经 PreflightContext 不会执行注入(转化为 ValueError)
    r = FakeReader()
    ctx = PreflightContext(r, _cfg(), UNI, r.symbols, _cfg().start, _cfg().end)
    with pytest.raises(ValueError):
        ctx.bars_field_coverage("circ_mv); DROP", positive=True)
    # 合法字段照常工作
    cov, n, n_have, _ = ctx.bars_field_coverage("circ_mv", positive=True)
    assert n_have == n and cov == 1.0


def test_wp3_evalconfig_rejects_bad_size_field():
    """安全审查 P1 第二道防线: 请求体边界(from_dict)拒绝白名单外 size_field。"""
    from quant_factor import EvalConfig

    with pytest.raises(ValueError, match="size_field"):
        EvalConfig.from_dict({"size_field": "circ_mv; DROP TABLE bars"})
    with pytest.raises(ValueError, match="size_field"):
        EvalConfig.from_dict({"size_field": "close"})   # 真实列但非市值口径,也拒
    # 白名单内放行
    assert EvalConfig.from_dict({"size_field": "total_mv"}).size_field == "total_mv"
    assert EvalConfig.from_dict({}).size_field == "circ_mv"  # 默认值不受影响


def test_wp3_builtin_factors_declare_required_columns():
    from quant_factor.builtin.momentum import Mom, Reversal, TurnoverN
    from quant_factor.builtin.size import LogMktCap
    from quant_factor.builtin.value import BookToPrice
    from quant_factor.builtin.volatility import Volatility

    assert Mom.required_columns == ("close",)
    assert Reversal.required_columns == ("close",)
    assert TurnoverN.required_columns == ("volume", "float_share")
    assert LogMktCap.required_columns == ("circ_mv", "total_mv")
    assert BookToPrice.required_columns == ("close",)
    assert Volatility.required_columns == ("close",)
