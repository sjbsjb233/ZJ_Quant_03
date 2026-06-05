"""增量账本 — append-only parquet per item_key + settle model (§4.3 / §7).

Strategy note (pragmatic correctness, §15.7): every run recomputes the resolved
window via the evaluator and (re)writes the item ledger for that window. Because
the factor compute is deterministic on PIT data, an overwrite of the *window*
is equivalent for every downstream consumer (snapshot stats use window-settled
rows; the item series view shows the window). Out-of-window history is never
read, so we don't carry it. The settle/pending split is derived from which
rebalance dates the evaluator could attach forward returns to.
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
from typing import Any

import polars as pl


# ─────────────────────────────────────────────────────────────────────────────
def item_dir(store_root: str, basket_id: str, revision: int, item_key: str) -> str:
    return os.path.join(
        store_root, "evaluations", "baskets", basket_id, f"rev{revision}", item_key
    )


def snapshot_dir(store_root: str, basket_id: str, revision: int, as_of: str) -> str:
    return os.path.join(
        store_root, "evaluations", "baskets", basket_id, f"rev{revision}",
        "snapshots", str(as_of),
    )


def _as_date(v: Any) -> dt.date | None:
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except Exception:  # noqa: BLE001
        return None


# ─────────────────────────────────────────────────────────────────────────────
def settled_split(
    result: dict[str, Any], primary_horizon: int
) -> tuple[list[dt.date], list[dt.date], dt.date | None]:
    """From an evaluator result derive (settled_dates, pending_dates, last_rebal).

    The evaluator can only compute IC for a rebalance day once ``primary_horizon``
    forward bars exist; later days have no IC row → they are *pending* (§7.2).
    """
    rebal = [d for d in (_as_date(x) for x in (result.get("rebalance_dates") or [])) if d]
    rebal = sorted(set(rebal))
    ic = result.get("ic_series")
    settled: list[dt.date] = []
    if isinstance(ic, pl.DataFrame) and ic.height and "date" in ic.columns:
        settled = sorted({d for d in (_as_date(x) for x in ic["date"].to_list()) if d})
    last_settled = settled[-1] if settled else None
    if last_settled is not None:
        pending = [d for d in rebal if d > last_settled]
    else:
        pending = list(rebal)
    last_rebal = rebal[-1] if rebal else None
    return settled, pending, last_rebal


# ─────────────────────────────────────────────────────────────────────────────
def write_ledger(idir: str, result: dict[str, Any], primary_horizon: int) -> dict[str, int]:
    """(Re)write the item's window ledger parquet. Returns row counts."""
    os.makedirs(idir, exist_ok=True)
    settled, _pending, _ = settled_split(result, primary_horizon)
    settled_set = set(settled)

    rows_factor = 0
    rows_ic = 0

    # factor_values.parquet — processed (post-neutralization) cross-section
    clean = result.get("clean_values")
    if isinstance(clean, pl.DataFrame) and clean.height:
        df = clean
        if "value" in df.columns:
            df = df.rename({"value": "processed"}) if "processed" not in df.columns else df
        df.write_parquet(os.path.join(idir, "factor_values.parquet"))
        rows_factor = df.height

    # ic.parquet — [date, ic, rank_ic, coverage, settled]
    ic = result.get("ic_series")
    if isinstance(ic, pl.DataFrame) and ic.height:
        df = ic
        if "date" in df.columns:
            df = df.with_columns(
                pl.col("date").map_elements(
                    lambda d: _as_date(d) in settled_set, return_dtype=pl.Boolean
                ).alias("settled")
            )
        df.write_parquet(os.path.join(idir, "ic.parquet"))
        rows_ic = df.height

    # layers.parquet — [date, quantile, ret]
    layers = (result.get("layers") or {}).get("layers")
    if isinstance(layers, pl.DataFrame) and layers.height:
        layers.write_parquet(os.path.join(idir, "layers.parquet"))
    elif isinstance(layers, list) and layers:
        pl.DataFrame(layers).write_parquet(os.path.join(idir, "layers.parquet"))

    # turnover.parquet — [date, turnover]
    series = (result.get("turnover") or {}).get("series")
    if isinstance(series, list) and series:
        pl.DataFrame(series).write_parquet(os.path.join(idir, "turnover.parquet"))

    return {"rows_factor": rows_factor, "rows_ic": rows_ic}


def archive_fingerprint(idir: str, old_fp: str | None) -> None:
    """Rename the stale-fingerprint ledger aside before a refingerprint rebuild
    (§7.6) — kept for audit, new ledger rebuilt from empty."""
    if not os.path.isdir(idir) or not old_fp:
        return
    tag = (old_fp or "none").replace(":", "_")[:24]
    dst = f"{idir}.fp_{tag}"
    try:
        if os.path.exists(dst):
            shutil.rmtree(dst)
        shutil.move(idir, dst)
    except Exception:  # noqa: BLE001 — archive is best-effort
        pass


# ─────────────────────────────────────────────────────────────────────────────
def read_series(idir: str, kind: str, start: dt.date | None, end: dt.date | None) -> list[dict]:
    """Read a window slice for the item-detail charts (§10.3). kind ∈ ic|layers|turnover."""
    fname = {"ic": "ic.parquet", "layers": "layers.parquet", "turnover": "turnover.parquet"}.get(kind)
    if not fname:
        return []
    path = os.path.join(idir, fname)
    if not os.path.exists(path):
        return []
    try:
        df = pl.read_parquet(path)
    except Exception:  # noqa: BLE001
        return []
    if "date" in df.columns and (start or end):
        if start:
            df = df.filter(pl.col("date") >= pl.lit(start))
        if end:
            df = df.filter(pl.col("date") <= pl.lit(end))
    rows = df.to_dicts()
    # normalize dates to iso strings
    for r in rows:
        d = r.get("date")
        if isinstance(d, (dt.date, dt.datetime)):
            r["date"] = d.isoformat()[:10]
    return rows


def read_ic_for_corr(idir: str) -> dict[str, float]:
    """Settled IC series keyed by ISO date — the input to篮内相关性 (§7.5)."""
    path = os.path.join(idir, "ic.parquet")
    if not os.path.exists(path):
        return {}
    try:
        df = pl.read_parquet(path)
    except Exception:  # noqa: BLE001
        return {}
    if "settled" in df.columns:
        df = df.filter(pl.col("settled"))
    col = "rank_ic" if "rank_ic" in df.columns else ("ic" if "ic" in df.columns else None)
    if col is None or "date" not in df.columns:
        return {}
    out: dict[str, float] = {}
    for d, v in zip(df["date"].to_list(), df[col].to_list()):
        dd = _as_date(d)
        if dd is not None and v is not None:
            out[dd.isoformat()] = float(v)
    return out
