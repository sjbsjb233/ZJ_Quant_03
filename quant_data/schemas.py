"""Single source of truth for every table's column-level schema.

Mirrors the design doc:
  * raw/ tables (append-only, partitioned)            -- doc §3
  * reference/ tables (mutable single-file dicts)       -- doc §5
  * metadata/ tables (mutable single-file ledgers)      -- doc §2

The four common metadata columns (doc §1.1) are appended to every raw table
here so the per-table definitions only list business columns.
"""
from __future__ import annotations

from dataclasses import dataclass

import pyarrow as pa

# --- arrow type aliases ------------------------------------------------------
STR = pa.string()
DOUBLE = pa.float64()
BIGINT = pa.int64()
INT = pa.int32()
BOOL = pa.bool_()
DATE = pa.date32()
TS = pa.timestamp("us")  # naive UTC microseconds; maps to DuckDB TIMESTAMP
MAP = pa.map_(pa.string(), pa.string())  # _extras KV bag (doc §1.2)

# arrow type -> DuckDB/registry type name (used by field_registry.data_type)
DUCK_TYPE_NAME: dict[pa.DataType, str] = {
    STR: "VARCHAR",
    DOUBLE: "DOUBLE",
    BIGINT: "BIGINT",
    INT: "INTEGER",
    BOOL: "BOOLEAN",
    DATE: "DATE",
    TS: "TIMESTAMP",
}
# inverse, for promote-to-core casts driven by registry data_type strings
ARROW_TYPE_BY_NAME: dict[str, pa.DataType] = {v: k for k, v in DUCK_TYPE_NAME.items()}

# --- common metadata column blocks (doc §1.1) -------------------------------
# raw rows carry these 4; the leading "_" visually separates them from business
RAW_META = (
    ("_batch_id", STR, False),
    ("_source", STR, False),
    ("_ingested_at", TS, False),
    ("_extras", MAP, True),
)
# reference dictionaries: static, mutable, carry provenance + extras
REF_META_WITH_EXTRAS = (
    ("_extras", MAP, True),
    ("_updated_at", TS, False),
    ("_source", STR, False),
)
REF_META_NO_EXTRAS = (
    ("_updated_at", TS, False),
    ("_source", STR, False),
)


@dataclass(frozen=True)
class PartitionCol:
    """A hive partition column derived from a business column (doc §1.4)."""

    name: str
    source: str
    transform: str  # 'year' | 'month' | 'quarter'


@dataclass(frozen=True)
class TableSpec:
    name: str  # logical name, e.g. 'raw.bars_daily'
    rel_path: str  # path under data root, e.g. 'raw/bars_daily'
    kind: str  # 'raw' | 'reference' | 'metadata' | 'derived'
    primary_key: tuple[str, ...]
    business_columns: tuple[tuple[str, pa.DataType, bool], ...]
    meta_columns: tuple[tuple[str, pa.DataType, bool], ...] = ()
    partition: tuple[PartitionCol, ...] = ()
    mutable: bool = False  # metadata + reference rewrite in place; raw is append-only

    # -- column helpers -------------------------------------------------------
    @property
    def columns(self) -> tuple[tuple[str, pa.DataType, bool], ...]:
        return self.business_columns + self.meta_columns

    def business_names(self) -> list[str]:
        return [c[0] for c in self.business_columns]

    def meta_names(self) -> list[str]:
        return [c[0] for c in self.meta_columns]

    def all_names(self) -> list[str]:
        return [c[0] for c in self.columns]

    def partition_names(self) -> list[str]:
        return [p.name for p in self.partition]

    def has_extras(self) -> bool:
        return "_extras" in self.meta_names()

    def type_of(self, col: str) -> pa.DataType:
        for n, t, _ in self.columns:
            if n == col:
                return t
        raise KeyError(f"{self.name} has no column {col!r}")

    # -- arrow schemas --------------------------------------------------------
    def arrow_schema(self) -> pa.Schema:
        """Business + meta columns (no partition helper columns)."""
        return pa.schema([pa.field(n, t, nullable=null) for n, t, null in self.columns])

    def write_schema(self) -> pa.Schema:
        """Business + meta + partition helper columns (what hits disk)."""
        fields = [pa.field(n, t, nullable=null) for n, t, null in self.columns]
        existing = set(self.all_names())
        for p in self.partition:
            if p.name not in existing:
                fields.append(pa.field(p.name, INT, nullable=False))
        return pa.schema(fields)


# ============================================================================
# raw/ tables (doc §3) -- append-only, partitioned, full _extras governance
# ============================================================================

