"""Adjustment factors derived from corporate actions (doc §6.3 / §9.9).

The design leans toward a single source of truth: the pre-adjustment factor is
computed from corporate_actions + daily close rather than carried per source, so
fixing one corporate_actions row re-derives every bar's adjusted price while the
raw bars never change. This module provides that computation. The result can be
joined at read time or written back into bars.adj_factor via a corrective batch.

Pre-adjustment convention: the most recent bar is unadjusted (factor = 1.0); the
factor for earlier bars is the cumulative product of per-event ratios.

内存治理 (《内存治理总体方案》WP1.1): the legacy implementation read the WHOLE
bars table with no date filter and ``.to_pylist()``-ed it into Python dicts
(~1.5–2KB/row ⇒ ~8GB on prod, the web-container P0). The computation is now
split so bars NEVER leave the engine as Python objects:

  ① prev_close per event  — one in-engine pass over the bars current view
     (only ``symbol/date/close`` are scanned), output = one row per event;
  ② per-event ratio + per-interval cumulative factor — pure Python over the
     tiny event list, using the *exact legacy fold order* so the output floats
     are bit-identical to the historical implementation (no log/exp detour);
  ③ broadcast intervals onto (symbol, date) — one in-engine interval join,
     materialising nothing beyond the 3-column result table itself.
"""
from __future__ import annotations

import bisect

import pyarrow as pa

from .logging import get_logger
from .reader import DataReader, _in_clause, _to_date

_log = get_logger("quant_data.corporate")

# action types that move the price on the ex-date
_PRICE_AFFECTING = {"cash_dividend", "stock_dividend", "transfer", "rights_issue", "split"}

_BARS_TABLE = "raw.bars_daily"


def _event_invalid_reason(ev: dict, last_bar_date) -> str | None:
    """脏事件检测(内存治理验收 T-WP1-001b — 不得静默算出错值)。

    quality 层只警告不拒写,脏行能进湖;读侧旧实现把它们"按公式硬算"——
    比如负现金分红会得出 >1 的因子(数值上有效、业务上荒谬)。这里把非法
    事件显式无效化(跳过,数值上等价于 ratio=1.0)并发结构化台账
    ``corporate.event.invalid``,语义 = 方案约定的"跳过 + 台账"。
    """
    if last_bar_date is not None and ev["action_date"] > last_bar_date:
        return "ex_date_beyond_last_bar"   # 未来事件会折扣全部历史 bar,违反末日=1.0 约定
    for fld in ("cash_per_share", "stock_per_10", "transfer_per_10",
                "rights_ratio", "rights_price"):
        v = ev.get(fld)
        if v is not None and v < 0:
            return f"negative_{fld}"
    split = ev.get("split_ratio")
    if split is not None and split <= 0:
        return "non_positive_split_ratio"  # 0/负拆股 = 无意义,旧实现静默当 1.0
    return None


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


def _empty_factors() -> pa.Table:
    return pa.table(
        {
            "symbol": pa.array([], pa.string()),
            "date": pa.array([], pa.date32()),
            "adj_factor": pa.array([], pa.float64()),
        }
    )


def _price_events(reader: DataReader, symbols) -> dict[str, list[dict]]:
    """Price-affecting corporate actions grouped per symbol (small table)."""
    actions = reader.current_view("raw.corporate_actions").to_pylist()
    sym_set = set(symbols) if symbols is not None else None
    out: dict[str, list[dict]] = {}
    for a in actions:
        if a["action_type"] not in _PRICE_AFFECTING:
            continue
        if sym_set is not None and a["symbol"] not in sym_set:
            continue
        out.setdefault(a["symbol"], []).append(a)
    return out


def _last_bar_dates(reader: DataReader, symbols: list[str]) -> dict:
    """每个事件票的末 bar 日(一次聚合;事件票集是小集合)。"""
    if not symbols:
        return {}
    quoted = ",".join("'" + s.replace("'", "''") + "'" for s in symbols)
    cur = reader.aggregate_current_view(
        _BARS_TABLE, projection="symbol, MAX(date)",
        tail="GROUP BY symbol",
        filters=[f"t.symbol IN ({quoted})"],
    )
    return {s: _to_date(d) for s, d in cur.fetchall()}


def _prev_closes(reader: DataReader, events_by_symbol, bar_filters) -> dict:
    """close of the last bar STRICTLY before each event's ex-date — in-engine.

    Output is one row per distinct (symbol, ex_date); a pair with no bar before
    the ex-date is simply absent (the legacy ``bisect ... i < 0 → skip``).
    NULL closes are kept (legacy looked the close up by date, NULL included).
    """
    pairs = sorted(
        {(a["symbol"], a["action_date"]) for evs in events_by_symbol.values() for a in evs}
    )
    if not pairs:
        return {}
    reader._con.register(
        "_caf_events",
        pa.table(
            {
                "s": pa.array([p[0] for p in pairs], pa.string()),
                "ad": pa.array([p[1] for p in pairs], pa.date32()),
            }
        ),
    )
    cur = reader.aggregate_current_view(
        _BARS_TABLE,
        projection="ev.s AS symbol, ev.ad AS action_date, close AS prev_close",
        tail=(
            "JOIN _caf_events ev ON symbol = ev.s AND date < ev.ad "
            "QUALIFY ROW_NUMBER() OVER (PARTITION BY ev.s, ev.ad ORDER BY date DESC) = 1"
        ),
        filters=bar_filters or None,
    )
    return {(s, _to_date(ad)): pc for s, ad, pc in cur.fetchall()}


