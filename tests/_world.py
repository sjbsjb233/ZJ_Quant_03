"""Deterministic test-data generators (test plan §1).

Builds a coherent "standard world" from real A-share codes with a fixed random
seed (42), plus the trading calendar it implies. Tests ingest this baseline and
then layer pollution/boundary/temporal variants on top. Everything is
reproducible: same seed -> same bytes.
"""
from __future__ import annotations

import datetime as dt
import random

# --- the 10 representative instruments (test plan §1.1) ---------------------
# real codes; attributes chosen to exercise blue-chip / boards / ST / new /
# delisted / cross-market cases.
SYMBOLS = [
    dict(symbol="600519.SH", name="贵州茅台", exchange="SH", board="main",
         sector_code="801120", currency="CNY", list_date=dt.date(2001, 8, 27),
         delist_date=None, is_active=True, st="normal"),
    dict(symbol="000001.SZ", name="平安银行", exchange="SZ", board="main",
         sector_code="801780", currency="CNY", list_date=dt.date(1991, 4, 3),
         delist_date=None, is_active=True, st="normal"),
    dict(symbol="300750.SZ", name="宁德时代", exchange="SZ", board="gem",
         sector_code="801730", currency="CNY", list_date=dt.date(2018, 6, 11),
         delist_date=None, is_active=True, st="normal"),
    dict(symbol="688981.SH", name="中芯国际", exchange="SH", board="star",
         sector_code="801080", currency="CNY", list_date=dt.date(2020, 7, 16),
         delist_date=None, is_active=True, st="normal"),
    dict(symbol="000063.SZ", name="中兴通讯", exchange="SZ", board="main",
         sector_code="801080", currency="CNY", list_date=dt.date(1997, 11, 18),
         delist_date=None, is_active=True, st="normal"),  # had *ST history
    dict(symbol="600446.SH", name="金证股份", exchange="SH", board="main",
         sector_code="801080", currency="CNY", list_date=dt.date(2003, 11, 18),
         delist_date=None, is_active=True, st="normal",
         name_history='[{"name":"金证科技","until":"2007-01-01"}]'),  # renamed
    dict(symbol="601399.SH", name="国机重装", exchange="SH", board="main",
         sector_code="801890", currency="CNY", list_date=dt.date(2011, 1, 18),
         delist_date=None, is_active=True, st="normal"),  # *ST 摘帽样本
    dict(symbol="301536.SZ", name="星宸科技", exchange="SZ", board="gem",
         sector_code="801080", currency="CNY", list_date=dt.date(2024, 2, 1),
         delist_date=None, is_active=True, st="normal"),  # 2024 新股
    dict(symbol="600145.SH", name="退市股样本", exchange="SH", board="main",
         sector_code="801180", currency="CNY", list_date=dt.date(1996, 1, 1),
         delist_date=dt.date(2024, 6, 28), is_active=False, st="delisted"),  # 退市
    dict(symbol="00700.HK", name="腾讯控股", exchange="HK", board="main",
         sector_code=None, currency="HKD", list_date=dt.date(2004, 6, 16),
         delist_date=None, is_active=True, st="normal"),  # 跨市场
]

A_SHARE = [s for s in SYMBOLS if s["exchange"] in ("SH", "SZ")]

# 2024 A-share *market* holiday model. Note: the stock market does NOT open on
# 调休 weekend "make-up workdays" -- those affect offices, not trading. So the
# market calendar is simply "weekday and not a market holiday".
_HOLIDAYS_2024 = {
    dt.date(2024, 1, 1),  # 元旦
    # 春节: market closed 02-09 (除夕) .. 02-17, reopens 02-19
    *[dt.date(2024, 2, d) for d in range(9, 18)],
    dt.date(2024, 4, 4), dt.date(2024, 4, 5),  # 清明
    dt.date(2024, 5, 1), dt.date(2024, 5, 2), dt.date(2024, 5, 3),  # 劳动
    dt.date(2024, 6, 10),  # 端午
    dt.date(2024, 9, 16), dt.date(2024, 9, 17),  # 中秋
    *[dt.date(2024, 10, d) for d in range(1, 8)],  # 国庆
}
# weekend "make-up workdays" (office 调休) -- the market stays CLOSED on these.
_MAKEUP_WORKDAYS_2024 = {dt.date(2024, 2, 4), dt.date(2024, 2, 18)}
_HALF_DAYS: set = set()  # no standard half-days; tested via a custom calendar row