BARS_DAILY = TableSpec(
    name="raw.bars_daily",
    rel_path="raw/bars_daily",
    kind="raw",
    primary_key=("date", "symbol"),
    business_columns=(
        ("date", DATE, False),
        ("symbol", STR, False),
        ("open", DOUBLE, True),
        ("high", DOUBLE, True),
        ("low", DOUBLE, True),
        ("close", DOUBLE, True),
        ("pre_close", DOUBLE, True),
        ("volume", BIGINT, True),
        ("amount", DOUBLE, True),
        ("vwap", DOUBLE, True),
        ("adj_factor", DOUBLE, True),
    ),
    meta_columns=RAW_META,
    partition=(
        PartitionCol("year", "date", "year"),
        PartitionCol("month", "date", "month"),
    ),
)

BARS_MINUTE = TableSpec(
    name="raw.bars_minute",
    rel_path="raw/bars_minute",
    kind="raw",
    primary_key=("datetime", "symbol"),
    business_columns=(
        ("datetime", TS, False),
        ("symbol", STR, False),
        ("frequency", STR, False),  # currently always '1m' (doc §3.2 / §6.4)
        ("open", DOUBLE, True),
        ("high", DOUBLE, True),
        ("low", DOUBLE, True),
        ("close", DOUBLE, True),
        ("volume", BIGINT, True),
        ("amount", DOUBLE, True),
        ("vwap", DOUBLE, True),
        ("adj_factor", DOUBLE, True),
    ),
    meta_columns=RAW_META,
    partition=(
        PartitionCol("year", "datetime", "year"),
        PartitionCol("month", "datetime", "month"),
    ),
)

FUNDAMENTALS = TableSpec(
    name="raw.fundamentals",
    rel_path="raw/fundamentals",
    kind="raw",
    primary_key=("symbol", "report_period", "period_type"),
    business_columns=(
        ("symbol", STR, False),
        ("report_period", DATE, False),
        ("period_type", STR, False),
        ("publish_date", DATE, False),  # PIT key (doc §3.3)
        ("currency", STR, False),
        ("revenue", DOUBLE, True),
        ("operating_income", DOUBLE, True),
        ("net_income", DOUBLE, True),
        ("net_income_attr_parent", DOUBLE, True),
        ("total_assets", DOUBLE, True),
        ("total_liabilities", DOUBLE, True),
        ("total_equity", DOUBLE, True),
        ("equity_attr_parent", DOUBLE, True),
        ("eps_basic", DOUBLE, True),
        ("eps_diluted", DOUBLE, True),
        ("bps", DOUBLE, True),
        ("operating_cash_flow", DOUBLE, True),
    ),
    meta_columns=RAW_META,
    partition=(
        PartitionCol("year", "report_period", "year"),
        PartitionCol("quarter", "report_period", "quarter"),
    ),
)

UNIVERSE = TableSpec(
    name="raw.universe",
    rel_path="raw/universe",
    kind="raw",
    primary_key=("date", "symbol"),
    business_columns=(
        ("date", DATE, False),
        ("symbol", STR, False),
        ("is_tradable", BOOL, False),
        ("is_listed", BOOL, False),
        ("is_suspended", BOOL, False),
        ("st_status", STR, False),
        ("in_hs300", BOOL, False),
        ("in_zz500", BOOL, False),
        ("in_zz1000", BOOL, False),
        ("in_sse50", BOOL, False),
        ("limit_up_status", STR, True),
        ("listed_days", INT, True),
    ),
    meta_columns=RAW_META,
    partition=(PartitionCol("year", "date", "year"),),
)

CALENDAR = TableSpec(
    name="raw.calendar",
    rel_path="raw/calendar",
    kind="raw",
    primary_key=("date", "market"),
    business_columns=(
        ("date", DATE, False),
        ("market", STR, False),
        ("is_trading_day", BOOL, False),
        ("prev_trading_day", DATE, True),
        ("next_trading_day", DATE, True),
        ("trading_session", STR, True),
        ("month_end_trading_day", BOOL, False),
        ("quarter_end_trading_day", BOOL, False),
        ("year_end_trading_day", BOOL, False),
        ("holiday_name", STR, True),
    ),
    meta_columns=RAW_META,
    partition=(),  # tiny, unpartitioned (doc §1.4)
)

CORPORATE_ACTIONS = TableSpec(
    name="raw.corporate_actions",
    rel_path="raw/corporate_actions",
    kind="raw",
    primary_key=("symbol", "action_date", "action_type"),
    business_columns=(
        ("symbol", STR, False),
        ("action_date", DATE, False),
        ("announce_date", DATE, True),
        ("record_date", DATE, True),
        ("action_type", STR, False),
        ("cash_per_share", DOUBLE, True),
        ("stock_per_10", DOUBLE, True),
        ("transfer_per_10", DOUBLE, True),
        ("rights_ratio", DOUBLE, True),
        ("rights_price", DOUBLE, True),
        ("split_ratio", DOUBLE, True),
    ),
    meta_columns=RAW_META,
    partition=(PartitionCol("year", "action_date", "year"),),
)

