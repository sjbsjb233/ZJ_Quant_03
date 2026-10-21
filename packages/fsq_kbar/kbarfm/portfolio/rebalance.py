"""调仓执行（报告6.2）.

目标权重 -> 交易指令：先卖后买（释放资金）、按手取整（A股100股
一手）、成本前置计提；单边换手率统计供图6.2口径复核。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OrderInstruction:
    symbol: str
    side: str            # "sell" / "buy"
    target_weight: float
    delta_value: float   # 需要交易的市值变化（正为买入）


def orders_from_targets(
    current_weights: dict[str, float],
    target_weights: dict[str, float],
    total_value: float,
    prices: dict[str, float],
    lot_size: int = 100,
    min_trade_value: float = 1000.0,
) -> list[OrderInstruction]:
    """生成先卖后买的指令序列.

    current_weights/target_weights: symbol -> 权重；prices: symbol -> 现价。
    低于 min_trade_value 的微小调整跳过，避免碎单摩擦。
    """
    symbols = set(current_weights) | set(target_weights)
    sells: list[OrderInstruction] = []
    buys: list[OrderInstruction] = []
    for s in symbols:
        cur = current_weights.get(s, 0.0)
        tgt = target_weights.get(s, 0.0)
        delta_value = (tgt - cur) * total_value
        if abs(delta_value) < min_trade_value:
            continue
        price = prices.get(s, 0.0)
        if price > 0 and lot_size > 0:
            shares = int(abs(delta_value) / price / lot_size) * lot_size
            if shares == 0 and tgt > 0:
                continue
            delta_value = float(shares * price) * (1 if delta_value > 0 else -1)
            if abs(delta_value) < min_trade_value and tgt > 0:
                continue
        instr = OrderInstruction(
            symbol=s,
            side="buy" if delta_value > 0 else "sell",
            target_weight=tgt,
            delta_value=delta_value,
        )
        (buys if delta_value > 0 else sells).append(instr)

    # 先卖后买；卖出按金额降序（尽快释放资金），买入按金额降序
    sells.sort(key=lambda o: o.delta_value)
    buys.sort(key=lambda o: -o.delta_value)
    return sells + buys


def turnover_oneside(orders: list[OrderInstruction], total_value: float) -> float:
    """单边换手率 = max(买入额, 卖出额) / 组合总值（图6.2口径）."""
    if total_value <= 0:
        return 0.0
    buy_value = sum(o.delta_value for o in orders if o.delta_value > 0)
    sell_value = -sum(o.delta_value for o in orders if o.delta_value < 0)
    return max(buy_value, sell_value) / total_value
