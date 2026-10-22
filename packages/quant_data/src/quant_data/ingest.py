"""Ingestion orchestrator (doc §7.1).

The full pipeline for one raw write:
  register batch -> apply field_mapping -> split core/_extras -> quality checks
  -> stamp metadata columns -> write append-only Parquet -> auto-register new
  extras fields -> finalize batch (success/failed).

Real source connectors (akshare/tushare/...) are out of scope here; callers
pass already-fetched ``records`` (or a ``fetch_fn``). ``records`` may use the
source's native column names -- field_mapping renames them (doc §2.2).
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field

from . import ids, quality, storage
from .logging import get_logger, timed
from .metadata import batches, field_registry, sources
from .metadata._common import metadata_lock
from .schemas import REFERENCE_TABLES, get_spec

_PASSTHROUGH_META = ("_batch_id", "_source", "_ingested_at")
_log = get_logger("quant_data.ingest")


@dataclass
class IngestResult:
    batch_id: str | None
    target_table: str
    row_count: int
    partitions: list[str] = field(default_factory=list)
    issues: list[quality.QualityIssue] = field(default_factory=list)
    skipped: bool = False


# --- helpers ----------------------------------------------------------------
def apply_field_mapping(records: list[dict], source: str, target_table: str, root=None) -> list[dict]:
    """Rename source-native columns to standard names per metadata.sources."""
    mapping = sources.get_field_mapping(source, target_table, root=root)
    if not mapping:
        return [dict(r) for r in records]
    out = []
    for r in records:
        out.append({mapping.get(k, k): v for k, v in r.items()})
    return out


def _split_core_extras(records: list[dict], core: set[str]) -> list[dict]:
    """Keep core columns; everything else flows into the _extras KV bag."""
    processed = []
    for rec in records:
        row: dict = {}
        extras = dict(rec.get("_extras") or {})
        for k, v in rec.items():
            if k == "_extras":
                continue
            if k in core or k in _PASSTHROUGH_META:
                row[k] = v
            else:
                extras[k] = None if v is None else str(v)
        row["_extras"] = extras or None
        processed.append(row)
    return processed


def _looks_numeric(values) -> bool:
    seen = False
    for v in values:
        if v is None or v == "":
            continue
        seen = True
        try:
            float(v)
        except (TypeError, ValueError):
            return False
    return seen


def discover_new_extras(processed: list[dict], target_table: str, batch_id: str, root=None) -> dict[str, str]:
    """Register any never-before-seen _extras keys as 'extras' (doc §7.1 step 7)."""
    samples: dict[str, list] = {}
    for row in processed:
        for k, v in (row.get("_extras") or {}).items():
            samples.setdefault(k, []).append(v)
    newly: dict[str, str] = {}
    for fname, vals in samples.items():
        if field_registry.get_field(target_table, fname, root=root) is not None:
            continue
        dtype = "DOUBLE" if _looks_numeric(vals) else "VARCHAR"
        field_registry.register_field(
            target_table, fname, data_type=dtype, status="extras",
            first_seen_batch_id=batch_id, root=root,
        )
        newly[fname] = dtype
    return newly


# --- generic raw ingest -----------------------------------------------------
def ingest(
    target_table: str,
    records,
    source: str = "akshare",
    parameters: dict | None = None,
    apply_mapping: bool = True,
    run_checks: bool = True,
    dedup: bool = False,
    notes: str | None = None,
    batch_id: str | None = None,
    root=None,
) -> IngestResult:
    """Run the full append-only ingest pipeline for a raw table."""
    spec = get_spec(target_table)
    if spec.kind != "raw":
        raise ValueError(f"{target_table} is not a raw table; use upsert_reference()")

    records = list(records)

    if dedup and parameters is not None:
        # 只对「已成功落库」的批次去重。若把 'running' 也算进来, 一个崩溃后卡在
        # running 的孤儿批 (register 成功但 finalize 前死掉, 正是 stale_running 要
        # 回收的对象) 会让同参数的重新摄取被静默跳过, 报告 success 却没有任何数据。
        existing = batches.find_by_parameters(
            target_table, parameters, statuses=("success",), root=root
        )
        if existing:
            return IngestResult(existing[0]["batch_id"], target_table, 0, skipped=True)

    bid = batches.register_batch(source, target_table, parameters=parameters, notes=notes,
                                 batch_id=batch_id, root=root)
    _log.info("ingest.batch.register", batch_id=bid, table=target_table, source=source,
              row_count=len(records))
    try:
        if apply_mapping:
            records = apply_field_mapping(records, source, target_table, root=root)
            _log.debug("ingest.field_mapping.applied", source=source, table=target_table)

        processed = _split_core_extras(records, set(spec.business_names()))
        _log.debug("ingest.split_extras.done", table=target_table,
                   core_cols=len(spec.business_names()))
        now = ids.utc_now()
        for row in processed:
            row["_batch_id"] = bid
            row["_source"] = source
            row["_ingested_at"] = now

        tbl = storage.normalize_to_arrow(spec, processed)

        issues: list[quality.QualityIssue] = []
        if run_checks:
            issues = quality.run_quality_checks(spec, tbl)
            for iss in issues:
                warnings.warn(str(iss), stacklevel=2)
            by_level: dict[str, int] = {}
            for iss in issues:
                by_level[iss.level] = by_level.get(iss.level, 0) + 1
            _log.info("ingest.quality.checked", batch_id=bid, table=target_table,
                      issue_count=len(issues), issue_count_by_level=by_level or None)

        _log.info("ingest.write.start", batch_id=bid, target_table=target_table,
                  row_count=tbl.num_rows)
        with timed(_log, "ingest.write", batch_id=bid, table=target_table,
                   row_count=tbl.num_rows) as _m:
            partitions = storage.write_append(spec, tbl, bid, root=root)
            _m["partitions"] = partitions
        newly = discover_new_extras(processed, target_table, bid, root=root)
        if newly:
            _log.info("ingest.extras.auto_register", table=target_table,
                      new_fields=list(newly))

        if issues:  # persist findings so the Web platform can query them (doc §7.1)
            try:
                from . import quality_log
                quality_log.record_issues(bid, target_table, issues, root=root)
            except Exception as exc:  # noqa: BLE001 - non-fatal, but never silent
                warnings.warn(
                    f"failed to persist quality issues for batch {bid}: {exc}", stacklevel=2
                )

        note = notes
        if issues:
            note = (note + " | " if note else "") + f"quality_issues={len(issues)}"
        batches.finalize_batch(bid, "success", row_count=tbl.num_rows,
                               partition_keys=partitions, root=root)
        if note != notes:
            # 批次已成功落定; notes 回写仅为附注, 失败不应把批次拖进失败处理分支
            # (finalize_batch 仅允许 running -> 终态, 二次 finalize 会抛误导性 ValueError)
            try:
                batches.update_batch(bid, notes=note, root=root)
            except Exception as exc:  # noqa: BLE001 - non-fatal, but never silent
                warnings.warn(
                    f"failed to update notes for batch {bid}: {exc}", stacklevel=2
                )
        _log.info("ingest.batch.finalize", batch_id=bid, status="success",
                  row_count=tbl.num_rows, partitions=partitions)
        return IngestResult(bid, target_table, tbl.num_rows, partitions, issues)
    except Exception as exc:  # noqa: BLE001 - record failure then re-raise
        _log.exception("ingest.write.failed", batch_id=bid, table=target_table)
        batches.finalize_batch(bid, "failed", error_message=str(exc), root=root)
        raise


# --- reference upsert (mutable dictionaries) --------------------------------
def upsert_reference(
    target_table: str,
    records,
    source: str = "manual",
    apply_mapping: bool = False,
    root=None,
) -> int:
    """Upsert rows into a reference dictionary by primary key (doc §5)."""
    spec = REFERENCE_TABLES.get(target_table) or get_spec(target_table)
    if spec.kind != "reference":
        raise ValueError(f"{target_table} is not a reference table")
    records = list(records)
    if apply_mapping:
        records = apply_field_mapping(records, source, target_table, root=root)

    core = set(spec.business_names())
    now = ids.utc_now()
    # serialise the read-modify-write of the single reference file
    with metadata_lock(root):
        rows = storage.read_rows(spec, root)
        index = {tuple(r[k] for k in spec.primary_key): i for i, r in enumerate(rows)}
        for rec in records:
            row = {n: None for n in spec.all_names()}
            extras = dict(rec.get("_extras") or {})
            for k, v in rec.items():
                if k == "_extras":
                    continue
                if k in core:
                    row[k] = v
                elif spec.has_extras():
                    extras[k] = None if v is None else str(v)
            if spec.has_extras():
                row["_extras"] = extras or None
            row["_updated_at"] = now
            row["_source"] = source
            key = tuple(row[k] for k in spec.primary_key)
            if key in index:
                rows[index[key]] = row
            else:
                index[key] = len(rows)
                rows.append(row)
        storage.write_rows(spec, rows, root)
    return len(records)


# --- per-table convenience wrappers (doc §7.1 signatures) -------------------
def _fetch_or_records(records, fetch_fn, **params):
    if records is None:
        if fetch_fn is None:
            raise ValueError("provide records= or fetch_fn=")
        records = fetch_fn(**params)
    return records


def ingest_bars_daily(records=None, *, fetch_fn=None, symbols=None, start=None, end=None,
                      source="akshare", root=None, **kw) -> IngestResult:
    params = {"symbols": symbols, "start": str(start) if start else None,
              "end": str(end) if end else None}
    recs = _fetch_or_records(records, fetch_fn, symbols=symbols, start=start, end=end)
    return ingest("raw.bars_daily", recs, source=source, parameters=params, root=root, **kw)


def ingest_bars_minute(records=None, *, fetch_fn=None, symbols=None, start=None, end=None,
                       source="akshare", root=None, **kw) -> IngestResult:
    params = {"symbols": symbols, "start": str(start) if start else None,
              "end": str(end) if end else None}
    recs = _fetch_or_records(records, fetch_fn, symbols=symbols, start=start, end=end)
    return ingest("raw.bars_minute", recs, source=source, parameters=params, root=root, **kw)


def ingest_fundamentals(records=None, *, fetch_fn=None, symbols=None, source="akshare",
                        root=None, **kw) -> IngestResult:
    params = {"symbols": symbols}
    recs = _fetch_or_records(records, fetch_fn, symbols=symbols)
    return ingest("raw.fundamentals", recs, source=source, parameters=params, root=root, **kw)


def ingest_universe(records=None, *, fetch_fn=None, start=None, end=None, source="akshare",
                    root=None, **kw) -> IngestResult:
    params = {"start": str(start) if start else None, "end": str(end) if end else None}
    recs = _fetch_or_records(records, fetch_fn, start=start, end=end)
    return ingest("raw.universe", recs, source=source, parameters=params, root=root, **kw)


def ingest_calendar(records=None, *, fetch_fn=None, market=None, source="akshare",
                    root=None, **kw) -> IngestResult:
    params = {"market": market}
    recs = _fetch_or_records(records, fetch_fn, market=market)
    return ingest("raw.calendar", recs, source=source, parameters=params, root=root, **kw)


def ingest_corporate_actions(records=None, *, fetch_fn=None, symbols=None, source="akshare",
                             root=None, **kw) -> IngestResult:
    params = {"symbols": symbols}
    recs = _fetch_or_records(records, fetch_fn, symbols=symbols)
    return ingest("raw.corporate_actions", recs, source=source, parameters=params, root=root, **kw)


def ingest_symbols(records, source="manual", root=None) -> int:
    return upsert_reference("reference.symbols", records, source=source, root=root)


def ingest_sectors(records, source="manual", root=None) -> int:
    return upsert_reference("reference.sectors", records, source=source, root=root)