# action_type enumeration (doc §3.6)
CORPORATE_ACTION_TYPES = frozenset(
    {
        "cash_dividend",
        "stock_dividend",
        "transfer",
        "rights_issue",
        "split",
        "name_change",
        "suspend",
        "resume",
        "delisting",
    }
)

# ============================================================================
# reference/ tables (doc §5) -- mutable single-file dictionaries (upsert by PK)
# ============================================================================

SYMBOLS = TableSpec(
    name="reference.symbols",
    rel_path="reference/symbols",
    kind="reference",
    primary_key=("symbol",),
    business_columns=(
        ("symbol", STR, False),
        ("name", STR, False),
        ("name_history", STR, True),  # JSON array
        ("exchange", STR, False),
        ("board", STR, True),
        ("sector_code", STR, True),
        ("industry_code", STR, True),
        ("currency", STR, False),
        ("list_date", DATE, True),
        ("delist_date", DATE, True),
        ("is_active", BOOL, False),
    ),
    meta_columns=REF_META_WITH_EXTRAS,
    mutable=True,
)

SECTORS = TableSpec(
    name="reference.sectors",
    rel_path="reference/sectors",
    kind="reference",
    primary_key=("code", "classification_system"),
    business_columns=(
        ("code", STR, False),
        ("name", STR, False),
        ("level", INT, False),
        ("parent_code", STR, True),
        ("classification_system", STR, False),
    ),
    meta_columns=REF_META_NO_EXTRAS,  # no _extras (doc §5.2)
    mutable=True,
)

# ============================================================================
# metadata/ tables (doc §2) -- mutable single-file governance ledgers
# ============================================================================

BATCHES = TableSpec(
    name="metadata.batches",
    rel_path="metadata/batches",
    kind="metadata",
    primary_key=("batch_id",),
    business_columns=(
        ("batch_id", STR, False),
        ("source", STR, False),
        ("target_table", STR, False),
        ("started_at", TS, False),
        ("finished_at", TS, True),
        ("status", STR, False),  # running|success|failed|invalidated
        ("row_count", BIGINT, True),
        ("partition_keys", STR, True),  # JSON array of "k=v/..." strings
        ("parameters", STR, True),  # JSON
        ("parameters_hash", STR, True),  # sha256(parameters)
        ("invalidated_at", TS, True),
        ("invalidated_reason", STR, True),
        ("superseded_by", STR, True),
        ("supersedes", STR, True),  # back-link to the batch this one replaces
        ("error_message", STR, True),
        ("notes", STR, True),
    ),
    mutable=True,
)

SOURCES = TableSpec(
    name="metadata.sources",
    rel_path="metadata/sources",
    kind="metadata",
    primary_key=("source_id",),
    business_columns=(
        ("source_id", STR, False),
        ("display_name", STR, False),
        ("category", STR, False),  # external_api|manual|internal
        ("priority", INT, False),  # higher wins on conflict
        ("is_active", BOOL, False),
        ("field_mapping", STR, True),  # JSON source->standard mapping
        ("rate_limit_per_min", INT, True),
        ("auth_method", STR, True),
        ("endpoint", STR, True),
        ("description", STR, True),
        ("_updated_at", TS, False),
    ),
    mutable=True,
)

FIELD_REGISTRY = TableSpec(
    name="metadata.field_registry",
    rel_path="metadata/field_registry",
    kind="metadata",
    primary_key=("table_name", "field_name"),
    business_columns=(
        ("table_name", STR, False),
        ("field_name", STR, False),
        ("data_type", STR, False),
        ("status", STR, False),  # extras|promoted|core|deprecated
        ("description", STR, False),
        ("unit", STR, True),
        ("first_seen_batch_id", STR, True),
        ("first_seen_at", TS, True),
        ("promoted_at", TS, True),
        ("core_at", TS, True),
        ("deprecated_at", TS, True),
        ("authoritative_source", STR, True),
        ("consumers", STR, True),  # JSON list
        ("notes", STR, True),
    ),
    mutable=True,
)

# --- registries -------------------------------------------------------------
RAW_TABLES = {
    t.name: t
    for t in (BARS_DAILY, BARS_MINUTE, FUNDAMENTALS, UNIVERSE, CALENDAR, CORPORATE_ACTIONS)
}
REFERENCE_TABLES = {t.name: t for t in (SYMBOLS, SECTORS)}
METADATA_TABLES = {t.name: t for t in (BATCHES, SOURCES, FIELD_REGISTRY)}

ALL_TABLES: dict[str, TableSpec] = {**RAW_TABLES, **REFERENCE_TABLES, **METADATA_TABLES}


def get_spec(name: str) -> TableSpec:
    try:
        return ALL_TABLES[name]
    except KeyError:
        raise KeyError(
            f"Unknown table {name!r}. Known: {sorted(ALL_TABLES)}"
        ) from None
