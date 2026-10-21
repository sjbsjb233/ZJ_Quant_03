"""组合回测执行（7.1 / 7.2，图7.1）.

回测在Qlib标准化回测引擎中执行：数据单向流动，任一时点因子仅用
该时点及之前的行情；交易按信号生成后的可成交价格撮合，计提单边
0.15%成本，一字涨跌停不可成交。

两遍式风控回测（与实盘在线状态机逐日等价的离线实现）：
  第一遍：仅因子信号 + 组合约束，得到原始净值；
  由第一遍净值推演回撤三级警戒状态机（表5.3），得到逐日目标
  风险度、冻结开仓日与强制处置日；因子有效性监控（5.2）叠加
  防御模式冻结日；
  第二遍：带风控状态重跑回测，再按表5.4阶梯叠加股指期货对冲。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from qlib.contrib.evaluate import backtest_daily

from ..data.qlib_source import init_qlib
from ..portfolio.strategy import KBarTopkStrategy
from ..risk.drawdown import DrawdownGuard
from ..risk.factor_monitor import FactorGuard, rolling_rank_ic
from ..risk.hedging import hedge_ratio_series, hedged_returns
from ..utils.logging import get_logger
from .metrics import performance_summary

logger = get_logger("kbarfm.backtest")


def run_backtest(
    cfg,
    signal: pd.DataFrame,
    portfolio_overrides: dict | None = None,
    risk_degree_series: pd.Series | None = None,
    freeze_dates: set | None = None,
    force_dates: set | None = None,
) -> tuple[pd.DataFrame, dict]:
    """单遍Qlib回测，返回(日频报告report_df, 逐日持仓positions)."""
    p = dict(cfg.portfolio)
    if portfolio_overrides:
        p.update(portfolio_overrides)

    strategy = KBarTopkStrategy(
        signal=signal,
        topk=p["topk"],
        n_drop=p["n_drop"],
        hold_thresh=p["hold_thresh"],          # 最短持有期5个交易日（6.2.1）
        only_tradable=True,                    # 可交易性复核（6.1.1）
        forbid_all_trade_at_limit=True,
        rebalance_freq=p["rebalance_freq"],
        default_risk_degree=p["risk_degree_normal"],
        risk_degree_series=risk_degree_series,
        freeze_dates=freeze_dates,
        force_dates=force_dates,
    )
    report, positions = backtest_daily(
        start_time=cfg.start,
        end_time=cfg.end,
        strategy=strategy,
        account=cfg.account,
        benchmark=cfg.benchmark,
        exchange_kwargs={
            "freq": "day",
            "deal_price": p["deal_price"],
            "open_cost": p["open_cost"],       # 单边0.15%（表7.2）
            "close_cost": p["close_cost"],
            "min_cost": p["min_cost"],
            "limit_threshold": p["limit_threshold"],
        },
    )
    return report, positions


def run_two_pass(
    cfg,
    signal: pd.DataFrame,
    vol_breadth: pd.Series | None = None,
    crowd_share: pd.Series | None = None,
) -> dict:
    """两遍式风控回测 + 对冲叠加，返回全套结果."""
    init_qlib(cfg.provider_uri, cfg.region)
    rk = dict(cfg.risk)

    # ---- 第一遍：无状态风控 ----
    logger.info("第一遍回测（仅因子 + 组合约束）...")
    report1, _pos1 = run_backtest(cfg, signal)
    nav1 = (1.0 + report1["return"].fillna(0.0)).cumprod()

    # ---- 回撤三级警戒状态机（表5.3）----
    guard = DrawdownGuard(
        levels=tuple(rk["dd_levels"]),
        exposures=tuple(rk["dd_exposure"]),
        release_dd=rk["dd_release"],
        release_days=rk["release_days"],
    )
    dd_state = guard.run(nav1)
    force_dates = set(dd_state.index[dd_state["level"].diff().fillna(0) != 0])
    freeze_dates = set(dd_state.index[dd_state["freeze_new"]])

    # ---- 因子有效性监控 -> 防御模式（5.2 / 6.2.2）----
    fwd1 = _forward_return_series(cfg, horizon=1)
    ric20 = rolling_rank_ic(signal["score"], fwd1, window=rk["ric_window"])
    defense = FactorGuard(rk["ric_fail"], rk["ric_recover"], rk["ric_days"]).run(ric20)
    freeze_dates |= set(defense.index[defense])

    # ---- 第二遍：带风控状态 ----
    logger.info(
        "第二遍回测（%d个冻结日 / %d个强制处置日）...",
        len(freeze_dates), len(force_dates),
    )
    report2, positions = run_backtest(
        cfg,
        signal,
        risk_degree_series=dd_state["exposure"],
        freeze_dates=freeze_dates,
        force_dates=force_dates,
    )

    # ---- 分级动态对冲叠加（表5.4）----
    ratio = hedge_ratio_series(
        dd_state["level"],
        vol_breadth=vol_breadth,
        crowd_share=crowd_share,
        ladder=tuple(rk["hedge_ladder"]),
        vol_breadth_thresh=rk["vol_breadth"],
        crowd_share_thresh=rk["crowd_hold_share"],
        release_days=rk["release_days"],
    )
    hedged = hedged_returns(report2["return"], report2["bench"], ratio)
    report2 = report2.copy()
    report2["hedge_ratio"] = ratio.reindex(report2.index).fillna(0.0)
    report2["hedged_return"] = hedged

    summary = performance_summary(report2, risk_free=cfg.risk_free)
    return {
        "report": report2,
        "positions": positions,
        "dd_state": dd_state,
        "defense": defense,
        "hedge_ratio": ratio,
        "summary": summary,
    }


def _forward_return_series(cfg, horizon: int = 1) -> pd.Series:
    """标的池未来horizon日收益（用于滚动RankIC监控），Qlib表达式实现."""
    from qlib.data import D

    from ..data.qlib_source import get_pool_instruments

    instruments = get_pool_instruments(cfg.pool, cfg.start, cfg.end)
    expr = f"Ref($close, -{horizon}) / $close - 1"
    df = D.features(instruments, [expr], start_time=cfg.start, end_time=cfg.end, freq="day")
    s = df.iloc[:, 0]
    s.index = s.index.swaplevel()  # -> (datetime, instrument)
    return s.sort_index().rename("fwd_ret")


def save_results(result: dict, out_dir: str | Path) -> None:
    import pickle

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    result["report"].to_parquet(out / "report.parquet")
    result["dd_state"].to_parquet(out / "dd_state.parquet")
    pd.DataFrame({"defense": result["defense"]}).to_parquet(out / "defense.parquet")
    pd.Series(result["summary"]).to_csv(out / "summary.csv", header=False)
    with open(out / "positions.pkl", "wb") as f:   # 逐日持仓（行业归因用，7.5.3）
        pickle.dump(result["positions"], f)
    logger.info("回测结果 -> %s", out)
