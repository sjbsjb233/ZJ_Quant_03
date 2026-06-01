"""Schema migration: promote an _extras field to a real core column (doc §7.3 Phase 3).

Each partition file is rewritten in place (atomically) to (1) materialise the
field as a typed column and (2) drop the key from _extras. A migration batch is
recorded for audit, and field_registry flips to 'core'. The reader's
extras_as_columns compat layer keeps old code working without changes (doc §7.3).
"""
from __future__ import annotations

import os
import time

import pyarrow.parquet as pq

from . import config, ids, storage
from .logging import get_logger
from .metadata import batches, field_registry
from .schemas import ARROW_TYPE_BY_NAME, get_spec

_log = get_logger("quant_data.migration")


def dry_run_promote(table: str, field: str, dtype: str | None = None,
                    sample: int = 10, root=None) -> dict:
    """Scan (never write) and report the impact of promoting a field (doc §7.7).

    Feeds the Web "pre-scan" confirmation modal: file/row counts, an IO/duration
    estimate, a sample of values that would not cast cleanly, and the before /
    after Parquet schemas.
    """
    spec = get_spec(table)
    if not spec.has_extras():
        raise ValueError(f"{table} has no _extras column to promote from")
    reg = field_registry.get_field(table, field, root=root)
    dtype = (dtype or (reg["data_type"] if reg else "DOUBLE")).upper()
    if dtype not in ARROW_TYPE_BY_NAME:
        raise ValueError(f"unsupported dtype {dtype!r}")

    files = storage.list_data_files(spec, root=root)
    f_lit = lambda s: "'" + str(s).replace("'", "''") + "'"  # noqa: E731
    con = config.connect_duckdb()
    total_rows, total_bytes, cast_failures = 0, 0, []
    for f in files:
        total_bytes += os.path.getsize(f)
        total_rows += con.execute(
            f"SELECT count(*) FROM read_parquet({f_lit(f)}, hive_partitioning=false)"
        ).fetchone()[0]
        if len(cast_failures) < sample:
            bad = con.execute(
                f"SELECT DISTINCT _batch_id, _extras[{f_lit(field)}] AS raw "
                f"FROM read_parquet({f_lit(f)}, hive_partitioning=false) "
                f"WHERE _extras[{f_lit(field)}] IS NOT NULL "
                f"AND TRY_CAST(_extras[{f_lit(field)}] AS {dtype}) IS NULL "
                f"LIMIT {int(sample)}"
            ).fetchall()
            for batch_id, raw in bad:
                cast_failures.append({"batch_id": batch_id, "value": raw, "would_be": None})
    con.close()

    current_names = list(pq.read_schema(files[0]).names) if files else spec.all_names()
    after_names = current_names if field in current_names else [*current_names, field]
    return {
        "affected_files": len(files),
        "total_rows": total_rows,
        "estimated_duration_sec": max(1, round(total_rows / 30000) + len(files)),
        "estimated_disk_io_mb": round(total_bytes * 2 / (1024 * 1024), 1),
        "cast_failure_sample": cast_failures[:sample],
        "current_schema": current_names,
        "after_schema": after_names,
        "field": field,
        "dtype": dtype,
    }


def promote_field_to_core(table: str, field: str, dtype: str | None = None,
                          dry_run: bool = False, progress_cb=None, root=None) -> dict:
    """Materialise ``_extras[field]`` into a typed core column across all files.

    With ``dry_run=True`` this only previews the impact (doc §7.7) and writes
    nothing. ``progress_cb(done, total)`` (if given) is called after each file so
    the async job (doc §7.3) can report file-by-file progress.
    """
    spec = get_spec(table)
    if not spec.has_extras():
        raise ValueError(f"{table} has no _extras column to promote from")

    reg = field_registry.get_field(table, field, root=root)
    dtype = (dtype or (reg["data_type"] if reg else "DOUBLE")).upper()
    if dtype not in ARROW_TYPE_BY_NAME:
        raise ValueError(f"unsupported dtype {dtype!r}")

    if dry_run:
        return dry_run_promote(table, field, dtype=dtype, root=root)

    files = storage.list_data_files(spec, root=root)
    params = {"operation": "promote_to_core", "field": field, "dtype": dtype}
    bid = batches.register_batch("internal:schema-migration", table, parameters=params, root=root)
    _log.info("migration.promote.start", table=table, field=field, dtype=dtype,
              batch_id=bid, total_files=len(files))
    start = time.perf_counter()

    try:
        con = config.connect_duckdb()
        total = 0
        f_lit = lambda s: "'" + str(s).replace("'", "''") + "'"  # noqa: E731
        ident = lambda s: '"' + str(s).replace('"', '""') + '"'  # noqa: E731
        cast = f"TRY_CAST(_extras[{f_lit(field)}] AS {dtype})"
        for i, f in enumerate(files):
            tmp = f + ".tmp"
            # Idempotent: if the file already has a `field` column (re-run, or a
            # same-named core column), exclude it from * and preserve its value,
            # falling back to _extras. Otherwise just materialise from _extras.
            # The alias is quoted so arbitrary _extras key names are safe.
            # hive_partitioning=false: read only the file's own columns, never
            # pull year/month/quarter out of the path into the rewritten file.
            already = field in pq.read_schema(f).names
            if already:
                exclude = f"_extras, {ident(field)}"
                field_expr = f"COALESCE({ident(field)}, {cast}) AS {ident(field)}"
            else:
                exclude = "_extras"
                field_expr = f"{cast} AS {ident(field)}"
            select = (
                f"SELECT * EXCLUDE ({exclude}), {field_expr}, "
                f"map_from_entries(list_filter(map_entries(_extras), "
                f"e -> e.key <> {f_lit(field)})) AS _extras "
                f"FROM read_parquet({f_lit(f)}, hive_partitioning=false)"
            )
            con.execute(
                f"COPY ({select}) TO {f_lit(tmp)} (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
            total += con.execute(f"SELECT count(*) FROM read_parquet({f_lit(tmp)})").fetchone()[0]
            os.replace(tmp, f)
            if progress_cb is not None:
                progress_cb(i + 1, len(files))
            _log.debug("migration.promote.file_rewritten", file_index=i + 1,
                       total_files=len(files))
        con.close()

        if reg is None:
            field_registry.register_field(table, field, dtype, status="core", root=root)
        field_registry.update_field_status(table, field, "core", root=root)

        batches.finalize_batch(
            bid, "success", row_count=total,
            partition_keys=None, root=root,
        )
        batches.update_batch(bid, notes=f"promoted {field} -> core ({len(files)} files)", root=root)
        _log.info("migration.promote.done", table=table, field=field,
                  files_rewritten=len(files), batch_id=bid, row_count=total,
                  duration_ms=int((time.perf_counter() - start) * 1000))
        return {"field": field, "dtype": dtype, "files_rewritten": len(files), "batch_id": bid}
    except Exception as exc:  # noqa: BLE001
        _log.exception("migration.promote.failed", table=table, field=field, batch_id=bid)
        batches.finalize_batch(bid, "failed", error_message=str(exc), root=root)
        raise
