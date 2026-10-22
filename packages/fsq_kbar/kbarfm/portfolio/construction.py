"""初始建仓与组合构建（报告6.1，表3.1 / 表6.2）.

- 横截面信号排序 + top-k精选：沪深300池 k=50 / n=5，
  中证800池 k=200 / n=10；
- 两道复核：可交易性复核（剔除停牌、一字涨跌停、ST）；
  波动率风险预算（预测波动率截面前20%分位降权、极端者剔除）；
- 等权配置：入选k只股票等权分配，波动率降权标的权重减半后
  整体归一；
- drop-n约束与最短持有期（6.2.1）：每期最多买入/卖出n只，任一
  标的买入后满5个交易日前不因排序回落卖出；
- 现金留存三档规则（表6.2）：常态3% / 风险升温10% / 危机防守
  由剩余权益仓位倒推。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..risk.drawdown import AlertLevel


@dataclass(frozen=True)
class PoolParams:
    """标的池组合参数（表3.1）."""

    index_code: str
    top_k: int
    drop_n: int
    min_holding_days: int = 5
    rebalance_every: int = 5           # 每5个交易日调仓（表7.2）
    vol_downweight_pct: float = 0.80   # 预测波动率截面前20%分位降权
    vol_exclude_pct: float = 0.97      # 极端突出者（前3%）直接剔除
    vol_downweight_mult: float = 0.5


POOL_PRESETS: dict[str, PoolParams] = {
    "hs300": PoolParams(index_code="000300.SH", top_k=50, drop_n=5),
    "csi800": PoolParams(index_code="000906.SH", top_k=200, drop_n=10),
}


class CashManager:
    """现金留存三档规则（表6.2）."""

    NORMAL_CASH = 0.03
    RISK_CASH = 0.10
    RELEASE_CONSECUTIVE_DAYS = 3

    def __init__(self) -> None:
        self.cash_ratio = self.NORMAL_CASH
        self._calm_count = 0

    def update(
        self,
        dd_level: AlertLevel,
        vol_alert: bool,
        crowded_share: float,
        equity_scale: float,
    ) -> float:
        """返回目标现金比例；危机档由权益目标仓位倒推."""
        risk_on = dd_level >= AlertLevel.L2_DERISK or vol_alert or crowded_share > 0.20
        if dd_level >= AlertLevel.L3_DEFENSE:
            self.cash_ratio = max(1.0 - equity_scale, self.RISK_CASH)   # 约70%-80%
            self._calm_count = 0
        elif risk_on:
            self.cash_ratio = self.RISK_CASH
            self._calm_count = 0
        else:
            self._calm_count += 1
            if self._calm_count >= self.RELEASE_CONSECUTIVE_DAYS:
                self.cash_ratio = self.NORMAL_CASH
        return self.cash_ratio


@dataclass
class TargetPortfolio:
    weights: dict[str, float] = field(default_factory=dict)   # symbol -> 目标权重
    buys: list[str] = field(default_factory=list)
    sells: list[str] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)


class PortfolioBuilder:
    def __init__(self, params: PoolParams) -> None:
        self.params = params

    # ------------------------------------------------------------ 波动率预算
    def vol_budget(self, vol_pred: pd.Series) -> tuple[set, dict[str, float]]:
        """预测波动率横截面排序：前20%分位降权、极端突出者剔除（5.1节）.

        返回 (剔除集合, symbol -> 权重乘数)。
        """
        vol = vol_pred.dropna()
        if vol.empty:
            return set(), {}
        pct = vol.rank(pct=True)
        excluded = set(pct[pct >= self.params.vol_exclude_pct].index)
        mults = {
            s: self.params.vol_downweight_mult
            for s in pct[(pct >= self.params.vol_downweight_pct) & (pct < self.params.vol_exclude_pct)].index
        }
        return excluded, mults

    # ------------------------------------------------------------ 目标持仓
    def build_targets(
        self,
        scores: pd.Series,
        vol_pred: pd.Series,
        tradable: set,
        current_holdings: set,
        holding_age: dict[str, int],
        crowded: set | None = None,
        equity_ratio: float = 0.97,
        pause_new_buys: bool = False,
    ) -> TargetPortfolio:
        """由因子打分构建目标组合.

        scores          当日横截面因子（z值），index为symbol；
        vol_pred        当日预测波动率（与scores同池）；
        tradable        可交易复核通过的标的集合；
        current_holdings当前持仓集合；
        holding_age     持仓天数（最短持有期约束用）；
        crowded         当日判定拥挤的标的集合（个股层面处置）；
        equity_ratio    目标权益比例 = (1 - 现金档位) × 警戒降仓系数；
        pause_new_buys  一级及以上警戒时暂停新建仓位（表5.3）。
        """
        p = self.params
        crowded = crowded or set()

        excluded_vol, vol_mults = self.vol_budget(vol_pred)
        # 候选 = 打分有效 ∩ 可交易 - 波动率剔除 - 拥挤剔除
        candidates = scores.dropna()
        candidates = candidates[candidates.index.isin(tradable)]
        candidates = candidates[~candidates.index.isin(excluded_vol)]
        candidates = candidates[~candidates.index.isin(crowded)]
        ranked = candidates.sort_values(ascending=False)
        ideal_top = list(ranked.index[: p.top_k])

        # ---- 卖出侧：跌出top-k且满足最短持有期的持仓，按排名劣化幅度取前n
        rank_map = {s: i for i, s in enumerate(ranked.index)}
        sell_pool = []
        for s in current_holdings:
            if s in ideal_top:
                continue
            age = holding_age.get(s, 0)
            forced = (s in excluded_vol) or (s in crowded)   # 风险处置不受持有期约束
            if age < p.min_holding_days and not forced:
                continue                       # 最短持有期：排序回落不卖，风险处置除外
            rank = rank_map.get(s, len(ranked) + p.top_k)
            sell_pool.append((rank, s, forced))
        # 强制处置（波动率剔除/拥挤）优先，其余按排名劣化幅度降序
        sell_pool.sort(key=lambda t: (not t[2], -t[0]))
        forced_sells = [s for _, s, f in sell_pool if f]
        optional_sells = [s for _, s, f in sell_pool if not f][: p.drop_n]
        sells = forced_sells + optional_sells

        # ---- 买入侧：drop-n约束下按排名最优补入
        # 存量中未被卖出的全部保留（含因临时停牌暂不在候选中的标的）
        keep = current_holdings - set(sells)
        buys: list[str] = []
        if not pause_new_buys:
            slots = max(p.top_k - len(keep), 0)
            max_buys = min(p.drop_n, slots)
            for s in ideal_top:
                if len(buys) >= max_buys:
                    break
                if s not in keep and s not in current_holdings:
                    buys.append(s)

        final_holdings = list((keep | set(buys)))

        # ---- 等权配置 + 波动率降权 + 整体归一到目标权益比例（6.1.2）
        if final_holdings:
            base = np.array([vol_mults.get(s, 1.0) for s in final_holdings], dtype=float)
            base = base / base.sum() * equity_ratio
            weights = {s: float(w) for s, w in zip(final_holdings, base)}
        else:
            weights = {}

        return TargetPortfolio(
            weights=weights,
            buys=buys,
            sells=sells,
            diagnostics={
                "n_candidates": int(len(ranked)),
                "n_holdings": len(final_holdings),
                "n_vol_excluded": len(excluded_vol),
                "n_vol_downweighted": len(vol_mults),
                "n_crowded": len(crowded),
                "equity_ratio": equity_ratio,
                "pause_new_buys": pause_new_buys,
            },
        )
