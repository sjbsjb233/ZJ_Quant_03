"""Adjustment factors derived from corporate actions (doc §6.3 / §9.9).

The design leans toward a single source of truth: the pre-adjustment factor is
computed from corporate_actions + daily close rather than carried per source, so
fixing one corporate_actions row re-derives every bar's adjusted price while the
raw bars never change. This module provides that computation. The result can be
joined at read time or written back into bars.adj_factor via a corrective batch.

Pre-adjustment convention: the most recent bar is unadjusted (factor = 1.0); the
factor for earlier bars is the cumulative product of per-event ratios.
"""
from __future__ import annotations

import bisect

import pyarrow as pa

from .reader import DataReader

# action types that move the price on the ex-date
_PRICE_AFFECTING = {"cash_dividend", "stock_dividend", "transfer", "rights_issue", "split"}


def _event_ratio(prev_close: float, ev: dict) -> float:
    """Ex-date price ratio: adjusted_prev_close / prev_close (<= 1 for typical events)."""
    cash = ev.get("cash_per_share") or 0.0
    send = (ev.get("stock_per_10") or 0.0) / 10.0
    transfer = (ev.get("transfer_per_10") or 0.0) / 10.0
    rights = ev.get("rights_ratio") or 0.0
    rights_price = ev.get("rights_price") or 0.0
    split = ev.get("split_ratio") or 1.0

    denom = (1.0 + send + transfer + rights) * (split if split else 1.0)
    if denom <= 0 or prev_close in (None, 0):
        return 1.0
    adj_prev = (prev_close - cash + rights * rights_price) / denom
    if adj_prev <= 0:
        return 1.0
    return adj_prev / prev_close


def compute_adj_factors(symbols=None, reader: DataReader | None = None, root=None) -> pa.Table:
    """Return (symbol, date, adj_factor) pre-adjustment factors.

    factor[d] = product of event ratios for every price-affecting action with
    ex-date strictly after d, so the latest available bar has factor 1.0.
    """
    reader = reader or DataReader(root=root)
    bars = reader.read_bars_daily(symbols=symbols, adjust="none").to_pylist()
    actions = reader.current_view("raw.corporate_actions").to_pylist()

    close_by_symbol: dict[str, dict] = {}
    dates_by_symbol: dict[str, list] = {}
    for r in bars:
        close_by_symbol.setdefault(r["symbol"], {})[r["date"]] = r.get("close")
    for sym, cmap in close_by_symbol.items():
        dates_by_symbol[sym] = sorted(cmap)

    events_by_symbol: dict[str, list] = {}
    for a in actions:
        if a["action_type"] in _PRICE_AFFECTING:
            events_by_symbol.setdefault(a["symbol"], []).append(a)

    out_symbol, out_date, out_factor = [], [], []
    for sym, dates in dates_by_symbol.items():
        cmap = close_by_symbol[sym]
        events = sorted(events_by_symbol.get(sym, []), key=lambda e: e["action_date"])
        # ratio attached to each event's ex-date
        event_points = []  # (ex_date, ratio)
        for ev in events:
            i = bisect.bisect_left(dates, ev["action_date"]) - 1
            if i < 0:
                continue
            prev_close = cmap[dates[i]]
            event_points.append((ev["action_date"], _event_ratio(prev_close, ev)))

        # factor[d] = product of ratios for ex_date > d  (walk from latest backwards)
        factor = 1.0
        ep_idx = len(event_points) - 1
        ex_dates_sorted = [p[0] for p in event_points]
        for d in reversed(dates):
            # fold in any event whose ex-date is strictly greater than d
            while ep_idx >= 0 and ex_dates_sorted[ep_idx] > d:
                factor *= event_points[ep_idx][1]
                ep_idx -= 1
            out_symbol.append(sym)
            out_date.append(d)
            out_factor.append(factor)

    return pa.table(
        {
            "symbol": pa.array(out_symbol, pa.string()),
            "date": pa.array(out_date, pa.date32()),
            "adj_factor": pa.array(out_factor, pa.float64()),
        }
    )
