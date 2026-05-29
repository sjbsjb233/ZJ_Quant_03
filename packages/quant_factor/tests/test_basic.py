"""Light smoke tests for quant_factor — verify imports + builtin discovery
+ pure-function primitives. Does not exercise DataReader (that is tested
end-to-end through the factor service)."""
from __future__ import annotations

import polars as pl


def test_imports_clean():
    import quant_factor

    assert quant_factor.__version__.startswith("0.1")
    assert hasattr(quant_factor, "FactorEvaluator")
    assert hasattr(quant_factor, "correlation_matrix")
    assert hasattr(quant_factor, "EvalConfig")


def test_registry_discovers_builtins():
    from quant_factor.registry import discover_builtins

    builtins = discover_builtins()
    # We registered Mom, Reversal, TurnoverN, LogMktCap, Volatility, BookToPrice
    assert "mom" in builtins
    assert "reversal" in builtins
    assert "logmktcap" in builtins
    assert "volatility" in builtins
    assert "booktoprice" in builtins


def test_preset_universes_listed():
    from quant_factor.universe import PRESET_UNIVERSES, PRESET_FLAG

    ids = {p["universe_id"] for p in PRESET_UNIVERSES}
    assert "preset:all" in ids
    assert "preset:hs300" in ids
    assert "preset:zz800" in ids  # design §9.1
    assert "preset:zz500" in ids
    assert PRESET_FLAG["preset:all"] is None
    assert PRESET_FLAG["preset:zz800"] == ("in_hs300", "in_zz500")


def test_winsorize_and_zscore():
    from quant_factor.preprocess import winsorize_mad, zscore

    s = pl.Series("x", [1.0, 2.0, 3.0, 4.0, 5.0, 100.0])
    w = winsorize_mad(s, n=2.0)
    # extreme value should be pulled in
    assert w.to_list()[-1] < 100.0
    z = zscore(s)
    arr = z.to_numpy()
    # roughly mean zero
    assert abs(arr.mean()) < 1e-9


def test_eval_config_roundtrip():
    from quant_factor.config import EvalConfig
    import datetime as dt

    cfg = EvalConfig(
        universe_id="preset:hs300",
        start=dt.date(2024, 1, 1),
        end=dt.date(2024, 12, 31),
        horizons=(1, 5, 10),
        primary_horizon=5,
        size_field="circ_mv",
        classification_system="SW",
        industry_level=2,
    )
    d = cfg.to_jsonable()
    assert d["start"] == "2024-01-01"
    assert d["horizons"] == [1, 5, 10]
    cfg2 = EvalConfig.from_dict(d)
    assert cfg2.start == dt.date(2024, 1, 1)
    assert cfg2.industry_level == 2
    assert cfg2.classification_system == "SW"


def test_validate_factor_smoke():
    from quant_factor.validate import validate_factor

    src = '''
import polars as pl
from quant_factor.base import Factor

class MyMom(Factor):
    category = "momentum"
    direction = 1
    requires_pit = False
    dependencies = ("raw.bars_daily",)
    param_schema = {"window": (int, 20)}

    def compute(self, ctx):
        bars = ctx.bars(adjust="post").sort(["symbol", "date"])
        w = self.params["window"]
        return (
            bars.with_columns(
                (pl.col("close") / pl.col("close").shift(w).over("symbol") - 1.0).alias("value")
            )
            .select(["date", "symbol", "value"])
            .drop_nulls("value")
        )
'''
    rep = validate_factor(src)
    assert rep["class_name"] == "MyMom"
    assert any(c["id"] == "schema" and c["status"] == "pass" for c in rep["checks"])
    assert rep["param_schema"]["window"]["type"] == "int"


def test_industry_rollup_handles_missing():
    """The industry mapping should return None rather than crash when codes
    don't appear in sectors. We can't easily test against a real reader
    here, so just confirm the entry point exists and accepts the right args."""
    from quant_factor.neutralize import industry_at_level

    assert callable(industry_at_level)
