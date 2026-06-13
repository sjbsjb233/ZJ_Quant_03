"""In-memory test-support DataReader (NOT part of the public API).

Builds a small, deterministic (seeded) panel as pyarrow tables so the engine,
preflight gate and degradation台账 can be exercised end-to-end without the data
layer's on-disk storage. Toggles let a test remove a column/table (e.g. no
``circ_mv``, no ``reference.sectors``, no ``vwap``) to reproduce the §13
acceptance scenarios.

Conventions follow tests/factories (project memory): seeded ``random`` + pyarrow,
no pandas / hypothesis.
"""
from __future__ import annotations

import datetime as dt
import random

import pyarrow as pa


_SECTORS = ["801010", "801020", "801030", "801040"]  # SW level-1 sample codes


class _FakeColumnNotInView(ValueError):
    """Mirrors quant_data.reader.ColumnNotInViewError's contract (a ValueError
    carrying ``.column``/``.table``) without importing the data layer."""

    def __init__(self, column: str, table: str):
        self.column = column
        self.table = table
        super().__init__(f"列 {column!r} 不在 {table} 的当前视图中(数据尚未包含该列)")


class FakeReader:
    """A minimal DataReader stand-in returning pyarrow tables.

    Parameters (all default to a *complete* dataset → a clean run):
      n_symbols      : number of stocks
      n_days         : number of consecutive trading days
      with_circ_mv   : include circ_mv column in bars (else absent)
      with_total_mv  : include total_mv column in bars
      with_vwap      : include a non-null vwap column in bars
      with_sectors   : populate reference.sectors + symbols.sector_code
      sector_coverage: fraction of symbols given a known sector_code
      with_flags     : include index-membership flags in universe
      with_st        : include st_status column in universe
      with_suspend   : include is_suspended / is_tradable in universe
      with_listdays  : include listed_days in universe
    """

    def __init__(
        self,
        *,
        n_symbols: int = 30,
        n_days: int = 60,
        seed: int = 7,
        with_circ_mv: bool = True,
        with_total_mv: bool = True,
        with_vwap: bool = True,
        with_sectors: bool = True,
        sector_coverage: float = 1.0,
        with_flags: bool = True,
        with_st: bool = True,
        with_suspend: bool = True,
        with_listdays: bool = True,
    ) -> None:
        self.n_symbols = n_symbols
        self.with_circ_mv = with_circ_mv
        self.with_total_mv = with_total_mv
        self.with_vwap = with_vwap
        self.with_sectors = with_sectors
        self.sector_coverage = sector_coverage
        self.with_flags = with_flags
        self.with_st = with_st
        self.with_suspend = with_suspend
        self.with_listdays = with_listdays

        rng = random.Random(seed)
        self.symbols = [f"{600000 + i}.SH" for i in range(n_symbols)]
        # consecutive calendar days are fine — the engine only needs a monotone
        # ordered set of distinct dates.
        d0 = dt.date(2024, 1, 2)
        self.dates = [d0 + dt.timedelta(days=k) for k in range(n_days)]

        # seeded random-walk close per symbol
        self._bars: list[dict] = []
        for si, sym in enumerate(self.symbols):
            px = 10.0 + si * 0.5
            for d in self.dates:
                px = max(1.0, px * (1.0 + rng.uniform(-0.03, 0.03)))
                close = round(px, 4)
                row = {
                    "date": d,
                    "symbol": sym,
                    "open": close * 0.99,
                    "high": close * 1.02,
                    "low": close * 0.98,
                    "close": close,
                    "volume": float(rng.randint(10_000, 90_000)),
                    "amount": close * rng.randint(10_000, 90_000),
                    "adj_factor": 1.0,
                }
                if with_vwap:
                    row["vwap"] = close * 1.001
                if with_total_mv:
                    row["total_mv"] = close * 1e8
                if with_circ_mv:
                    row["circ_mv"] = close * 8e7
                self._bars.append(row)

        # sector assignment per symbol
        n_known = int(round(n_symbols * sector_coverage)) if with_sectors else 0
        self._sector_of: dict[str, str | None] = {}
        for i, sym in enumerate(self.symbols):
            self._sector_of[sym] = _SECTORS[i % len(_SECTORS)] if i < n_known else None

    # ── bars ─────────────────────────────────────────────────────────────
    def _bars_columns(self) -> list[str]:
        cols = ["date", "symbol", "open", "high", "low", "close", "volume", "amount", "adj_factor"]
        if self.with_vwap:
            cols.append("vwap")
        if self.with_total_mv:
            cols.append("total_mv")
        if self.with_circ_mv:
            cols.append("circ_mv")
        return cols

    def read_bars_daily(self, symbols=None, start=None, end=None, adjust="none",
                        extras_as_columns=None, recompute_adj=False,
                        columns=None) -> pa.Table:
        rows = self._bars
        if symbols is not None:
            ss = set(symbols)
            rows = [r for r in rows if r["symbol"] in ss]
        if start is not None:
            rows = [r for r in rows if r["date"] >= start]
        if end is not None:
            rows = [r for r in rows if r["date"] <= end]
        cols = self._bars_columns()
        if columns is not None:
            # mirror the real reader's WP2 contract: unknown column → typed
            # ValueError (ColumnNotInViewError subclasses it); pk auto-union;
            # request order preserved.
            for c in columns:
                if c not in cols:
                    raise _FakeColumnNotInView(c, "raw.bars_daily")
            keep = list(dict.fromkeys(columns))
            for k in ("date", "symbol"):
                if k not in keep:
                    keep.append(k)
            cols = keep
        return _to_table(rows, cols)

    # ── fundamentals (PIT; minimal — bps per symbol, published before window) ──
    def read_fundamentals(self, symbols=None, as_of_date=None,
                          extras_as_columns=None, columns=None) -> pa.Table:
        if as_of_date is None:
            raise ValueError("read_fundamentals requires as_of_date")
        pub = self.dates[0]
        rows = []
        for i, sym in enumerate(self.symbols):
            if symbols is not None and sym not in set(symbols):
                continue
            if pub <= as_of_date:
                rows.append({
                    "symbol": sym, "report_period": pub, "publish_date": pub,
                    "bps": 3.0 + (i % 5) * 0.5,
                })
        return _to_table(rows, ["symbol", "report_period", "publish_date", "bps"])

    # ── universe ───────────────────────────────────────────────────────────
    def read_universe(self, symbols=None, start=None, end=None, extras_as_columns=None) -> pa.Table:
        rows = []
        for d in self.dates:
            if start is not None and d < start:
                continue
            if end is not None and d > end:
                continue
            for sym in self.symbols:
                if symbols is not None and sym not in set(symbols):
                    continue
                row = {"date": d, "symbol": sym}
                if self.with_flags:
                    row["in_hs300"] = True
                    row["in_zz500"] = False
                    row["in_zz1000"] = False
                    row["in_sse50"] = False
                if self.with_st:
                    row["st_status"] = "normal"
                if self.with_suspend:
                    row["is_suspended"] = False
                    row["is_tradable"] = True
                if self.with_listdays:
                    row["listed_days"] = 500
                rows.append(row)
        cols = ["date", "symbol"]
        if self.with_flags:
            cols += ["in_hs300", "in_zz500", "in_zz1000", "in_sse50"]
        if self.with_st:
            cols.append("st_status")
        if self.with_suspend:
            cols += ["is_suspended", "is_tradable"]
        if self.with_listdays:
            cols.append("listed_days")
        return _to_table(rows, cols)

    # ── reference ────────────────────────────────────────────────────────
    def read_symbols(self, symbols=None, active_only=False, extras_as_columns=None) -> pa.Table:
        rows = []
        for sym in self.symbols:
            if symbols is not None and sym not in set(symbols):
                continue
            rows.append({"symbol": sym, "sector_code": self._sector_of.get(sym)})
        return _to_table(rows, ["symbol", "sector_code"])

    def read_sectors(self, classification_system=None, level=None) -> pa.Table:
        rows = []
        if self.with_sectors:
            for code in _SECTORS:
                rows.append({
                    "code": code, "name": f"SW-{code}", "level": 1,
                    "parent_code": None, "classification_system": "SW",
                })
        if level is not None:
            rows = [r for r in rows if r["level"] == level]
        return _to_table(rows, ["code", "name", "level", "parent_code", "classification_system"])

    def read_calendar(self, market=None, start=None, end=None) -> pa.Table:
        rows = [{"date": d, "market": "XSHG", "is_trading_day": True} for d in self.dates]
        return _to_table(rows, ["date", "market", "is_trading_day"])

    # ── aggregate / metadata primitives (内存治理 WP3.3 — preflight 聚合下推) ──
    # The real reader pushes these into DuckDB over parquet; the fake runs the
    # SAME SQL shape over its in-memory tables so preflight exercises the
    # identical query path ("缩限不缩机制").
    def _cv_table(self, name: str) -> pa.Table:
        # NOTE: call the base implementations explicitly — counting spies in
        # tests override read_bars_daily/read_universe to count *governed*
        # reads; the fake's internal materialisation (its stand-in for the
        # engine's parquet scan) must not show up in those counters.
        if name == "raw.bars_daily":
            return FakeReader.read_bars_daily(self)
        if name == "raw.universe":
            return FakeReader.read_universe(self)
        raise KeyError(f"FakeReader has no current view for {name}")

    def current_view_columns(self, table_name: str) -> set[str]:
        return set(self._cv_table(table_name).column_names)

    def aggregate_current_view(self, spec, projection: str, tail: str = "",
                               filters=None):
        import re as _re

        import duckdb

        name = spec if isinstance(spec, str) else spec.name
        con = duckdb.connect()
        con.register("_fake_cv", self._cv_table(name))
        where = (" WHERE " + " AND ".join(filters)) if filters else ""
        sql = (
            f"WITH cv AS (SELECT * FROM _fake_cv t{where}) "
            f"SELECT {projection} FROM cv {tail}"
        )
        try:
            return con.execute(sql)
        except duckdb.BinderException as e:
            m = _re.search(r'Referenced column "([^"]+)" not found', str(e))
            if m:
                raise _FakeColumnNotInView(m.group(1), name) from e
            raise


def _to_table(rows: list[dict], cols: list[str]) -> pa.Table:
    """Build a pyarrow table with stable column order + inferred-but-typed cols.

    Empty input still yields a table WITH the schema (mirrors reader._empty),
    so polars .select(cols) downstream never fails.
    """
    data = {c: [r.get(c) for r in rows] for c in cols}
    fields = []
    for c in cols:
        fields.append(pa.field(c, _arrow_type(c)))
    return pa.table(data, schema=pa.schema(fields))


def _arrow_type(col: str):
    if col in {"date", "report_period", "publish_date"}:
        return pa.date32()
    if col in {"level", "listed_days"}:
        return pa.int64()
    if col in {"is_trading_day", "in_hs300", "in_zz500", "in_zz1000", "in_sse50",
               "is_suspended", "is_tradable"}:
        return pa.bool_()
    if col in {"symbol", "market", "st_status", "code", "name", "parent_code",
               "classification_system", "sector_code"}:
        return pa.string()
    return pa.float64()