def _drop_invalid_events(events_by_symbol, last_bar):
    """非法事件:跳过 + ``corporate.event.invalid`` 台账(T-WP1-001b)。

    数值语义与"ratio=1.0"等价(乘 1 = 不乘),但负参数等旧实现会硬算出
    错值的形态在这里被显式拦下;每个被拦事件都有结构化告警,不静默。
    """
    out: dict[str, list[dict]] = {}
    for sym, evs in events_by_symbol.items():
        kept = []
        for ev in evs:
            reason = _event_invalid_reason(ev, last_bar.get(sym))
            if reason is not None:
                _log.warn("corporate.event.invalid", symbol=sym,
                          action_date=str(ev["action_date"]),
                          action_type=ev["action_type"], reason=reason)
                continue
            kept.append(ev)
        if kept:
            out[sym] = kept
    return out


def _cumulative_intervals(events_by_symbol, prev_close):
    """Per-symbol piecewise-constant factor intervals, legacy fold order.

    The factor function d ↦ ∏(ratio | ex_date > d) is constant between event
    dates. For distinct ex-dates x_1 < … < x_k the interval [x_{j-1}, x_j)
    carries the product of ratios of events at x_j..x_k, folded latest-first
    exactly like the legacy backwards walk (multiplication sequence preserved
    ⇒ identical floats); d ≥ x_k falls through to COALESCE(…, 1.0).
    """
    iv_sym: list[str] = []
    iv_lo: list = []
    iv_hi: list = []
    iv_cum: list[float] = []
    for sym, events in events_by_symbol.items():
        events = sorted(events, key=lambda e: e["action_date"])
        points = [
            (ev["action_date"], _event_ratio(prev_close[(sym, ev["action_date"])], ev))
            for ev in events
            if (sym, ev["action_date"]) in prev_close
        ]
        if not points:
            continue
        distinct = sorted({d for d, _ in points})
        cum_by_hi: dict = {}
        factor, i = 1.0, len(points) - 1
        for j in range(len(distinct) - 1, -1, -1):
            hi = distinct[j]
            while i >= 0 and points[i][0] >= hi:
                factor *= points[i][1]
                i -= 1
            cum_by_hi[hi] = factor
        for j, hi in enumerate(distinct):
            iv_sym.append(sym)
            iv_lo.append(distinct[j - 1] if j > 0 else None)
            iv_hi.append(hi)
            iv_cum.append(cum_by_hi[hi])
    return pa.table(
        {
            "s": pa.array(iv_sym, pa.string()),
            "lo": pa.array(iv_lo, pa.date32()),
            "hi": pa.array(iv_hi, pa.date32()),
            "cum": pa.array(iv_cum, pa.float64()),
        }
    )


def compute_adj_factors(symbols=None, reader: DataReader | None = None, root=None) -> pa.Table:
    """Return (symbol, date, adj_factor) pre-adjustment factors.

    factor[d] = product of event ratios for every price-affecting action with
    ex-date strictly after d, so the latest available bar has factor 1.0.

    WP1.1: computed in-engine (see module docstring) — peak Python-heap cost is
    the event list plus the 3-column result; bars are never converted to Python
    objects. Interface and output schema are unchanged; rows come back ordered
    by (symbol, date) ascending (the legacy emitted dates descending — callers
    join/dict by key, order was never part of the contract).
    """
    reader = reader or DataReader(root=root)
    bar_filters = []
    if (c := _in_clause("t.symbol", symbols)):
        bar_filters.append(c)
    # Early-exit keeps the empty-view path off the aggregate SQL (whose empty
    # fallback has no `cv` relation for the joins below to reference).
    if reader.count_current_view(_BARS_TABLE, filters=bar_filters or None) == 0:
        return _empty_factors()

    events_by_symbol = _price_events(reader, symbols)
    last_bar = _last_bar_dates(reader, sorted(events_by_symbol))
    events_by_symbol = _drop_invalid_events(events_by_symbol, last_bar)
    prev_close = _prev_closes(reader, events_by_symbol, bar_filters)
    intervals = _cumulative_intervals(events_by_symbol, prev_close)

    reader._con.register("_caf_iv", intervals)
    cur = reader.aggregate_current_view(
        _BARS_TABLE,
        projection="symbol, date, COALESCE(iv.cum, 1.0) AS adj_factor",
        tail=(
            "LEFT JOIN _caf_iv iv ON symbol = iv.s AND date < iv.hi "
            "AND (iv.lo IS NULL OR date >= iv.lo) ORDER BY symbol, date"
        ),
        filters=bar_filters or None,
    )
    return cur.fetch_arrow_table()


def _compute_adj_factors_legacy(
    symbols=None, reader: DataReader | None = None, root=None
) -> pa.Table:
    """旧实现 — 仅供 golden 对照 (T-WP1-001),验收后删除。

    ⚠ 全表 ``.to_pylist()``,生产规模 ~8GB Python 堆;禁止任何业务调用。
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
