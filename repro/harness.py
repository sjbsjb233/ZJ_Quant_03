"""Reproduce the OOM-audit findings against the production-scale dataset.

Each suspect read path runs in its own child process. The parent samples the
child's RSS (/proc/<pid>/status VmRSS) every 50ms. If RSS crosses --cap MB the
child is SIGKILL'd and the run is recorded as a *simulated container OOM-kill*
(this is exactly what the cgroup OOM-killer does at mem_limit=8GB in prod; we
model it with a sampler so a blow-up can't take down the 15GB host).

Crucially every child sets:
    QUANT_DUCKDB_MEMORY_LIMIT=2GB   (production engine cap)
    QUANT_DUCKDB_TEMP_DIR=...        (so the engine spills, like prod)
so we prove the OOM happens in the *to-Python/Arrow materialisation*, which the
DuckDB memory_limit does NOT bound -- not in the engine.
"""
from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import time

ROOT = "/home/user/ZJ_Quant_03/repro/data"
TEMP = "/home/user/ZJ_Quant_03/repro/duck_tmp"


def _rss_mb(pid: int) -> float:
    try:
        with open(f"/proc/{pid}/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except (FileNotFoundError, ProcessLookupError):
        pass
    return 0.0


# ---- the suspect paths (each runs in a child) ------------------------------
def path_count_current_view():
    """FIXED primitive: COUNT(*), never materialises. Baseline."""
    from quant_data.reader import DataReader
    from quant_data.schemas import get_spec
    r = DataReader(root=ROOT)
    n = r.count_current_view(get_spec("raw.bars_daily"))
    print(f"    [child] count_current_view -> {n:,}", flush=True)


def path_table_stats_fixed():
    """FIXED table_stats.compute_table_stats (uses count_current_view now)."""
    from quant_data import table_stats
    s = table_stats.compute_table_stats("raw.bars_daily", root=ROOT)
    print(f"    [child] compute_table_stats row_count={s['row_count']:,}", flush=True)


def path_table_stats_historical_bug():
    """The ORIGINAL refresh_all pattern: current_view(table).num_rows."""
    from quant_data.reader import DataReader
    r = DataReader(root=ROOT)
    n = r.current_view("raw.bars_daily").num_rows   # materialises whole table
    print(f"    [child] current_view().num_rows -> {n:,}", flush=True)


def path_explorer():
    """apps/web charts/explorer.py: materialise whole table, register, aggregate (OLD)."""
    from quant_data.config import connect_duckdb
    from quant_data.reader import DataReader
    r = DataReader(root=ROOT)
    cv = r.current_view("raw.bars_daily")            # whole table into Python
    con = connect_duckdb()
    con.register("t", cv)
    rows = con.execute("SELECT date_trunc('month', date) AS x, avg(close) AS v "
                       "FROM t GROUP BY x ORDER BY x LIMIT 200000").fetchall()
    print(f"    [child] explorer agg rows={len(rows)}", flush=True)


def path_explorer_fixed():
    """NEW explorer path: aggregate pushed into the engine (aggregate_current_view)."""
    from quant_data.reader import DataReader
    from quant_data.schemas import get_spec
    r = DataReader(root=ROOT)
    spec = get_spec("raw.bars_daily")
    rows = r.aggregate_current_view(
        spec, 'date_trunc(\'month\', "date") AS x, avg("close") AS v',
        "GROUP BY x ORDER BY x LIMIT 200000",
    ).fetchall()
    print(f"    [child] explorer_fixed agg rows={len(rows)}", flush=True)


def path_diff_sources():
    """governance.diff_sources with NO date window -> two full to_pylist()."""
    from quant_data import governance
    d = governance.diff_sources("raw.bars_daily", "akshare", "tushare", root=ROOT)
    print(f"    [child] diff n_a={d['n_a']:,} n_b={d['n_b']:,}", flush=True)


def path_invariants_ctx():
    """invariants._Ctx(): registers bars_daily+universe+fundamentals+corp at once."""
    from quant_data import invariants
    ctx = invariants._Ctx(root=ROOT)
    print(f"    [child] _Ctx built, con={ctx.con}", flush=True)


def path_corporate():
    """corporate.compute_adj_factors(symbols=None): two full to_pylist()."""
    from quant_data import corporate
    t = corporate.compute_adj_factors(symbols=None, root=ROOT)
    print(f"    [child] compute_adj_factors rows={t.num_rows:,}", flush=True)


PATHS = {
    "count_current_view": path_count_current_view,
    "table_stats_fixed": path_table_stats_fixed,
    "table_stats_historical_bug": path_table_stats_historical_bug,
    "explorer": path_explorer,
    "explorer_fixed": path_explorer_fixed,
    "diff_sources": path_diff_sources,
    "invariants_ctx": path_invariants_ctx,
    "corporate": path_corporate,
}


def _child_entry(fn_name):
    os.environ["QUANT_DUCKDB_MEMORY_LIMIT"] = "2GB"
    os.environ["QUANT_DUCKDB_TEMP_DIR"] = TEMP
    os.makedirs(TEMP, exist_ok=True)
    PATHS[fn_name]()


def run_one(name: str, cap_mb: float, hard_mb: float) -> dict:
    ctx = mp.get_context("spawn")
    p = ctx.Process(target=_child_entry, args=(name,))
    t0 = time.time()
    p.start()
    peak = 0.0
    killed = None
    while p.is_alive():
        rss = _rss_mb(p.pid)
        peak = max(peak, rss)
        if rss >= cap_mb:
            killed = "CONTAINER_OOM_SIMULATED"
            p.kill()
            break
        if rss >= hard_mb:
            killed = "HOST_SAFETY_KILL"
            p.kill()
            break
        time.sleep(0.05)
    p.join()
    dur = time.time() - t0
    exitcode = p.exitcode
    status = killed or ("OK" if exitcode == 0 else f"DIED(exit={exitcode})")
    return {"name": name, "status": status, "peak_mb": round(peak, 1),
            "seconds": round(dur, 1), "exitcode": exitcode}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=float, default=8000.0,
                    help="MB; models the container mem_limit (default 8000 = 8GB)")
    ap.add_argument("--hard", type=float, default=12000.0,
                    help="MB; absolute host-safety kill threshold")
    ap.add_argument("--only", nargs="*", default=None)
    args = ap.parse_args()

    names = args.only or list(PATHS)
    print(f"cap={args.cap}MB (container model)  hard={args.hard}MB (host safety)\n")
    results = []
    for name in names:
        print(f"=== {name} ===", flush=True)
        res = run_one(name, args.cap, args.hard)
        results.append(res)
        print(f"    -> status={res['status']}  peak_RSS={res['peak_mb']}MB  "
              f"{res['seconds']}s\n", flush=True)

    print("\n================ SUMMARY ================")
    print(f"{'path':<32}{'status':<26}{'peakRSS(MB)':>12}{'sec':>8}")
    for r in results:
        print(f"{r['name']:<32}{r['status']:<26}{r['peak_mb']:>12}{r['seconds']:>8}")


if __name__ == "__main__":
    main()
