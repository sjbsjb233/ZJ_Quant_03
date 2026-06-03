"""DataReader -- the only sanctioned way business code touches data (doc §6).

Everything goes through the "current view" (doc §6.1): valid batches only,
multi-source conflict resolved by priority, one row per logical key. On top of
that:
  * PIT for fundamentals -- publish_date <= as_of_date (doc §6.2)
  * price adjustment for bars -- pre/post/none (doc §6.3)
  * real-time minute downsampling -- 1m/5m/15m/30m/1h (doc §6.4)
  * extras_as_columns -- promote _extras KV into typed columns, with a
    core-column-first compat fallback (doc §3.3 / §7.3)
"""
from __future__ import annotations

import datetime as dt
import time

import pyarrow as pa
import pyarrow.parquet as pq

from . import config, storage
from .logging import get_logger, get_slow_query_ms
from .metadata import batches, sources
from .schemas import ARROW_TYPE_BY_NAME, DATE, TS, TableSpec, get_spec

_PRICE_COLS = ("open", "high", "low", "close", "pre_close", "vwap")
_log = get_logger("quant_data.reader")


def _hashable(v):
    """Make a cell value hashable for set-based divergence detection.

    ``_extras`` and any list-typed cell arrive as dict/list; fold them to a
    stable string so they can live in a set alongside scalars.
    """
    if isinstance(v, (dict, list)):
        return repr(sorted(v.items())) if isinstance(v, dict) else repr(v)
    return v


# --- literal/identifier helpers (safe SQL assembly) -------------------------
def _str_lit(v: str) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def _ident(name: str) -> str:
    """Quote a column identifier (for aliases / EXCLUDE) so arbitrary _extras
    keys -- including ones with quotes or SQL metacharacters -- are safe."""
    return '"' + str(name).replace('"', '""') + '"'


def _in_clause(col: str, values) -> str | None:
    # None means "no symbol filter" (whole universe); an explicitly empty list
    # means "no symbols" -> a never-true predicate (doc test T-RD-004).
    if values is None:
        return None
    if len(values) == 0:
        return "1 = 0"
    return f"{col} IN ({','.join(_str_lit(v) for v in values)})"


def _to_date(x) -> dt.date:
    if isinstance(x, dt.datetime):
        return x.date()
    if isinstance(x, dt.date):
        return x
    return dt.date.fromisoformat(str(x)[:10])


def _to_ts(x) -> dt.datetime:
    if isinstance(x, dt.datetime):
        return x
    if isinstance(x, dt.date):
        return dt.datetime(x.year, x.month, x.day)
    return dt.datetime.fromisoformat(str(x))


def _date_lit(x) -> str:
    return f"DATE '{_to_date(x).isoformat()}'"


def _ts_lit(x) -> str:
    return f"TIMESTAMP '{_to_ts(x).isoformat(sep=' ')}'"


def _union_columns(files: list[str]) -> set[str]:
    """Column names present across the file set (union_by_name semantics)."""
    cols: set[str] = set()
    for f in files:
        cols.update(pq.read_schema(f).names)
    return cols


