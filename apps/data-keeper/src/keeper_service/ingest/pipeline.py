"""Crawl pipeline — wires the adapters into quant_data.ingest with主源→备源降级.

One crawl == one ingest run == one batch == one trace_id (the JobRunner re-binds
it). The fetch_fn tries akshare first, falls back to tushare on any failure
(V3 §4.3 / §11 Q1); ingest's append-only + batch dedup makes retries idempotent.
"""
from __future__ import annotations

import datetime as dt

from quant_data import DataReader, ingest, logging as qd_logging

from .adapters import akshare, tushare

_log = qd_logging.get_logger("keeper.ingest")


def _active_symbols(root) -> list[str]:
    """The active symbol universe from reference.symbols (scheduler default)."""
    try:
        tbl = DataReader(root=root).read_symbols(active_only=True)
        col = tbl.column("symbol").to_pylist() if "symbol" in tbl.column_names else []
        return [s for s in col if s]
    except Exception as exc:  # noqa: BLE001 - never let a missing universe crash the crawl
        _log.warn("ingest.universe.unavailable", reason=str(exc))
        return []


def _fallback_fetch(primary, backup):
    """Build a fetch_fn that tries akshare, then tushare on any error."""
    def fetch_fn(**params):
        try:
            return primary(**params)
        except Exception as exc:  # noqa: BLE001 - degrade to backup, never abort the crawl
            _log.warn("ingest.source.fallback", primary="akshare", reason=str(exc))
            return backup(**params)
    return fetch_fn


def crawl_bars_daily(*, symbols=None, start=None, end=None, root=None) -> dict:
    if not symbols:
        symbols = _active_symbols(root)
    if not symbols:
        _log.warn("ingest.crawl.skipped", table="raw.bars_daily", reason="no active symbols")
        return {"table": "raw.bars_daily", "rows": 0, "skipped": True}
    fetch_fn = _fallback_fetch(
        lambda **p: akshare.fetch_bars_daily(symbols, start, end),
        lambda **p: tushare.fetch_bars_daily(symbols, start, end),
    )
    res = ingest.ingest_bars_daily(fetch_fn=fetch_fn, symbols=symbols, start=start, end=end,
                                   source="akshare", apply_mapping=False, root=root)
    return {"table": "raw.bars_daily", "batch_id": res.batch_id, "rows": res.row_count,
            "issues": len(res.issues)}


def crawl_calendar(*, market="CN-A", start=None, end=None, root=None) -> dict:
    fetch_fn = _fallback_fetch(
        lambda **p: akshare.fetch_calendar(market, start, end),
        lambda **p: [],  # tushare calendar not implemented; akshare is authoritative here
    )
    res = ingest.ingest_calendar(fetch_fn=fetch_fn, market=market, source="akshare",
                                 apply_mapping=False, root=root)
    return {"table": "raw.calendar", "batch_id": res.batch_id, "rows": res.row_count}


_CRAWLERS = {
    "raw.bars_daily": crawl_bars_daily,
    "raw.calendar": crawl_calendar,
}


def crawl_table(table: str, *, root=None, **params) -> dict:
    crawler = _CRAWLERS.get(table)
    if crawler is None:
        raise ValueError(f"no crawler registered for {table!r}; known: {sorted(_CRAWLERS)}")
    # normalise date strings -> date objects (scheduler passes ISO strings)
    for k in ("start", "end"):
        v = params.get(k)
        if isinstance(v, str) and v:
            params[k] = dt.date.fromisoformat(v[:10])
    return crawler(root=root, **params)
