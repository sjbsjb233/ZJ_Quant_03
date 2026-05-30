"""Seed the demo data world from the single writer (V3 §7 phase C).

Run: ``python -m keeper_service.seed_demo [DATA_ROOT]`` (defaults to
$QUANT_DATA_ROOT or ./data). Since data-keeper is the only :rw mount, the demo
lake is (re)built here. The web side still owns its own audit DB + demo users
(``quant_web.seed_users``); this script only touches the data lake.

Idempotent-ish: it appends batches, so re-running layers more data. Use a fresh
DATA_ROOT for a clean demo.
"""
from __future__ import annotations

import datetime as dt
import os
import random
import sys
import warnings

import quant_data as qd
from quant_data import ingest, table_stats
from quant_data.metadata import batches, field_registry, sources

warnings.filterwarnings("ignore")
RNG = random.Random(20260521)

SYMBOLS = [
    ("600519.SH", "贵州茅台", "SH", "main", "801120", 1680.0),
    ("000858.SZ", "五粮液", "SZ", "main", "801120", 148.0),
    ("000568.SZ", "泸州老窖", "SZ", "main", "801120", 210.0),
    ("000001.SZ", "平安银行", "SZ", "main", "801780", 11.5),
    ("600000.SH", "浦发银行", "SH", "main", "801780", 8.2),
    ("600036.SH", "招商银行", "SH", "main", "801780", 38.0),
    ("000002.SZ", "万科A", "SZ", "main", "801180", 9.4),
    ("300750.SZ", "宁德时代", "SZ", "gem", "801730", 185.0),
]
SECTORS = [
    ("801120", "食品饮料", 1, None), ("801780", "银行", 1, None),
    ("801180", "房地产", 1, None), ("801730", "电力设备", 1, None),
]
BASE = {s[0]: s[5] for s in SYMBOLS}


