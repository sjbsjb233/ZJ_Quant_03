"""Schema migration: promote an _extras field to a real core column (doc §7.3 Phase 3).

Each partition file is rewritten in place (atomically) to (1) materialise the
field as a typed column and (2) drop the key from _extras. A migration batch is
recorded for audit, and field_registry flips to 'core'. The reader's
extras_as_columns compat layer keeps old code working without changes (doc §7.3).
"""
from __future__ import annotations

import os

import duckdb
import pyarrow.parquet as pq

from . import ids, storage
from .metadata import batches, field_registry
from .schemas import ARROW_TYPE_BY_NAME, get_spec


def promote_field_to_core(table: str, field: str, dtype: str | None = None, root=None) -> dict:
    """Materialise ``_extras[field]`` into a typed core column across all files."""
    spec = get_spec(table)
    if not spec.has_extras():
        raise ValueError(f"{table} has no _extras column to promote from")

    reg = field_registry.get_field(table, field, root=root)
    dtype = (dtype or (reg["data_type"] if reg else "DOUBLE")).upper()
    if dtype not in ARROW_TYPE_BY_NAME:
        raise ValueError(f"unsupported dtype {dtype!r}")

    files = storage.list_data_files(spec, root=root)
    params = {"operation": "promote_to_core", "field": field, "dtype": dtype}
    bid = batches.register_batch("internal:schema-migration", table, parameters=params, root=root)

    try:
        con = duckdb.connect()
        total = 0
        f_lit = lambda s: "'" + str(s).replace("'", "''") + "'"  # noqa: E731
        ident = lambda s: '"' + str(s).replace('"', '""') + '"'  # noqa: E731
        cast = f"TRY_CAST(_extras[{f_lit(field)}] AS {dtype})"
        for f in files:
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
        con.close()

        if reg is None:
            field_registry.register_field(table, field, dtype, status="core", root=root)
        field_registry.update_field_status(table, field, "core", root=root)

        batches.finalize_batch(
            bid, "success", row_count=total,
            partition_keys=None, root=root,
        )
        batches.update_batch(bid, notes=f"promoted {field} -> core ({len(files)} files)", root=root)
        return {"field": field, "dtype": dtype, "files_rewritten": len(files), "batch_id": bid}
    except Exception as exc:  # noqa: BLE001
        batches.finalize_batch(bid, "failed", error_message=str(exc), root=root)
        raise