class DataReader:
    def __init__(self, root=None):
        self.root = root
        # Memory guardrails (memory_limit / temp_directory) live in
        # config.connect_duckdb so every DuckDB engine shares them -- see there.
        self._con = config.connect_duckdb()

    # -- core current-view engine (doc §6.1) ---------------------------------
    # default dedup ranking: source priority, then newest batch.
    # _batch_id DESC is a stable tiebreaker so equal _ingested_at (or clock
    # rewind) never makes the winner non-deterministic (doc test T-CV-004/T-MP-003).
    _DEFAULT_DEDUP = "_src_priority DESC, _ingested_at DESC, _batch_id DESC"

    def _current_view(
        self,
        spec: TableSpec,
        filters: list[str] | None = None,
        extras_as_columns: dict[str, str] | None = None,
        order_by: str | None = None,
        dedup_order: str | None = None,
        post_filters: list[str] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> pa.Table:
        """Materialise the current view.

        ``filters`` are pushed into the dwp CTE -- applied **before** dedup, so
        they prune source rows (typical: symbol / date predicates).
        ``post_filters`` are AND'd onto the final ``WHERE rn = 1`` -- applied
        **after** dedup, so e.g. a ``_source = 'akshare'`` filter selects only
        cases where akshare's row actually won, never resurrecting a row that
        lost to a higher-priority source. Order matters here: putting these in
        ``filters`` would silently flip the dedup winner.

        ``limit`` / ``offset`` push pagination into SQL so callers materialise
        one page instead of the whole table (root cause of the 2026-05-23 OOM).
        Only safe when downstream post-processing preserves row count and is
        value-stable per row -- e.g. ``_adjust(adjust="post")`` uses a window
        over the whole symbol and is NOT page-stable; callers using it must
        paginate in memory after the typed reader returns.
        """
        self._validate_extras_types(extras_as_columns)
        files = storage.list_data_files(spec, self.root)
        valid_ids = [
            b["batch_id"]
            for b in batches.list_batches(target_table=spec.name, status="success", root=self.root)
        ]
        prio = sources.priority_map(self.root)
        if not files or not valid_ids or not prio:
            return self._empty(spec, extras_as_columns)

        present = _union_columns(files)
        con = self._con
        con.register("_valid_batches", pa.table({"batch_id": pa.array(valid_ids, pa.string())}))
        con.register(
            "_src_prio",
            pa.table(
                {
                    "source_id": pa.array(list(prio), pa.string()),
                    "priority": pa.array(list(prio.values()), pa.int32()),
                }
            ),
        )

        where = ["t._batch_id IN (SELECT batch_id FROM _valid_batches)"]
        where += filters or []
        where_sql = " AND ".join(where)

        pk = ", ".join(spec.primary_key)
        excepts = ["rn", "_src_priority", *spec.partition_names()]
        extra_select, overridden = self._extras_select(extras_as_columns or {}, spec, present)
        excepts += overridden
        except_sql = f" EXCLUDE ({', '.join(_ident(c) for c in excepts)})" if excepts else ""
        order_sql = f" ORDER BY {order_by}" if order_by else ""
        post_sql = (" AND " + " AND ".join(post_filters)) if post_filters else ""
        # LIMIT/OFFSET literals: validated as ints so SQL injection is not a risk.
        page_sql = f" LIMIT {int(limit)} OFFSET {int(offset)}" if limit is not None else ""

        sql = f"""
        WITH dwp AS (
            SELECT t.*, p.priority AS _src_priority
            FROM read_parquet(?, hive_partitioning=true, union_by_name=true) t
            JOIN _src_prio p ON t._source = p.source_id
            WHERE {where_sql}
        ),
        ranked AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY {pk} ORDER BY {dedup_order or self._DEFAULT_DEDUP}
            ) AS rn
            FROM dwp
        )
        SELECT *{except_sql}{extra_select}
        FROM ranked WHERE rn = 1{post_sql}{order_sql}{page_sql}
        """
        _log.debug("reader.query.built", table=spec.name,
                   predicates=len(filters or []),
                   post_predicates=len(post_filters or []),
                   columns=len(present), limit=limit, offset=offset)
        start = time.perf_counter()
        try:
            result = con.execute(sql, [files]).to_arrow_table()
        except Exception:  # noqa: BLE001 - log then re-raise so callers still see it
            _log.exception("reader.query.failed", table=spec.name,
                           sql_first_200ch=" ".join(sql.split())[:200])
            raise
        duration_ms = int((time.perf_counter() - start) * 1000)
        if extras_as_columns:
            _log.debug("reader.extras_promote.applied", table=spec.name,
                       promoted_fields=list(extras_as_columns))
        if duration_ms > get_slow_query_ms():
            _log.warn("reader.query.slow", table=spec.name,
                      rows_returned=result.num_rows, duration_ms=duration_ms)
        else:
            _log.info("reader.query.executed", table=spec.name,
                      rows_returned=result.num_rows, duration_ms=duration_ms)
        return result

    def count_current_view(
        self,
        spec: TableSpec,
        filters: list[str] | None = None,
        post_filters: list[str] | None = None,
        dedup_order: str | None = None,
    ) -> int:
        """Count rows in the current view (applying the same pre/post filters).

        Used by the paged ``/rows`` endpoint to fill the ``total`` field without
        materialising the data. Dedup ranking still runs because post_filters
        select on the winner, but only COUNT(*) is returned -- O(rows) without
        the to-Arrow copy + Python conversion that caused the original blow-up.
        """
        files = storage.list_data_files(spec, self.root)
        valid_ids = [
            b["batch_id"]
            for b in batches.list_batches(target_table=spec.name, status="success", root=self.root)
        ]
        prio = sources.priority_map(self.root)
        if not files or not valid_ids or not prio:
            return 0
        con = self._con
        con.register("_valid_batches", pa.table({"batch_id": pa.array(valid_ids, pa.string())}))
        con.register(
            "_src_prio",
            pa.table(
                {
                    "source_id": pa.array(list(prio), pa.string()),
                    "priority": pa.array(list(prio.values()), pa.int32()),
                }
            ),
        )
        where = ["t._batch_id IN (SELECT batch_id FROM _valid_batches)"] + (filters or [])
        where_sql = " AND ".join(where)
        pk = ", ".join(spec.primary_key)
        post_sql = (" AND " + " AND ".join(post_filters)) if post_filters else ""
        sql = f"""
        WITH dwp AS (
            SELECT t.*, p.priority AS _src_priority
            FROM read_parquet(?, hive_partitioning=true, union_by_name=true) t
            JOIN _src_prio p ON t._source = p.source_id
            WHERE {where_sql}
        ),
        ranked AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY {pk} ORDER BY {dedup_order or self._DEFAULT_DEDUP}
            ) AS rn
            FROM dwp
        )
        SELECT COUNT(*) FROM ranked WHERE rn = 1{post_sql}
        """
        return int(con.execute(sql, [files]).fetchone()[0])

    def aggregate_current_view(
        self,
        spec: TableSpec,
        projection: str,
        tail: str = "",
        filters: list[str] | None = None,
    ):
        """Run an aggregation over the current view **in the engine**.

        Builds the same de-dup CTE as ``_current_view`` and exposes its winning
        rows as a relation ``cv``; the caller's ``projection`` (the part between
        ``SELECT`` and ``FROM``) and ``tail`` (its ``WHERE``/``GROUP BY``/
        ``ORDER BY``/``LIMIT``) run on top, so only the small aggregated result
        is ever materialised -- never the whole table. This is the bounded
        replacement for ``current_view(table)`` + register + aggregate, which
        copied 18M+ rows into the Python heap and OOM-killed the web backend
        (chart explorer, doc §6.9). ``projection``/``tail`` are assembled by the
        caller from a validated column/agg whitelist + escaped literals, exactly
        as before -- the SQL surface is unchanged, only the data path is.

        Returns the executed DuckDB cursor (use ``.fetchall()`` / ``.description``).
        """
        con = self._con
        files = storage.list_data_files(spec, self.root)
        valid_ids = [
            b["batch_id"]
            for b in batches.list_batches(target_table=spec.name, status="success", root=self.root)
        ]
        prio = sources.priority_map(self.root)
        if not files or not valid_ids or not prio:
            con.register("_agg_empty", self._empty(spec, None))
            return con.execute(f"SELECT {projection} FROM _agg_empty {tail}")

        con.register("_valid_batches", pa.table({"batch_id": pa.array(valid_ids, pa.string())}))
        con.register(
            "_src_prio",
            pa.table(
                {
                    "source_id": pa.array(list(prio), pa.string()),
                    "priority": pa.array(list(prio.values()), pa.int32()),
                }
            ),
        )
        where = ["t._batch_id IN (SELECT batch_id FROM _valid_batches)"] + (filters or [])
        where_sql = " AND ".join(where)
        pk = ", ".join(spec.primary_key)
        excepts = ["rn", "_src_priority", *spec.partition_names()]
        except_sql = f" EXCLUDE ({', '.join(_ident(c) for c in excepts)})" if excepts else ""
        sql = f"""
        WITH dwp AS (
            SELECT t.*, p.priority AS _src_priority
            FROM read_parquet(?, hive_partitioning=true, union_by_name=true) t
            JOIN _src_prio p ON t._source = p.source_id
            WHERE {where_sql}
        ),
        ranked AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY {pk} ORDER BY {self._DEFAULT_DEDUP}
            ) AS rn
            FROM dwp
        ),
        cv AS (
            SELECT *{except_sql} FROM ranked WHERE rn = 1
        )
        SELECT {projection} FROM cv {tail}
        """
        return con.execute(sql, [files])

    def batch_symbol_date_range(
        self, batch_id: str, table: str, symbol: str, start=None, end=None
    ) -> tuple[dt.date | None, dt.date | None]:
        """Min/max bar **date** one symbol got from a single batch, in-engine.

        ``get_bar_revisions`` only needs the date span a now-invalidated batch
        wrote for one symbol; reading the whole batch with ``read_batch`` and
        filtering in Python materialised an entire multi-million-row batch
        (doc §4.6.2). Pushing the symbol filter + min/max into SQL returns two
        scalars instead. ``CAST(col AS DATE)`` unifies daily (date) and minute
        (datetime) so the returned span matches the old date-based logic.
        """
        spec = get_spec(table)
        files = storage.list_data_files(spec, self.root)
        if not files:
            return (None, None)
        time_col = "date" if table == "raw.bars_daily" else "datetime"
        d = f"CAST({_ident(time_col)} AS DATE)"
        where = [f"_batch_id = {_str_lit(batch_id)}", f"symbol = {_str_lit(symbol)}"]
        if start is not None:
            where.append(f"{d} >= {_date_lit(start)}")
        if end is not None:
            where.append(f"{d} <= {_date_lit(end)}")
        sql = (
            f"SELECT min({d}), max({d}) "
            "FROM read_parquet(?, hive_partitioning=true, union_by_name=true) "
            f"WHERE {' AND '.join(where)}"
        )
        lo, hi = self._con.execute(sql, [files]).fetchone()
        return (lo, hi)

    @staticmethod
    def _validate_extras_types(extras_as_columns: dict[str, str] | None) -> None:
        """Reject unknown extras types up front so the empty-dataset path and the
        with-data path raise the same ValueError (not a KeyError)."""
        for type_name in (extras_as_columns or {}).values():
            if type_name.upper() not in ARROW_TYPE_BY_NAME:
                raise ValueError(f"unsupported extras type {type_name!r}")

    def _extras_select(
        self, extras_as_columns: dict[str, str], spec: TableSpec, present: set[str]
    ) -> tuple[str, list[str]]:
        """Build the extras->typed-column projection + the core columns it overrides.

        The decision keys off columns physically present in the files, so a field
        that has been promoted to a real column (doc §7.3) is read from that
        column, with the _extras KV kept only as a fallback for pre-migration rows.
        """
        if not extras_as_columns:
            return "", []
        exprs, overridden = [], []
        for fname, type_name in extras_as_columns.items():
            tname = type_name.upper()
            if tname not in ARROW_TYPE_BY_NAME:
                raise ValueError(f"unsupported extras type {type_name!r}")
            cast = f"TRY_CAST(_extras[{_str_lit(fname)}] AS {tname})"
            if fname in present:
                # promoted core column wins; fall back to extras for old rows
                exprs.append(f"COALESCE({_ident(fname)}, {cast}) AS {_ident(fname)}")
                overridden.append(fname)
            else:
                exprs.append(f"{cast} AS {_ident(fname)}")
        return ", " + ", ".join(exprs), overridden

    def _empty(self, spec: TableSpec, extras_as_columns: dict[str, str] | None) -> pa.Table:
        schema = spec.arrow_schema()
        if extras_as_columns:
            for fname, type_name in extras_as_columns.items():
                if fname in schema.names:
                    continue
                schema = schema.append(pa.field(fname, ARROW_TYPE_BY_NAME[type_name.upper()]))
        return schema.empty_table()

    # -- bars (doc §6.3 adjust, §6.4 downsample) -----------------------------
    def read_bars_daily(
        self, symbols=None, start=None, end=None, adjust="none", extras_as_columns=None,
        recompute_adj=False,
    ) -> pa.Table:
        if start is not None and end is not None and _to_date(start) > _to_date(end):
            raise ValueError(f"start {start} is after end {end}")
        filters = []
        if (c := _in_clause("t.symbol", symbols)):
            filters.append(c)
        if start is not None:
            filters.append(f"t.date >= {_date_lit(start)}")
        if end is not None:
            filters.append(f"t.date <= {_date_lit(end)}")
        cv = self._current_view(
            get_spec("raw.bars_daily"), filters, extras_as_columns, order_by="symbol, date"
        )
        if recompute_adj:
            cv = self._recomputed_factors(cv, symbols)
        return self._adjust(cv, "date", adjust)

    def _recomputed_factors(self, cv: pa.Table, symbols) -> pa.Table:
        """Override adj_factor with values derived live from corporate_actions.

        Lets a fix to corporate_actions propagate to adjusted prices without ever
        touching raw bars (doc §6.3 / §9.9). Lazy import avoids a cycle.
        """
        if cv.num_rows == 0:
            return cv
        from . import corporate

        factors = corporate.compute_adj_factors(symbols=symbols, root=self.root)
        self._con.register("_cv_b", cv)
        self._con.register("_recf", factors)
        sql = (
            "SELECT c.* EXCLUDE (adj_factor), "
            "COALESCE(f.adj_factor, c.adj_factor) AS adj_factor "
            "FROM _cv_b c LEFT JOIN _recf f USING (symbol, date)"
        )
        return self._con.execute(sql).to_arrow_table()

    def read_bars_minute(
        self, symbols=None, start=None, end=None, frequency="1m", adjust="none",
        extras_as_columns=None,
    ) -> pa.Table:
        if frequency not in config.MINUTE_FREQUENCIES:
            raise ValueError(
                f"unsupported frequency {frequency!r}; choose {sorted(config.MINUTE_FREQUENCIES)}"
            )
        filters = []
        if (c := _in_clause("t.symbol", symbols)):
            filters.append(c)
        if start is not None:
            filters.append(f"t.datetime >= {_ts_lit(start)}")
        if end is not None:
            filters.append(f"t.datetime <= {_ts_lit(end)}")
        cv = self._current_view(
            get_spec("raw.bars_minute"), filters, extras_as_columns, order_by="symbol, datetime"
        )
        if frequency != "1m":
            cv = self._downsample(cv, frequency)
        return self._adjust(cv, "datetime", adjust)

    def _downsample(self, cv: pa.Table, frequency: str) -> pa.Table:
        """Real-time 1m -> Nm/h aggregation via time_bucket (doc §6.4)."""
        if cv.num_rows == 0:
            return cv
        interval = config.MINUTE_FREQUENCIES[frequency]
        self._con.register("_cv", cv)
        sql = f"""
        SELECT
            time_bucket(INTERVAL '{interval}', datetime) AS datetime,
            symbol,
            {_str_lit(frequency)} AS frequency,
            first(open ORDER BY datetime) AS open,
            max(high) AS high,
            min(low) AS low,
            last(close ORDER BY datetime) AS close,
            CAST(sum(volume) AS BIGINT) AS volume,
            sum(amount) AS amount,
            CASE WHEN sum(volume) > 0 THEN sum(amount) / sum(volume) END AS vwap,
            last(adj_factor ORDER BY datetime) AS adj_factor
        FROM _cv
        GROUP BY 1, 2
        ORDER BY symbol, datetime
        """
        return self._con.execute(sql).to_arrow_table()

    def _adjust(self, cv: pa.Table, time_col: str, adjust: str) -> pa.Table:
        if adjust == "none":
            return cv
        if adjust not in ("pre", "post"):
            raise ValueError(f"adjust must be 'pre'/'post'/'none', got {adjust!r}")
        if cv.num_rows == 0:
            return cv
        price_cols = [c for c in _PRICE_COLS if c in cv.column_names]
        if "adj_factor" not in cv.column_names or not price_cols:
            return cv

        if adjust == "pre":
            fct = "COALESCE(adj_factor, 1.0)"
        else:  # post: normalise so the earliest row in the window stays raw
            fct = (
                "COALESCE(adj_factor, 1.0) / NULLIF(first_value(COALESCE(adj_factor, 1.0)) "
                f"OVER (PARTITION BY symbol ORDER BY {time_col}), 0)"
            )
        self._con.register("_adj", cv)
        _log.debug("reader.adjust.applied", mode=adjust, time_col=time_col,
                   rows=cv.num_rows)
        adjusted = ", ".join(f"{c} * _fct AS {c}" for c in price_cols)
        sql = f"""
        SELECT * EXCLUDE ({', '.join(price_cols)}, _fct), {adjusted}
        FROM (SELECT _adj.*, {fct} AS _fct FROM _adj)
        ORDER BY symbol, {time_col}
        """
        return self._con.execute(sql).to_arrow_table()

    # -- fundamentals (PIT, doc §6.2) ----------------------------------------
    def read_fundamentals(self, symbols=None, as_of_date=None, extras_as_columns=None) -> pa.Table:
        if as_of_date is None:
            raise ValueError("read_fundamentals requires as_of_date (PIT, doc §6.2)")
        filters = [f"t.publish_date <= {_date_lit(as_of_date)}"]
        if (c := _in_clause("t.symbol", symbols)):
            filters.append(c)
        _log.debug("reader.pit.applied", table="raw.fundamentals", as_of=str(as_of_date))
        # Strict PIT: among the report versions visible as of the date (publish_date
        # <= as_of), keep the most-recently-DISCLOSED one. Ordering dedup by
        # publish_date DESC (not just _ingested_at) means a later backfill of an
        # older revision can't leak a stale value -- the as-of revision wins.
        return self._current_view(
            get_spec("raw.fundamentals"), filters, extras_as_columns,
            order_by="symbol, report_period",
            dedup_order="_src_priority DESC, publish_date DESC, _ingested_at DESC, _batch_id DESC",
        )

    # -- universe / calendar -------------------------------------------------
    def read_universe(self, symbols=None, start=None, end=None, extras_as_columns=None) -> pa.Table:
        filters = []
        if (c := _in_clause("t.symbol", symbols)):
            filters.append(c)
        if start is not None:
            filters.append(f"t.date >= {_date_lit(start)}")
        if end is not None:
            filters.append(f"t.date <= {_date_lit(end)}")
        return self._current_view(
            get_spec("raw.universe"), filters, extras_as_columns, order_by="date, symbol"
        )

    def read_calendar(self, market=None, start=None, end=None) -> pa.Table:
        filters = []
        if market is not None:
            filters.append(f"t.market = {_str_lit(market)}")
        if start is not None:
            filters.append(f"t.date >= {_date_lit(start)}")
        if end is not None:
            filters.append(f"t.date <= {_date_lit(end)}")
        return self._current_view(get_spec("raw.calendar"), filters, order_by="market, date")

    # -- reference dictionaries (mutable single-file) ------------------------
    def read_symbols(self, symbols=None, active_only=False, extras_as_columns=None) -> pa.Table:
        filters = []
        if (c := _in_clause("symbol", symbols)):
            filters.append(c)
        if active_only:
            filters.append("is_active = true")
        return self._read_reference(get_spec("reference.symbols"), filters, extras_as_columns,
                                    order_by="symbol")

    def read_sectors(self, classification_system=None, level=None) -> pa.Table:
        filters = []
        if classification_system is not None:
            filters.append(f"classification_system = {_str_lit(classification_system)}")
        if level is not None:
            filters.append(f"level = {int(level)}")
        return self._read_reference(get_spec("reference.sectors"), filters,
                                    order_by="classification_system, code")

    def _read_reference(self, spec, filters, extras_as_columns=None, order_by=None) -> pa.Table:
        self._validate_extras_types(extras_as_columns)
        path = storage.single_file_path(spec, self.root)
        if not path.exists():
            return self._empty(spec, extras_as_columns)
        present = set(spec.all_names())
        extra_select, overridden = self._extras_select(extras_as_columns or {}, spec, present)
        except_sql = f" EXCLUDE ({', '.join(_ident(c) for c in overridden)})" if overridden else ""
        where_sql = (" WHERE " + " AND ".join(filters)) if filters else ""
        order_sql = f" ORDER BY {order_by}" if order_by else ""
        sql = (
            f"SELECT *{except_sql}{extra_select} FROM read_parquet({_str_lit(str(path))})"
            f"{where_sql}{order_sql}"
        )
        return self._con.execute(sql).to_arrow_table()

    # -- generic escape hatch (used by governance.diff_sources + paged /rows)
    def current_view(self, table_name: str, filters: list[str] | None = None,
                     extras_as_columns=None, order_by=None,
                     post_filters: list[str] | None = None,
                     dedup_order: str | None = None,
                     limit: int | None = None, offset: int = 0) -> pa.Table:
        return self._current_view(
            get_spec(table_name), filters, extras_as_columns, order_by,
            dedup_order=dedup_order, post_filters=post_filters,
            limit=limit, offset=offset,
        )

    # -- web-platform read paths (doc §7.5 / §7.6 / §7.8) --------------------
    def _batches_arrow(self) -> pa.Table:
        """All batch ledger rows as an arrow table for joining onto raw rows.

        Carries the columns the full-view / lineage paths need to explain why a
        physical row is or is not part of the current view.
        """
        rows = batches.list_batches(root=self.root)
        cols = {
            "batch_id": [r["batch_id"] for r in rows],
            "_status": [r["status"] for r in rows],
            "_invalidated_at": [r.get("invalidated_at") for r in rows],
            "_invalidated_reason": [r.get("invalidated_reason") for r in rows],
            "_superseded_by": [r.get("superseded_by") for r in rows],
            "_finished_at": [r.get("finished_at") for r in rows],
        }
        return pa.table(
            {
                "batch_id": pa.array(cols["batch_id"], pa.string()),
                "_status": pa.array(cols["_status"], pa.string()),
                "_invalidated_at": pa.array(cols["_invalidated_at"], TS),
                "_invalidated_reason": pa.array(cols["_invalidated_reason"], pa.string()),
                "_superseded_by": pa.array(cols["_superseded_by"], pa.string()),
                "_finished_at": pa.array(cols["_finished_at"], TS),
            }
        )

    def read_batch(self, batch_id: str, table: str,
                   limit: int | None = None, offset: int = 0) -> pa.Table:
        """Every row a single batch wrote, bypassing the current view (doc §7.5).

        Used by the batch-detail "written rows" preview and the table browser's
        single-batch view -- shows the raw file content even after it has been
        invalidated or superseded. ``limit``/``offset`` are pushed into SQL so
        a multi-million-row batch can be previewed without materialising it.
        """
        spec = get_spec(table)
        files = storage.list_data_files(spec, self.root)
        if not files:
            return self._empty(spec, None)
        sql = (
            "SELECT * EXCLUDE (" + ", ".join(_ident(p) for p in spec.partition_names()) + ") "
            if spec.partition_names()
            else "SELECT * "
        )
        order = ", ".join(spec.primary_key)
        page_sql = f" LIMIT {int(limit)} OFFSET {int(offset)}" if limit is not None else ""
        q = (
            f"{sql} FROM read_parquet(?, hive_partitioning=true, union_by_name=true) "
            f"WHERE _batch_id = {_str_lit(batch_id)} ORDER BY {order}{page_sql}"
        )
        return self._con.execute(q, [files]).to_arrow_table()

    def count_batch(self, batch_id: str, table: str) -> int:
        """Total rows a batch wrote (for the ``total`` field next to read_batch)."""
        spec = get_spec(table)
        files = storage.list_data_files(spec, self.root)
        if not files:
            return 0
        q = (
            "SELECT COUNT(*) FROM read_parquet(?, hive_partitioning=true, union_by_name=true) "
            f"WHERE _batch_id = {_str_lit(batch_id)}"
        )
        return int(self._con.execute(q, [files]).fetchone()[0])

    def read_full_view(
        self, table: str, filters: list[str] | None = None, order_by: str | None = None,
        limit: int | None = None, offset: int = 0,
    ) -> pa.Table:
        """All physical rows incl. failed/invalidated/superseded (doc §7.5).

        Appends ``_status`` / ``_invalidated_at`` / ``_superseded_by`` so the
        audit ("全量视图") can show which rows are no longer effective.

        ``limit`` / ``offset`` push pagination into SQL so the export/browse path
        materialises one page instead of the whole table. Unlike the current
        view this keeps EVERY physical row (no dedup), so a large symbol set used
        to blow the Python heap (~8GB at 3000 symbols) even though only a page
        was shown; the page-stable order (PK + _batch_id) makes paging safe here.
        """
        spec = get_spec(table)
        files = storage.list_data_files(spec, self.root)
        if not files:
            return self._empty(spec, None)
        con = self._con
        con.register("_all_batches", self._batches_arrow())
        where = " AND ".join(filters) if filters else "1 = 1"
        excludes = spec.partition_names()
        except_sql = (
            f" EXCLUDE ({', '.join(_ident(c) for c in excludes)})" if excludes else ""
        )
        order_sql = f" ORDER BY {order_by}" if order_by else ""
        # LIMIT/OFFSET literals validated as ints -> no injection risk.
        page_sql = f" LIMIT {int(limit)} OFFSET {int(offset)}" if limit is not None else ""
        sql = f"""
        SELECT t.*{except_sql},
               b._status, b._invalidated_at, b._superseded_by
        FROM read_parquet(?, hive_partitioning=true, union_by_name=true) t
        LEFT JOIN _all_batches b ON t._batch_id = b.batch_id
        WHERE {where}{order_sql}{page_sql}
        """
        return con.execute(sql, [files]).to_arrow_table()

    def count_full_view(self, table: str, filters: list[str] | None = None) -> int:
        """Count physical rows the full view would return (for the paged total).

        Mirrors read_full_view's WHERE without the batch-ledger join (filters
        never reference it) so the ``total`` is filled without materialising the
        rows -- same count+page discipline the current view uses.
        """
        spec = get_spec(table)
        files = storage.list_data_files(spec, self.root)
        if not files:
            return 0
        where = " AND ".join(filters) if filters else "1 = 1"
        sql = (
            "SELECT COUNT(*) FROM read_parquet(?, hive_partitioning=true, "
            f"union_by_name=true) t WHERE {where}"
        )
        return int(self._con.execute(sql, [files]).fetchone()[0])

    def list_extras_keys(self, table: str) -> list[str]:
        """Distinct keys ever seen in a table's ``_extras`` MAP (doc §7.6)."""
        spec = get_spec(table)
        if not spec.has_extras():
            return []
        if spec.mutable:
            path = storage.single_file_path(spec, self.root)
            if not path.exists():
                return []
            src = f"read_parquet({_str_lit(str(path))})"
        else:
            files = storage.list_data_files(spec, self.root)
            if not files:
                return []
            src = "read_parquet(?, hive_partitioning=true, union_by_name=true)"
        sql = (
            f"SELECT DISTINCT k FROM (SELECT unnest(map_keys(_extras)) AS k FROM {src}) "
            f"WHERE k IS NOT NULL ORDER BY k"
        )
        params = [files] if not spec.mutable else []
        return [r[0] for r in self._con.execute(sql, params).fetchall()]

    def distinct_values(self, table: str, field: str) -> list:
        """Distinct values of a single (low-cardinality) column, sorted.

        Backs the table browser's enum filter dropdowns (exchange / market /
        classification_system / action_type / period_type / …). Only ever called
        for columns the route has white-listed as enum filters -- never for
        high-cardinality columns like ``symbol`` -- so this is a cheap
        single-column scan, not a full-table materialise. Reference tables read
        their single file; raw tables push ``DISTINCT`` into DuckDB over the
        parquet set (columnar, one column only).
        """
        spec = get_spec(table)
        col = _ident(field)
        if spec.mutable:
            path = storage.single_file_path(spec, self.root)
            if not path.exists():
                return []
            sql = (
                f"SELECT DISTINCT {col} AS v FROM read_parquet({_str_lit(str(path))}) "
                f"WHERE {col} IS NOT NULL ORDER BY v"
            )
            return [r[0] for r in self._con.execute(sql).fetchall()]
        files = storage.list_data_files(spec, self.root)
        if not files:
            return []
        sql = (
            f"SELECT DISTINCT {col} AS v "
            f"FROM read_parquet(?, hive_partitioning=true, union_by_name=true) "
            f"WHERE {col} IS NOT NULL ORDER BY v"
        )
        return [r[0] for r in self._con.execute(sql, [files]).fetchall()]

    def read_row_lineage(self, table: str, pk_values: dict) -> dict:
        """Every historical write of one primary key, across all batches (doc §7.8).

        Returns the currently-effective record (replaying the current-view dedup
        rule), the full write history newest-first, and the columns whose values
        diverge across writes.

        Mutable reference/metadata tables are single-file, upsert-in-place and
        carry no batch/invalidation chain -- there is no append-only history to
        replay. For them we return a provenance payload (current effective value
        + ``_updated_at`` / ``_source`` / ``_extras``) instead of forcing the
        batch-lineage SQL, which would reference ``_batch_id`` / ``_ingested_at``
        columns these tables don't have (the reference.symbols INTERNAL_ERROR).
        """
        spec = get_spec(table)
        missing = [k for k in spec.primary_key if k not in pk_values]
        if missing:
            raise ValueError(f"{table} lineage needs PK columns {list(spec.primary_key)}; "
                             f"missing {missing}")
        if spec.mutable:
            return self._read_reference_provenance(spec, pk_values)
        files = storage.list_data_files(spec, self.root)
        empty = {"pk": dict(pk_values), "current": None, "history": [], "diff_columns": []}
        if not files:
            return empty

        con = self._con
        con.register("_all_batches", self._batches_arrow())
        prio = sources.priority_map(self.root)
        con.register(
            "_src_prio2",
            pa.table({
                "source_id": pa.array(list(prio) or [None], pa.string()),
                "_priority": pa.array(list(prio.values()) or [None], pa.int32()),
            }),
        )
        pk_filters = []
        for k in spec.primary_key:
            v = pk_values[k]
            typ = spec.type_of(k)
            if typ == DATE:
                pk_filters.append(f"t.{_ident(k)} = {_date_lit(v)}")
            elif typ == TS:
                pk_filters.append(f"t.{_ident(k)} = {_ts_lit(v)}")
            else:
                pk_filters.append(f"t.{_ident(k)} = {_str_lit(v)}")
        excludes = spec.partition_names()
        except_sql = f" EXCLUDE ({', '.join(_ident(c) for c in excludes)})" if excludes else ""
        sql = f"""
        SELECT t.*{except_sql},
               b._status, b._invalidated_at, b._invalidated_reason, b._superseded_by,
               COALESCE(p._priority, -1) AS _priority
        FROM read_parquet(?, hive_partitioning=true, union_by_name=true) t
        LEFT JOIN _all_batches b ON t._batch_id = b.batch_id
        LEFT JOIN _src_prio2 p ON t._source = p.source_id
        WHERE {' AND '.join(pk_filters)}
        ORDER BY t._ingested_at DESC, t._batch_id DESC
        """
        rows = con.execute(sql, [files]).to_arrow_table().to_pylist()
        if not rows:
            return empty

        # currently-effective row: replay the current-view ranking (success only,
        # source priority then most-recent ingest then batch id as tiebreak).
        effective = [r for r in rows if r.get("_status") == "success"]
        effective.sort(
            key=lambda r: (
                r.get("_priority") if r.get("_priority") is not None else -1,
                r.get("_ingested_at") or dt.datetime.min,
                r.get("_batch_id") or "",
            ),
            reverse=True,
        )
        current_batch = effective[0]["_batch_id"] if effective else None

        business = [c for c in spec.business_names() if c not in spec.primary_key]
        diff_columns = [
            c for c in business
            if len({_hashable(r.get(c)) for r in rows}) > 1
        ]

        def shape(r: dict) -> dict:
            return {
                "batch_id": r.get("_batch_id"),
                "source": r.get("_source"),
                "ingested_at": r.get("_ingested_at"),
                "status": r.get("_status") or "success",
                "is_current": r.get("_batch_id") == current_batch,
                "invalidated_at": r.get("_invalidated_at"),
                "invalidated_reason": r.get("_invalidated_reason"),
                "superseded_by": r.get("_superseded_by"),
                "priority": None if r.get("_priority") in (None, -1) else r.get("_priority"),
                "values": {c: r.get(c) for c in spec.business_names()},
            }

        history = [shape(r) for r in rows]
        current = next((h for h in history if h["is_current"]), None)
        return {
            "pk": dict(pk_values),
            "current": current,
            "history": history,
            "diff_columns": diff_columns,
            "lineage_kind": "batch",
            "mutable": False,
        }

    def _read_reference_provenance(self, spec: TableSpec, pk_values: dict) -> dict:
        """Provenance for a single mutable (upsert-in-place) reference row.

        No batch/invalidation chain exists for these tables, so the payload
        stays shape-compatible with batch lineage (``pk`` / ``current`` /
        ``history`` / ``diff_columns``) but ``current`` carries the live values
        plus ``_updated_at`` / ``_source`` / ``_extras`` and ``history`` is empty.
        """
        base = {
            "pk": dict(pk_values), "current": None, "history": [],
            "diff_columns": [], "lineage_kind": "reference", "mutable": True,
        }
        path = storage.single_file_path(spec, self.root)
        if not path.exists():
            return base
        where = " AND ".join(
            f"{_ident(k)} = {self._pk_literal(spec, k, pk_values[k])}"
            for k in spec.primary_key
        )
        rows = self._con.execute(
            f"SELECT * FROM read_parquet({_str_lit(str(path))}) WHERE {where} LIMIT 1"
        ).to_arrow_table().to_pylist()
        if not rows:
            return base
        r = rows[0]
        base["current"] = {
            "values": {c: r.get(c) for c in spec.business_names()},
            "updated_at": r.get("_updated_at"),
            "source": r.get("_source"),
            "extras": r.get("_extras") if spec.has_extras() else None,
        }
        return base

    @staticmethod
    def _pk_literal(spec: TableSpec, col: str, value) -> str:
        typ = spec.type_of(col)
        if typ == DATE:
            return _date_lit(value)
        if typ == TS:
            return _ts_lit(value)
        return _str_lit(value)
