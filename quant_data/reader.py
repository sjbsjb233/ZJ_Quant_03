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

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from . import config, storage
from .metadata import batches, sources
from .schemas import ARROW_TYPE_BY_NAME, TableSpec, get_spec

_PRICE_COLS = ("open", "high", "low", "close", "pre_close", "vwap")


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
        self._con = duckdb.connect()

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
    ) -> pa.Table:
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
        FROM ranked WHERE rn = 1{order_sql}
        """
        return con.execute(sql, [files]).to_arrow_table()

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

    # -- generic escape hatch (used by governance.diff_sources) --------------
    def current_view(self, table_name: str, filters: list[str] | None = None,
                     extras_as_columns=None, order_by=None) -> pa.Table:
        return self._current_view(get_spec(table_name), filters, extras_as_columns, order_by)