def trading_days(start: dt.date, end: dt.date) -> list[dt.date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def gen_calendar(days: list[dt.date]) -> list[dict]:
    tset = set(days)
    recs = []
    span_start, span_end = days[0] - dt.timedelta(days=10), days[-1]
    d = span_start
    while d <= span_end:
        td = d in tset
        nxt = next((x for x in days if x > d), None)
        recs.append(dict(date=d, market="CN-A", is_trading_day=td,
                         prev_trading_day=max((x for x in days if x < d), default=None) if td else None,
                         next_trading_day=nxt if td else None,
                         trading_session="full" if td else None,
                         month_end_trading_day=td and (nxt is None or nxt.month != d.month),
                         quarter_end_trading_day=td and (nxt is None or (nxt.month - 1)//3 != (d.month-1)//3),
                         year_end_trading_day=td and (nxt is None or nxt.year != d.year),
                         holiday_name=None))
        d += dt.timedelta(days=1)
    return recs


def gen_bars_daily(symbols, days, scale=1.0, jitter=0.015) -> list[dict]:
    recs = []
    for s in symbols:
        price = BASE[s]
        prev = price
        for d in days:
            ret = RNG.gauss(0.0003, jitter)
            close = round(max(0.5, prev * (1 + ret)) * scale, 2)
            high = round(max(close, prev * scale) * (1 + abs(RNG.gauss(0, 0.004))), 2)
            low = round(min(close, prev * scale) * (1 - abs(RNG.gauss(0, 0.004))), 2)
            open_ = round(min(max(prev * scale * (1 + RNG.gauss(0, 0.003)), low), high), 2)
            vol = RNG.randint(800_000, 40_000_000)
            recs.append(dict(date=d, symbol=s, open=open_, high=high, low=low, close=close,
                             pre_close=round(prev * scale, 2), volume=vol,
                             amount=round(close * vol, 2), vwap=close, adj_factor=1.0,
                             turnover=round(RNG.uniform(0.1, 1.5), 2)))
            prev = close / scale
    return recs


def gen_bars_minute(symbols, days) -> list[dict]:
    recs = []
    sessions = [(9, 30, 120), (13, 0, 120)]
    for s in symbols:
        price = BASE[s]
        for d in days:
            for (h, m, n) in sessions:
                t0 = dt.datetime(d.year, d.month, d.day, h, m)
                for i in range(n):
                    ts = t0 + dt.timedelta(minutes=i)
                    ret = RNG.gauss(0, 0.0015)
                    close = round(max(0.5, price * (1 + ret)), 2)
                    high = round(max(close, price) * (1 + abs(RNG.gauss(0, 0.001))), 2)
                    low = round(min(close, price) * (1 - abs(RNG.gauss(0, 0.001))), 2)
                    vol = RNG.randint(1000, 80000)
                    recs.append(dict(datetime=ts, symbol=s, frequency="1m",
                                     open=round(price, 2), high=high, low=low, close=close,
                                     volume=vol, amount=round(close * vol, 2), vwap=close,
                                     adj_factor=1.0))
                    price = close
    return recs


def gen_universe(symbols, days) -> list[dict]:
    hs300 = {"600519.SH", "000001.SZ", "300750.SZ", "600036.SH"}
    recs = []
    for s in symbols:
        for i, d in enumerate(days[-30:]):
            recs.append(dict(date=d, symbol=s, is_tradable=True, is_listed=True,
                             is_suspended=False, st_status="normal", in_hs300=s in hs300,
                             in_zz500=False, in_zz1000=False, in_sse50=s == "600519.SH",
                             limit_up_status="normal", listed_days=2000 + i))
    return recs


def gen_fundamentals(symbols, today) -> list[dict]:
    recs = []
    years = range(today.year - 3, today.year + 1)
    for s in symbols:
        rev0 = RNG.uniform(5e9, 2e11)
        for y in years:
            for q, (mm, dd) in enumerate([(3, 31), (6, 30), (9, 30), (12, 31)], 1):
                rp = dt.date(y, mm, dd)
                pub = rp + dt.timedelta(days=RNG.randint(25, 60))
                if pub > today:
                    continue
                grow = 1 + 0.04 * ((y - (today.year - 3)) * 4 + q)
                rev = round(rev0 * grow * RNG.uniform(0.9, 1.1), 2)
                ni = round(rev * RNG.uniform(0.08, 0.45), 2)
                pt = "A" if q == 4 else f"Q{q}"
                recs.append(dict(symbol=s, report_period=rp, period_type=pt, publish_date=pub,
                                 currency="CNY", revenue=rev, net_income=ni,
                                 net_income_attr_parent=round(ni * 0.97, 2),
                                 total_assets=round(rev * RNG.uniform(2, 6), 2),
                                 total_liabilities=round(rev * RNG.uniform(1, 3), 2),
                                 total_equity=round(rev * RNG.uniform(1, 3), 2),
                                 eps_basic=round(ni / 1e9, 3), operating_cash_flow=round(ni * 1.1, 2),
                                 roe=round(RNG.uniform(8, 33), 2),
                                 gross_margin=round(RNG.uniform(20, 92), 2),
                                 pe_ttm=round(RNG.uniform(8, 60), 2)))
    return recs


def gen_corporate_actions(today) -> list[dict]:
    y = today.year
    return [
        dict(symbol="600519.SH", action_date=dt.date(y - 1, 6, 28),
             announce_date=dt.date(y - 1, 5, 20), action_type="cash_dividend", cash_per_share=30.0),
        dict(symbol="000858.SZ", action_date=dt.date(y - 1, 7, 5),
             announce_date=dt.date(y - 1, 6, 1), action_type="cash_dividend", cash_per_share=4.5),
        dict(symbol="300750.SZ", action_date=dt.date(y - 1, 6, 12),
             announce_date=dt.date(y - 1, 5, 10), action_type="transfer", transfer_per_10=4.0),
        dict(symbol="000001.SZ", action_date=dt.date(y, 5, 20),
             announce_date=dt.date(y, 4, 18), action_type="cash_dividend", cash_per_share=2.28),
        dict(symbol="600036.SH", action_date=dt.date(y - 1, 7, 12),
             announce_date=dt.date(y - 1, 6, 10), action_type="stock_dividend", stock_per_10=2.0),
    ]


def seed(root: str) -> None:
    qd.bootstrap(root=root)  # registers akshare (priority 100)
    sources.register_source("tushare_pro", display_name="Tushare Pro", category="external_api",
                            priority=200, rate_limit_per_min=120, auth_method="token",
                            endpoint="https://api.tushare.pro/v2",
                            description="副源 · 财务+衍生品", root=root)
    sources.register_source("manual", display_name="人工录入", category="manual", priority=9,
                            description="行业体系 / 交易日历", root=root)
    sources.register_source("wind_intern", display_name="Wind 内部镜像", category="internal",
                            priority=3, is_active=False, description="尚未投产", root=root)

    today = dt.date.today()
    days = trading_days(today - dt.timedelta(days=380), today)
    syms = [s[0] for s in SYMBOLS]
    recent5 = days[-5:]

    print(f"[seed] root={root} · {len(syms)} symbols · {len(days)} trading days → {today}")

    ingest.ingest_symbols([dict(symbol=s[0], name=s[1], exchange=s[2], board=s[3],
                                sector_code=s[4], industry_code=s[4], currency="CNY",
                                list_date=dt.date(2005, 1, 1), delist_date=None, is_active=True)
                           for s in SYMBOLS], source="manual", root=root)
    ingest.ingest_sectors([dict(code=c, name=n, level=lv, parent_code=p,
                                classification_system="sw") for c, n, lv, p in SECTORS],
                          source="manual", root=root)
    ingest.ingest_calendar(records=gen_calendar(days), source="manual",
                           apply_mapping=False, root=root)
    ingest.ingest_universe(records=gen_universe(syms, days), source="akshare",
                           apply_mapping=False, root=root)
    ingest.ingest_corporate_actions(records=gen_corporate_actions(today), source="tushare_pro",
                                    apply_mapping=False, root=root)
    ingest.ingest_bars_daily(records=gen_bars_daily(syms, days), source="akshare",
                             apply_mapping=False, root=root)
    ingest.ingest_bars_minute(records=gen_bars_minute(["600519.SH", "000001.SZ"], days[-3:]),
                              source="tushare_pro", apply_mapping=False, root=root)
    ingest.ingest_fundamentals(records=gen_fundamentals(syms, today), source="tushare_pro",
                               apply_mapping=False, root=root)

    _governance_demo(root, recent5, today)
    _field_aging(root)

    table_stats.refresh_all(root=root)
    print("[seed] table_stats refreshed · done")


def _governance_demo(root, recent5, today):
    # 1) polluted akshare batch for 茅台 (close x10), then invalidate + supersede
    bad = [dict(date=d, symbol="600519.SH", open=BASE["600519.SH"] * 10, high=BASE["600519.SH"] * 10.2,
                low=BASE["600519.SH"] * 9.8, close=BASE["600519.SH"] * 10, pre_close=BASE["600519.SH"] * 10,
                volume=1_200_000, amount=1e9, vwap=BASE["600519.SH"] * 10, adj_factor=1.0)
           for d in recent5]
    rbad = ingest.ingest_bars_daily(records=bad, source="akshare", apply_mapping=False, root=root)
    good = [dict(date=d, symbol="600519.SH", open=BASE["600519.SH"], high=BASE["600519.SH"] * 1.02,
                 low=BASE["600519.SH"] * 0.98, close=round(BASE["600519.SH"] * 1.01, 2),
                 pre_close=BASE["600519.SH"], volume=1_234_567, amount=1.88e9,
                 vwap=BASE["600519.SH"], adj_factor=1.0) for d in recent5]
    rgood = ingest.ingest_bars_daily(records=good, source="tushare_pro", apply_mapping=False, root=root)
    batches.invalidate_batch(rbad.batch_id, "akshare 返回价格单位错误,close 被错乘 10", root=root)
    batches.supersede_batch(rbad.batch_id, rgood.batch_id, root=root)

    # 2) a failed batch (auth error)
    fb = batches.register_batch("tushare_pro", "raw.fundamentals",
                                parameters={"symbols": ["000001.SZ"]}, root=root)
    batches.finalize_batch(fb, "failed", error_message="AuthError: token expired", root=root)

    # 3) a stale running batch (started >24h ago, never finalized)
    sr = batches.register_batch("tushare_pro", "raw.bars_minute",
                                parameters={"symbols": ["600519.SH"]}, root=root)
    batches.update_batch(sr, started_at=dt.datetime.utcnow() - dt.timedelta(hours=27), root=root)

    # 4) inject a quality issue via a bad bar (high < low) -> recorded by ingest hook
    ingest.ingest_bars_daily(records=[dict(date=today, symbol="000002.SZ", open=9.0, high=8.5,
                                           low=9.2, close=9.1, pre_close=9.0, volume=0,
                                           amount=0.0, vwap=9.1, adj_factor=1.0)],
                             source="akshare", apply_mapping=False, root=root)


def _field_aging(root):
    """Backdate / wire extras so the Fields page shows realistic governance states."""
    now = dt.datetime.utcnow()
    tbl = "raw.fundamentals"
    field_registry.update_field_status(tbl, "roe", "extras", root=root,
                                        first_seen_at=now - dt.timedelta(days=184))
    for c in ["factor.roe_decile", "factor.quality_composite", "strategy.value_v1"]:
        field_registry.add_consumer(tbl, "roe", c, root=root)
    field_registry.update_field_status(tbl, "gross_margin", "extras", root=root,
                                        first_seen_at=now - dt.timedelta(days=120))
    field_registry.add_consumer(tbl, "gross_margin", "factor.profitability", root=root)
    field_registry.promote_field(tbl, "gross_margin", status="promoted",
                                 authoritative_source="tushare_pro", root=root)
    field_registry.update_field_status("raw.bars_daily", "turnover", "extras", root=root,
                                        first_seen_at=now - dt.timedelta(days=60))
    field_registry.add_consumer("raw.bars_daily", "turnover", "factor.liquidity", root=root)


def main() -> None:
    root = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("QUANT_DATA_ROOT", "./data")
    seed(root)
    print("[seed] data lake ready. (web owns its own audit DB + demo users: "
          "run `python -m quant_web.seed_users` on the web side.)")


if __name__ == "__main__":
    main()
