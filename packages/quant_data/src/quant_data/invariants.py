"""Data-quality invariant sweeper (doc §8).

⚠ 禁止在服务进程调用 (内存治理方案 WP1.2)
==========================================
本模块是离线/测试用的全库扫描器:每条 invariant 都是一次全表级引擎扫描,
CPU 重、IO 重。接进 web/keeper/factor 任一服务进程 = 复刻 2026-06-01
table_stats 崩溃循环。CI 对 ``apps/`` 下的 ``quant_data.invariants`` 引用
直接判失败;``quant_data.__init__`` 也不再导出本模块。

Invariants are conditions that must hold in *any* state. Each is expressed as a
query; a violation reports the count + a small sample. Severity is FATAL (data
is corrupt / unusable) or WARN (suspicious, investigate). Intended to run as a
daily job over the full dataset; also used in tests to prove injected defects
are caught.

Scope decisions:
  * Value/logic invariants run over the **current view** (valid, deduped) -- an
    invalidated batch is allowed to contain the bad data that got it invalidated.
  * Structural invariants (_batch_id/_source presence, orphan files) run over the
    **physical files**, so they catch corruption even inside valid batches.

内存治理 (WP1.2 长期改写): the sweep used to materialise every table's full
current view into the Python heap (``con.register`` of Arrow tables) and
``.to_pylist()`` every violation set (~15GB on prod). The big raw tables are
now exposed as DuckDB **temp views** over :meth:`DataReader.current_view_sql`
(zero materialisation; multi-table joins run inside the engine under
``memory_limit``), and every check fetches COUNT(*) + a 5-row sample instead
of the whole violation set. ``to_pylist`` survives only on the ≤5-row samples.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import storage
from .metadata import batches as batches_mod
from .metadata import field_registry as fr_mod
from .metadata import sources as sources_mod
from .reader import DataReader
from .schemas import BATCHES, FIELD_REGISTRY, RAW_TABLES, SOURCES, get_spec

FATAL = "FATAL"
WARN = "WARN"

# tolerance for stored-vs-derived adj_factor comparison (INV-202)
_ADJ_TOL = 1e-6


@dataclass
class Violation:
    inv: str
    severity: str
    table: str
    message: str
    count: int
    samples: list = field(default_factory=list)

    def __str__(self) -> str:
        return f"[{self.severity}] {self.inv} {self.table}: {self.message} (count={self.count})"


def _viol(con, inv, sev, table, msg, sql, params=None) -> Violation | None:
    """COUNT(*) + a 5-row sample of the violating set — never the whole set.

    The violation query itself can match millions of rows; pulling them all
    through ``.to_pylist()`` was one of the WP1.2 blow-up points. Counting and
    sampling are two engine passes over the same (pushed-down) query.
    """
    n = int(con.execute(f"SELECT COUNT(*) FROM ({sql})", params or []).fetchone()[0])
    if not n:
        return None
    samples = (
        con.execute(f"SELECT * FROM ({sql}) LIMIT 5", params or [])
        .to_arrow_table()
        .to_pylist()
    )
    return Violation(inv, sev, table, msg, n, samples)


class _Ctx:
    """Exposes current views as DuckDB TEMP VIEWS + small metadata relations.

    Big raw tables (bd/fd/un/cal/ca) are temp views over the self-contained
    current-view SQL — nothing is materialised; every invariant (including the
    bd×un / bd×cal joins) runs wholly inside the engine. Mutable reference
    dictionaries (sym/sec) are single small files and stay registered tables.
    """

    def __init__(self, root=None):
        self.root = root
        self.reader = DataReader(root=root)
        self.con = self.reader._con
        self._view("bd", "raw.bars_daily")
        self._view("fd", "raw.fundamentals")
        self._view("un", "raw.universe")
        self._view("cal", "raw.calendar")
        self._view("ca", "raw.corporate_actions")
        self._reg("sym", self.reader.read_symbols())
        self._reg("sec", self.reader.read_sectors())
        # metadata via the table spec so empty tables still carry the right columns
        self._reg("batches", storage.normalize_to_arrow(BATCHES, batches_mod.list_batches(root=root)))
        self._reg("sources", storage.normalize_to_arrow(SOURCES, sources_mod.list_sources(root=root)))
        self._reg("fieldreg", storage.normalize_to_arrow(FIELD_REGISTRY, fr_mod.list_fields(root=root)))

    def _view(self, name, table):
        sql = self.reader.current_view_sql(table)
        if sql is None:  # empty view → schema-correct empty relation
            self._reg(name, self.reader._empty(get_spec(table), None))
        else:
            self.con.execute(f"CREATE OR REPLACE TEMP VIEW {name} AS {sql}")

    def _reg(self, name, tbl):
        self.con.register(name, tbl)


# ---------------------------------------------------------------------------
def check_all(root=None) -> list[Violation]:
    ctx = _Ctx(root=root)
    con = ctx.con
    out: list[Violation] = []

    # --- §8.1 raw value/logic invariants (current view) --------------------
    out.append(_viol(con, "INV-004", FATAL, "raw.bars_daily",
                     "(date,symbol) not unique in current view",
                     "SELECT date,symbol,count(*) c FROM bd GROUP BY 1,2 HAVING count(*)>1"))
    out.append(_viol(con, "INV-005", WARN, "raw.bars_daily",
                     "bar date is not a trading day",
                     "SELECT b.date,b.symbol FROM bd b LEFT JOIN cal c "
                     "ON b.date=c.date AND c.market='CN-A' "
                     "WHERE c.is_trading_day IS DISTINCT FROM true"))
    out.append(_viol(con, "INV-006", FATAL, "raw.bars_daily",
                     "symbol not in reference.symbols",
                     "SELECT DISTINCT b.symbol FROM bd b LEFT JOIN sym s USING(symbol) "
                     "WHERE s.symbol IS NULL"))
    out.append(_viol(con, "INV-007", FATAL, "raw.bars_daily",
                     "volume<0 or amount<0",
                     "SELECT date,symbol,volume,amount FROM bd WHERE volume<0 OR amount<0"))
    out.append(_viol(con, "INV-008", FATAL, "raw.bars_daily",
                     "OHLC ordering violated",
                     "SELECT date,symbol FROM bd WHERE high<low OR high<open OR high<close "
                     "OR low>open OR low>close"))
    out.append(_viol(con, "INV-009", FATAL, "raw.fundamentals",
                     "publish_date < report_period",
                     "SELECT symbol,report_period,publish_date FROM fd "
                     "WHERE publish_date < report_period"))
    out.append(_viol(con, "INV-010", WARN, "raw.universe",
                     "is_tradable inconsistent with listed/suspended/st",
                     "SELECT date,symbol FROM un WHERE is_tradable AND "
                     "(NOT is_listed OR is_suspended OR st_status='delisted')"))
    out.append(_viol(con, "INV-011", FATAL, "raw.corporate_actions",
                     "announce_date > action_date",
                     "SELECT symbol,action_date,announce_date FROM ca "
                     "WHERE announce_date > action_date"))
    out.append(_viol(con, "INV-012", FATAL, "raw.calendar",
                     "prev/next_trading_day bracket violated",
                     "SELECT date,market FROM cal WHERE (prev_trading_day IS NOT NULL "
                     "AND prev_trading_day>=date) OR (next_trading_day IS NOT NULL "
                     "AND next_trading_day<=date)"))

    # --- §8.1 structural invariants (physical files) -----------------------
    out += _structural(con, root)

    # --- §8.2 metadata invariants ------------------------------------------
    out.append(_viol(con, "INV-101", FATAL, "metadata.batches",
                     "finished_at < started_at",
                     "SELECT batch_id FROM batches WHERE finished_at IS NOT NULL "
                     "AND finished_at<started_at"))
    out.append(_viol(con, "INV-102", FATAL, "metadata.batches",
                     "invalidated_at < finished_at",
                     "SELECT batch_id FROM batches WHERE invalidated_at IS NOT NULL "
                     "AND finished_at IS NOT NULL AND invalidated_at<finished_at"))
    out.append(_viol(con, "INV-103", FATAL, "metadata.batches",
                     "success batch with NULL row_count",
                     "SELECT batch_id FROM batches WHERE status='success' AND row_count IS NULL"))
    out.append(_viol(con, "INV-104", FATAL, "metadata.batches",
                     "superseded_by points to unknown batch",
                     "SELECT b.batch_id FROM batches b LEFT JOIN batches t "
                     "ON b.superseded_by=t.batch_id "
                     "WHERE b.superseded_by IS NOT NULL AND t.batch_id IS NULL"))
    out += _supersede_cycles(root)
    out.append(_viol(con, "INV-107", FATAL, "metadata.field_registry",
                     "field status not in state machine",
                     "SELECT table_name,field_name,status FROM fieldreg "
                     "WHERE status NOT IN ('extras','promoted','core','deprecated')"))

    # --- §8.3 cross-table invariants ---------------------------------------
    out.append(_viol(con, "INV-201", WARN, "raw.bars_daily",
                     "volume=0 but not suspended (or suspended but volume>0)",
                     "SELECT b.date,b.symbol FROM bd b JOIN un u "
                     "ON b.date=u.date AND b.symbol=u.symbol "
                     "WHERE (b.volume=0) <> (u.is_suspended)"))
    out.append(_viol(con, "INV-203", FATAL, "raw.bars_daily",
                     "bar after symbol delist_date",
                     "SELECT b.date,b.symbol FROM bd b JOIN sym s USING(symbol) "
                     "WHERE s.delist_date IS NOT NULL AND b.date>s.delist_date"))
    out.append(_adj_factor_consistency(ctx))

    return [v for v in out if v is not None]


def _structural(con, root) -> list[Violation]:
    out = []
    for spec in RAW_TABLES.values():
        files = storage.list_data_files(spec, root=root)
        if not files:
            continue
        rel = "read_parquet(?, hive_partitioning=true, union_by_name=true)"
        out.append(_viol(con, "INV-001", FATAL, spec.name, "_batch_id IS NULL",
                         f"SELECT _batch_id FROM {rel} WHERE _batch_id IS NULL", [files]))
        out.append(_viol(con, "INV-002", FATAL, spec.name, "_batch_id not in metadata.batches",
                         f"SELECT DISTINCT t._batch_id FROM {rel} t "
                         "LEFT JOIN batches b ON t._batch_id=b.batch_id "
                         "WHERE b.batch_id IS NULL", [files]))
        out.append(_viol(con, "INV-003", FATAL, spec.name, "_source not in metadata.sources",
                         f"SELECT DISTINCT t._source FROM {rel} t "
                         "LEFT JOIN sources s ON t._source=s.source_id "
                         "WHERE s.source_id IS NULL", [files]))
    return out


def _supersede_cycles(root) -> list[Violation]:
    rows = batches_mod.list_batches(root=root)
    nxt = {r["batch_id"]: r.get("superseded_by") for r in rows}
    in_cycle = set()
    for start in nxt:
        seen, cur = [], start
        while cur is not None and cur in nxt and cur not in seen:
            seen.append(cur)
            cur = nxt[cur]
        if cur is not None and cur in seen:  # looped back
            in_cycle.update(seen[seen.index(cur):])
    if in_cycle:
        return [Violation("INV-105", FATAL, "metadata.batches",
                          "supersede chain contains a cycle", len(in_cycle), sorted(in_cycle)[:5])]
    return [None]


def _adj_factor_consistency(ctx: _Ctx) -> Violation | None:
    """INV-202: stored adj_factor vs corporate_actions-derived — in-engine.

    The legacy path ``.to_pylist()``-ed BOTH the full bars table and the full
    derived table into Python dicts and diffed them row by row. Now the derived
    table (3 columns, the unavoidable output of :func:`compute_adj_factors`,
    itself engine-computed per WP1.1) is registered and compared in SQL; only
    COUNT + 5 samples ever reach Python.
    """
    from . import corporate

    derived = corporate.compute_adj_factors(reader=ctx.reader, root=ctx.root)
    ctx.con.register("_inv_derived", derived)
    return _viol(
        ctx.con, "INV-202", WARN, "raw.bars_daily",
        "stored adj_factor != corporate_actions-derived",
        "SELECT b.symbol, CAST(b.date AS VARCHAR) AS date, "
        "b.adj_factor AS stored, d.adj_factor AS derived "
        "FROM bd b JOIN _inv_derived d USING (symbol, date) "
        "WHERE b.adj_factor IS NOT NULL AND d.adj_factor IS NOT NULL "
        f"AND abs(b.adj_factor - d.adj_factor) > {_ADJ_TOL}",
    )


def fatal(root=None) -> list[Violation]:
    return [v for v in check_all(root=root) if v.severity == FATAL]
