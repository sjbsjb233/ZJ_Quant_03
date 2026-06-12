"""Factor contract — the only API a factor author uses.

A factor declares:
  - param_schema: dict[name, (type, default)]; the engine builds an instance
    by reading these from a payload dict.
  - direction: +1 (higher = better) or -1 (lower = better)
  - requires_pit: True for factors that touch fundamentals; engine then calls
    compute_cross_section(ctx, as_of) once per evaluation day.
  - dependencies: tuple of raw-table names; documented in the import report.

It implements either:
  - compute(ctx) -> pl.DataFrame[date, symbol, value]  (vectorized one-shot)
  - compute_cross_section(ctx, as_of) -> pl.DataFrame[date, symbol, value]
    (per-day, PIT-safe; engine concatenates across rebalance days)

FactorContext is a thin sandbox around DataReader: it ensures factor code
talks to the data layer through governed APIs, returning Polars (zero-copy
from Arrow) so factor code stays one-liners.
"""
from __future__ import annotations

import abc
import datetime as dt
from typing import Any, Iterable

import polars as pl


def _engine_log(event: str, **fields) -> None:
    """Structured engine log via quant_data's chain (no-op if unavailable).

    quant-data is a declared dependency, but the guard keeps reader-agnostic
    test rigs importable in odd environments.
    """
    try:
        from quant_data.logging import get_logger

        get_logger("quant_factor.engine").info(event, **fields)
    except Exception:  # noqa: BLE001 - logging must never break a run
        pass


class RunCancelled(Exception):
    """Raised by the engine cancellation check to unwind a running evaluation."""


