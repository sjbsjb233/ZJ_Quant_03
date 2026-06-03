"""Value-level correctness checks for the OOM fixes (small data, fast)."""
from __future__ import annotations

import datetime as dt

import quant_data as q
from quant_data import charts, ingest, storage
from quant_data.metadata import batches, sources
from quant_data.reader import DataReader
from quant_data.schemas import get_spec

ROOT = "/tmp/oomfix_correctness"
import shutil, os
shutil.rmtree(ROOT, ignore_errors=True)
q.bootstrap(ROOT)
sources.register_source("tushare", display_name="tushare", category="external_api",
                        priority=90, auth_method="none", root=ROOT)

SYMS = ["600000.SH", "000001.SZ", "300750.SZ"]
days = [dt.date(2024, 1, d) for d in range(2, 12)]


def bars(src, close_shift=0.0):
    recs = []
    for s in SYMS:
        for i, d in enumerate(days):
            c = 10.0 + i + close_shift
            recs.append(dict(date=d, symbol=s, open=c, high=c + 1, low=c - 1, close=c,
                             pre_close=c, volume=1000 + i, amount=c * 1000, vwap=c, adj_factor=1.0))
    return recs


# akshare (primary) + tushare (secondary, slightly different close)
ingest.ingest_bars_daily(records=bars("akshare"), source="akshare", apply_mapping=False,
                         run_checks=False, root=ROOT)
ingest.ingest_bars_daily(records=bars("tushare", 0.5), source="tushare", apply_mapping=False,
                         run_checks=False, root=ROOT)

# an invalidated batch for get_bar_revisions
res = ingest.ingest_bars_daily(records=bars("akshare", 99.0), source="akshare",
                               apply_mapping=False, run_checks=False, root=ROOT)
batches.invalidate_batch(res.batch_id, reason="test-revision", root=ROOT)

reader = DataReader(root=ROOT)
ok = True


# ---- 1. explorer aggregate pushdown == old materialise+register ----
def explorer_old(table, projection, tail):
    from quant_data.config import connect_duckdb
    cv = reader.current_view(table)
    con = connect_duckdb()
    con.register("t", cv)
    out = con.execute(f"SELECT {projection} FROM t {tail}").fetchall()
    con.close()
    return out


def explorer_new(table, projection, tail):
    return reader.aggregate_current_view(get_spec(table), projection, tail).fetchall()


for proj, tail in [
    ('date_trunc(\'month\', "date") AS x, avg("close") AS v', "GROUP BY x ORDER BY x"),
    ('"date" AS x, "symbol" AS g, "close" AS v', "WHERE \"symbol\" IN ('600000.SH') ORDER BY x LIMIT 5"),
    ('"date" AS x, sum("volume") AS v', "GROUP BY x ORDER BY x LIMIT 100"),
]:
    o = explorer_old("raw.bars_daily", proj, tail)
    n = explorer_new("raw.bars_daily", proj, tail)
    same = o == n
    ok &= same
    print(f"[explorer] proj={proj[:40]!r:42} old==new: {same}  (rows {len(o)})")


# ---- 2. get_bar_revisions: new (SQL) == old (python filter) logic ----
def revisions_old(symbol, start=None, end=None):
    table = "raw.bars_daily"
    out = []
    s = start; e = end
    for b in batches.list_batches(target_table=table, root=ROOT):
        if b["status"] != "invalidated":
            continue
        tbl = reader.read_batch(b["batch_id"], table)
        rows = [r for r in tbl.to_pylist() if r.get("symbol") == symbol]
        ds = [r["date"] for r in rows if r.get("date") is not None]
        if s is not None: ds = [d for d in ds if d >= s]
        if e is not None: ds = [d for d in ds if d <= e]
        if not ds:
            continue
        out.append({"start": min(ds), "end": max(ds), "batch_id": b["batch_id"]})
    out.sort(key=lambda r: r["start"])
    return out


for sym, s, e in [("600000.SH", None, None),
                  ("600000.SH", dt.date(2024, 1, 4), dt.date(2024, 1, 8)),
                  ("NONEXIST.SH", None, None)]:
    old = revisions_old(sym, s, e)
    new = [{"start": r["start"], "end": r["end"], "batch_id": r["batch_id"]}
           for r in charts.get_bar_revisions(sym, "1d", start=s, end=e, root=ROOT)]
    same = old == new
    ok &= same
    print(f"[revisions] sym={sym:12} window=({s},{e}) old==new: {same}  {new}")


# ---- 3. batch_symbol_date_range basic ----
inv = [b for b in batches.list_batches(target_table="raw.bars_daily", root=ROOT)
       if b["status"] == "invalidated"][0]
lo, hi = reader.batch_symbol_date_range(inv["batch_id"], "raw.bars_daily", "600000.SH")
print(f"[date_range] {lo}..{hi}  (expect {days[0]}..{days[-1]})")
ok &= (lo == days[0] and hi == days[-1])

print("\nALL CORRECTNESS PASS" if ok else "\n*** CORRECTNESS FAILURE ***")
import sys
sys.exit(0 if ok else 1)