def is_trading_day(d: dt.date) -> bool:
    if d in _HOLIDAYS_2024:
        return False
    return d.weekday() < 5  # Mon-Fri, weekends never trade (incl. make-up days)


def gen_calendar(start: dt.date, end: dt.date, market: str = "CN-A") -> list[dict]:
    days = []
    d = start
    while d <= end:
        days.append(d)
        d += dt.timedelta(days=1)
    trading = [d for d in days if is_trading_day(d)]
    prev_map, next_map = {}, {}
    for i, d in enumerate(trading):
        prev_map[d] = trading[i - 1] if i > 0 else None
        next_map[d] = trading[i + 1] if i < len(trading) - 1 else None
    trading_set = set(trading)

    recs = []
    for d in days:
        td = d in trading_set
        recs.append(dict(
            date=d, market=market, is_trading_day=td,
            prev_trading_day=prev_map.get(d) if td else None,
            next_trading_day=next_map.get(d) if td else None,
            trading_session=("half" if d in _HALF_DAYS else "full") if td else None,
            month_end_trading_day=td and (next_map.get(d) is None or next_map[d].month != d.month),
            quarter_end_trading_day=td and (next_map.get(d) is None
                                            or (next_map[d].month - 1) // 3 != (d.month - 1) // 3),
            year_end_trading_day=td and (next_map.get(d) is None or next_map[d].year != d.year),
            holiday_name=None,
        ))
    return recs


def trading_days(start: dt.date, end: dt.date) -> list[dt.date]:
    d, out = start, []
    while d <= end:
        if is_trading_day(d):
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def gen_bars_daily(symbols, days, seed: int = 42) -> list[dict]:
    rng = random.Random(seed)
    recs = []
    for s in symbols:
        price = 100.0 + rng.random() * 50
        prev_close = price
        for d in days:
            ret = rng.gauss(0, 0.015)
            close = round(max(1.0, prev_close * (1 + ret)), 2)
            high = round(max(close, prev_close) * (1 + abs(rng.gauss(0, 0.004))), 2)
            low = round(min(close, prev_close) * (1 - abs(rng.gauss(0, 0.004))), 2)
            open_ = round(prev_close * (1 + rng.gauss(0, 0.003)), 2)
            open_ = min(max(open_, low), high)
            vol = rng.randint(1_000_00, 5_000_00)
            recs.append(dict(
                date=d, symbol=s, open=open_, high=high, low=low, close=close,
                pre_close=prev_close, volume=vol, amount=round(close * vol, 2),
                vwap=close, adj_factor=1.0,
            ))
            prev_close = close
    return recs


def gen_universe(symbols, days) -> list[dict]:
    hs300 = {"600519.SH", "000001.SZ", "300750.SZ"}
    recs = []
    for s in symbols:
        for i, d in enumerate(days):
            recs.append(dict(
                date=d, symbol=s, is_tradable=True, is_listed=True, is_suspended=False,
                st_status="normal", in_hs300=s in hs300, in_zz500=False,
                in_zz1000=False, in_sse50=s == "600519.SH",
                limit_up_status="normal", listed_days=1000 + i,
            ))
    return recs


def gen_fundamentals(symbols) -> list[dict]:
    periods = [
        (dt.date(2024, 3, 31), "Q1", dt.date(2024, 4, 25)),
        (dt.date(2024, 6, 30), "Q2", dt.date(2024, 8, 28)),
        (dt.date(2024, 9, 30), "Q3", dt.date(2024, 10, 25)),
    ]
    rng = random.Random(42)
    recs = []
    for s in symbols:
        for rp, pt, pub in periods:
            rev = round(rng.uniform(1e8, 1e10), 2)
            recs.append(dict(
                symbol=s, report_period=rp, period_type=pt, publish_date=pub,
                currency="CNY", revenue=rev, net_income=round(rev * 0.1, 2),
                total_assets=round(rev * 5, 2), total_liabilities=round(rev * 2, 2),
                _extras={"roe": f"{rng.uniform(5, 25):.2f}"},
            ))
    return recs


def gen_corporate_actions() -> list[dict]:
    return [
        dict(symbol="600519.SH", action_date=dt.date(2024, 6, 19),
             announce_date=dt.date(2024, 5, 20), action_type="cash_dividend",
             cash_per_share=30.0),
        dict(symbol="300750.SZ", action_date=dt.date(2024, 6, 6),
             announce_date=dt.date(2024, 5, 10), action_type="stock_dividend",
             stock_per_10=5.0),
        dict(symbol="000001.SZ", action_date=dt.date(2024, 6, 13),
             announce_date=dt.date(2024, 5, 15), action_type="transfer",
             transfer_per_10=3.0),
        dict(symbol="000063.SZ", action_date=dt.date(2024, 7, 1),
             announce_date=dt.date(2024, 6, 1), action_type="rights_issue",
             rights_ratio=0.3, rights_price=8.0),
        dict(symbol="600446.SH", action_date=dt.date(2024, 3, 15),
             announce_date=dt.date(2024, 3, 1), action_type="name_change"),
    ]


def gen_symbols() -> list[dict]:
    out = []
    for s in SYMBOLS:
        out.append(dict(
            symbol=s["symbol"], name=s["name"], name_history=s.get("name_history"),
            exchange=s["exchange"], board=s["board"], sector_code=s["sector_code"],
            industry_code=s["sector_code"], currency=s["currency"],
            list_date=s["list_date"], delist_date=s["delist_date"], is_active=s["is_active"],
        ))
    return out


def gen_sectors() -> list[dict]:
    return [
        dict(code="801120", name="食品饮料", level=1, parent_code=None, classification_system="sw"),
        dict(code="801080", name="电子", level=1, parent_code=None, classification_system="sw"),
        dict(code="801780", name="银行", level=1, parent_code=None, classification_system="sw"),
        dict(code="801730", name="电力设备", level=1, parent_code=None, classification_system="sw"),
        dict(code="b10m", name="食品饮料", level=1, parent_code=None, classification_system="citics"),
    ]


def gen_minute(symbol: str, day: dt.date, seed: int = 42) -> list[dict]:
    """A full A-share trading day of 1m bars: 09:30-11:30 + 13:00-15:00 = 240 bars."""
    rng = random.Random(seed)
    recs = []
    price = 100.0
    sessions = [(dt.time(9, 30), 120), (dt.time(13, 0), 120)]
    for start_t, n in sessions:
        base = dt.datetime.combine(day, start_t)
        for i in range(n):
            ts = base + dt.timedelta(minutes=i)
            ret = rng.gauss(0, 0.001)
            close = round(price * (1 + ret), 2)
            recs.append(dict(
                datetime=ts, symbol=symbol, frequency="1m",
                open=round(price, 2), high=round(max(price, close) + 0.02, 2),
                low=round(min(price, close) - 0.02, 2), close=close,
                volume=1000, amount=round(close * 1000, 2), vwap=close, adj_factor=1.0,
            ))
            price = close
    return recs


# --- one-call baseline ingest -----------------------------------------------
def load_standard_world(root, start=dt.date(2024, 1, 1), end=dt.date(2024, 3, 31)):
    """Ingest a small, fully-consistent baseline; returns context for assertions."""
    from quant_data import ingest

    syms = [s["symbol"] for s in A_SHARE if (s["delist_date"] is None or s["delist_date"] > start)]
    days = trading_days(start, end)
    ingest.ingest_symbols(gen_symbols(), root=root)
    ingest.ingest_sectors(gen_sectors(), root=root)
    ingest.ingest_calendar(records=gen_calendar(dt.date(2023, 12, 1), dt.date(2025, 1, 31)),
                           market="CN-A", apply_mapping=False, root=root)
    ingest.ingest_bars_daily(records=gen_bars_daily(syms, days), apply_mapping=False, root=root)
    ingest.ingest_universe(records=gen_universe(syms, days), apply_mapping=False, root=root)
    ingest.ingest_fundamentals(records=gen_fundamentals(syms), apply_mapping=False, root=root)
    # corporate_actions are NOT loaded here on purpose: the flat adj_factor=1.0
    # baseline must stay consistent with the (no-action) derived factors so the
    # clean world has zero invariant violations. Action-specific tests ingest them.
    return dict(symbols=syms, days=days, start=start, end=end)