# ─────────────────────────────────────────────────────────────────────────────
# FactorContext
# ─────────────────────────────────────────────────────────────────────────────
class FactorContext:
    """Data sandbox passed to factor code.

    Wraps DataReader, converts pyarrow.Table -> pl.DataFrame at the boundary
    (zero-copy, both share Arrow memory), and forces PIT semantics for
    fundamentals (as_of must be supplied).

    内存治理 (《内存治理总体方案》WP3.2) — 面板缓存 + 列投影:
      * ``bars()`` memoises per ``(adjust, frozenset(columns))`` — the engine
        reads the evaluation panel ONCE and every same-口径 re-read (factor
        compute, forward returns, per-day PIT slices) is a cache hit. 每评估
        bars 全量读: H+3 → 1。
      * ``set_default_columns`` carries the engine-computed projection
        (factor.required_columns ∪ 收益价列); factors that did not declare
        required_columns keep the legacy full-column read (回退全列,结果不变,
        不进台账 — 性能行为不污染降级语义, 方案 §2.4)。
      * a projected column the data does not carry is dropped with a log and
        the read retried — same observable behaviour as the legacy full read
        (the column simply wasn't in the frame), so column-fallback factors
        (TurnoverN/LogMktCap) keep their own degradation semantics.
    """

    def __init__(
        self,
        reader,
        symbols: list[str],
        start: dt.date,
        end: dt.date,
        ledger=None,
        default_columns: list[str] | None = None,
    ):
        self._reader = reader
        self.symbols = list(symbols)
        self.start = start
        self.end = end
        # Optional DegradationLedger (设计 §6) so factor code can record a
        # controlled degradation (e.g. a field fallback) rather than silently
        # changing semantics. None during validation / standalone use.
        self.ledger = ledger
        self._default_columns = list(default_columns) if default_columns else None
        self._panel_cache: dict[tuple, pl.DataFrame] = {}

    def set_default_columns(self, columns: list[str] | None) -> None:
        """Engine hook (WP3.1): the projection ``bars()`` uses when the factor
        code doesn't pass one explicitly. ``None`` = legacy full read."""
        self._default_columns = list(columns) if columns else None

    # ── raw / reference reads ────────────────────────────────────────────
    def bars(self, adjust: str = "post", columns: list[str] | None = None) -> pl.DataFrame:
        """Daily bars on [start, end], post-adjusted by default for continuity.

        Cached per (adjust, columns) — repeated same-口径 calls within one
        evaluation cost zero reads (WP3.2). Do NOT mutate the returned frame
        in place (polars ops return new frames, so idiomatic code is safe).
        """
        cols = columns if columns is not None else self._default_columns
        key = (adjust, tuple(sorted(cols)) if cols is not None else None)
        hit = self._panel_cache.get(key)
        if hit is not None:
            return hit
        df = pl.from_arrow(self._read_bars(cols, adjust))
        self._panel_cache[key] = df
        return df

    def _read_bars(self, cols: list[str] | None, adjust: str):
        """Projected read with drop-missing-column retry (see class docstring)."""
        cols = list(cols) if cols is not None else None
        while True:
            try:
                return self._reader.read_bars_daily(
                    symbols=self.symbols, start=self.start, end=self.end,
                    adjust=adjust, columns=cols,
                )
            except TypeError:
                # reader without columns support (older fakes/stubs) → full read
                return self._reader.read_bars_daily(
                    symbols=self.symbols, start=self.start, end=self.end, adjust=adjust,
                )
            except ValueError as e:
                # ColumnNotInViewError subclasses ValueError: a declared-but-
                # not-ingested column (e.g. market-cap pre-backfill). Dropping
                # it reproduces the legacy full read's observable shape — the
                # column was absent there too; factor-level fallbacks fire as
                # before. Any other ValueError re-raises untouched.
                missing = getattr(e, "column", None)
                if not (cols and missing and missing in cols):
                    raise
                cols = [c for c in cols if c != missing]
                _engine_log("factor.columns.missing", column=missing,
                            remaining=list(cols))
                if not cols:
                    cols = None

    def fundamentals(self, as_of: dt.date) -> pl.DataFrame:
        """PIT fundamentals — only rows where publish_date <= as_of are returned."""
        tbl = self._reader.read_fundamentals(symbols=self.symbols, as_of_date=as_of)
        return pl.from_arrow(tbl)

    def universe(self) -> pl.DataFrame:
        tbl = self._reader.read_universe(
            symbols=self.symbols, start=self.start, end=self.end
        )
        return pl.from_arrow(tbl)

    def symbols_ref(self) -> pl.DataFrame:
        tbl = self._reader.read_symbols(symbols=self.symbols)
        return pl.from_arrow(tbl)

    def sectors(
        self, classification_system: str | None = None, level: int | None = None
    ) -> pl.DataFrame:
        tbl = self._reader.read_sectors(
            classification_system=classification_system, level=level
        )
        return pl.from_arrow(tbl)

    def calendar(self) -> pl.DataFrame:
        tbl = self._reader.read_calendar(start=self.start, end=self.end)
        return pl.from_arrow(tbl)


