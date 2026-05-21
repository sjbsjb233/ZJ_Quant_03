"""Data-quality checks (doc §3.1/§3.2 constraints, §7.1 step 4).

All checks are advisory (non-blocking): ingestion runs them and records the
findings, but a failing check never aborts a write. Returned issues let callers
decide whether to invalidate a batch afterwards.
"""
from __future__ import annotations

from dataclasses import dataclass

import pyarrow as pa
import pyarrow.compute as pc

from .schemas import CORPORATE_ACTION_TYPES, TableSpec
from .symbology import is_normalized


@dataclass
class QualityIssue:
    table: str
    check: str
    message: str
    count: int
    level: str = "warn"

    def __str__(self) -> str:
        return f"[{self.level}] {self.table}: {self.check} ({self.count}) -- {self.message}"


def _true_count(mask) -> int:
    if mask is None or len(mask) == 0:
        return 0
    return int(pc.sum(pc.cast(mask, pa.int64())).as_py() or 0)


def _col(tbl: pa.Table, name: str):
    return tbl.column(name) if name in tbl.column_names else None


def run_quality_checks(spec: TableSpec, tbl: pa.Table) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    n = tbl.num_rows
    if n == 0:
        return issues

    # 1. primary-key columns must be non-null
    for k in spec.primary_key:
        col = _col(tbl, k)
        if col is not None:
            nulls = col.null_count
            if nulls:
                issues.append(QualityIssue(spec.name, "pk_null", f"null in PK column {k!r}", nulls))

    # 2. duplicate primary keys *within this batch*
    pk_cols = [tbl.column(k).to_pylist() for k in spec.primary_key if k in tbl.column_names]
    if pk_cols:
        keys = list(zip(*pk_cols))
        dups = len(keys) - len(set(keys))
        if dups:
            issues.append(QualityIssue(spec.name, "pk_duplicate", "duplicate keys in batch", dups))

    issues.extend(_DISPATCH.get(spec.name, lambda s, t: [])(spec, tbl))
    return issues


def _check_bars(spec: TableSpec, tbl: pa.Table) -> list[QualityIssue]:
    issues = []
    o, h, lo, cl = (_col(tbl, c) for c in ("open", "high", "low", "close"))
    if h is not None and lo is not None:
        issues_n = _true_count(pc.less(h, lo))
        if issues_n:
            issues.append(QualityIssue(spec.name, "high_lt_low", "high < low", issues_n))
    for nm, c in (("open", o), ("high", h), ("low", lo), ("close", cl)):
        if c is not None:
            neg = _true_count(pc.less_equal(c, 0))
            if neg:
                issues.append(QualityIssue(spec.name, "non_positive_price", f"{nm} <= 0", neg))
    vol = _col(tbl, "volume")
    if vol is not None:
        neg = _true_count(pc.less(vol, 0))
        if neg:
            issues.append(QualityIssue(spec.name, "negative_volume", "volume < 0", neg))
        zero = _true_count(pc.equal(vol, 0))
        if zero:
            # cross-check against universe.is_suspended happens at read time; flag here
            issues.append(
                QualityIssue(spec.name, "zero_volume", "volume == 0 (verify suspension)", zero)
            )
    sym = _col(tbl, "symbol")
    if sym is not None:
        bad = sum(1 for v in sym.to_pylist() if v is not None and not is_normalized(v))
        if bad:
            issues.append(QualityIssue(spec.name, "unnormalized_symbol",
                                       "symbol missing .EXCHANGE suffix", bad))
    return issues


def _check_fundamentals(spec: TableSpec, tbl: pa.Table) -> list[QualityIssue]:
    issues = []
    rp, pub = _col(tbl, "report_period"), _col(tbl, "publish_date")
    if rp is not None and pub is not None:
        # publish must not precede the period end (PIT sanity)
        bad = _true_count(pc.less(pub, rp))
        if bad:
            issues.append(
                QualityIssue(spec.name, "publish_before_period", "publish_date < report_period", bad)
            )
    return issues


def _check_corporate_actions(spec: TableSpec, tbl: pa.Table) -> list[QualityIssue]:
    issues = []
    at = _col(tbl, "action_type")
    if at is not None:
        vals = at.to_pylist()
        bad = sum(1 for v in vals if v is not None and v not in CORPORATE_ACTION_TYPES)
        if bad:
            issues.append(
                QualityIssue(spec.name, "unknown_action_type", "action_type not in enum", bad)
            )
    ann, act = _col(tbl, "announce_date"), _col(tbl, "action_date")
    if ann is not None and act is not None:
        bad = _true_count(pc.greater(ann, act))
        if bad:
            issues.append(QualityIssue(spec.name, "announce_after_action",
                                       "announce_date > action_date", bad))
    return issues


_DISPATCH = {
    "raw.bars_daily": _check_bars,
    "raw.bars_minute": _check_bars,
    "raw.fundamentals": _check_fundamentals,
    "raw.corporate_actions": _check_corporate_actions,
}