# ─────────────────────────────────────────────────────────────────────────────
# Factor base class
# ─────────────────────────────────────────────────────────────────────────────
class Factor(abc.ABC):
    """Abstract factor — implement either compute() or compute_cross_section().

    Class attributes (override in subclasses):
        category        : "momentum" | "value" | "quality" | "vol" | ...
        direction       : +1 if larger = better, -1 if smaller = better
        requires_pit    : True for fundamental factors; engine slices per day
        dependencies    : raw tables this factor reads; declared for governance
        required_columns: bars columns compute() actually uses (内存治理 WP3.1,
                          与 dependencies 同级治理). The engine projects the
                          evaluation panel to required_columns ∪ 收益价列;
                          undeclared (empty) ⇒ legacy full-column read + a
                          ``factor.columns.fallback`` log (结果不变,不进台账).
        param_schema    : {name: (type, default)}; UI form generated from this
        display_name    : (optional) human-readable label
        description     : (optional) short description shown in UI
        tags            : (optional) default tags applied on registration
    """

    category: str = "uncategorized"
    direction: int = 1
    requires_pit: bool = False
    dependencies: tuple[str, ...] = ("raw.bars_daily",)
    required_columns: tuple[str, ...] = ()
    param_schema: dict[str, tuple[type, Any]] = {}
    display_name: str = ""
    description: str = ""
    tags: tuple[str, ...] = ()
    tracking_pool: str | None = None  # default tracking universe if user opts in

    def __init__(self, **params: Any) -> None:
        # Validate + coerce params via param_schema; ignore unknown keys.
        self.params: dict[str, Any] = {}
        for name, (typ, default) in (self.param_schema or {}).items():
            v = params.get(name, default)
            try:
                if typ is bool:
                    # explicit bool coercion: "false" / 0 -> False
                    if isinstance(v, str):
                        v = v.lower() in {"1", "true", "yes", "on"}
                    else:
                        v = bool(v)
                else:
                    v = typ(v) if v is not None else default
            except Exception as e:  # noqa: BLE001
                raise ValueError(
                    f"Parameter {name!r} must be {typ.__name__}, got {v!r}: {e}"
                ) from e
            self.params[name] = v

    # ── name / id helpers ────────────────────────────────────────────────
    @property
    def base_name(self) -> str:
        """Lowercased class name; used as the factor's 'base' identifier."""
        return self.__class__.__name__.lower()

    @property
    def name(self) -> str:
        """Parametric instance name. MA(window=20) -> 'ma_20'."""
        if not self.param_schema:
            return self.base_name
        suffix = "_".join(str(self.params[k]) for k in self.param_schema)
        return f"{self.base_name}_{suffix}" if suffix else self.base_name

    # ── compute interface ────────────────────────────────────────────────
    def compute(self, ctx: FactorContext) -> pl.DataFrame:  # type: ignore[override]
        """Vectorized one-shot computation over the full window.

        Must return three columns: ['date', 'symbol', 'value'].
        Do NOT do winsorize / zscore / neutralize here — the engine pipeline
        applies them uniformly.

        Implement THIS for non-fundamental factors. Fundamental factors should
        override compute_cross_section instead and leave this NotImplemented.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} did not implement compute(). For PIT "
            f"factors, override compute_cross_section(ctx, as_of) instead."
        )

    def compute_cross_section(
        self, ctx: FactorContext, as_of: dt.date
    ) -> pl.DataFrame:
        """Per-day computation for PIT (fundamental) factors.

        Returns [symbol, value] (engine adds the date column). Called once
        per rebalance day, passing the same FactorContext but with PIT
        fundamentals via ctx.fundamentals(as_of).
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} requires PIT but did not implement "
            f"compute_cross_section(ctx, as_of)."
        )

    # ── metadata override hook ───────────────────────────────────────────
    def metadata(self) -> dict[str, Any]:
        """Optional — declare default category/tags/tracking_pool at the
        instance level, overriding class attributes if you need to."""
        return {
            "category": self.category,
            "direction": self.direction,
            "requires_pit": self.requires_pit,
            "dependencies": list(self.dependencies),
            "tags": list(self.tags),
            "tracking_pool": self.tracking_pool,
            "description": self.description,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Module helpers
# ─────────────────────────────────────────────────────────────────────────────
def factor_to_summary_dict(f: Factor) -> dict[str, Any]:
    """Compact representation used by /factors/{id} responses."""
    return {
        "factor_id": f.name,
        "base_name": f.base_name,
        "category": f.category,
        "direction": f.direction,
        "requires_pit": f.requires_pit,
        "dependencies": list(f.dependencies),
        "param_schema": {
            k: {"type": t.__name__, "default": d}
            for k, (t, d) in (f.param_schema or {}).items()
        },
        "params": dict(f.params),
        "display_name": f.display_name or f.base_name,
        "description": f.description,
        "tags": list(f.tags),
    }


def coerce_params(schema: dict[str, tuple[type, Any]], values: dict | None) -> dict:
    """Coerce a free-form param dict through a schema; used by the API layer
    before instantiating a Factor."""
    values = values or {}
    out: dict[str, Any] = {}
    for name, (typ, default) in schema.items():
        v = values.get(name, default)
        if typ is bool and isinstance(v, str):
            v = v.lower() in {"1", "true", "yes", "on"}
        else:
            try:
                v = typ(v) if v is not None else default
            except (TypeError, ValueError):
                v = default
        out[name] = v
    return out


def _ensure_columns(df: pl.DataFrame, cols: Iterable[str], where: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(
            f"{where}: missing columns {missing!r}; have {df.columns!r}"
        )
