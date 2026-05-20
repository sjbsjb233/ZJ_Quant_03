"""Low-level on-disk layer.

Two storage models, picked by ``TableSpec.mutable``:

* append-only, hive-partitioned Parquet for raw tables (doc §1.3/§1.4). Every
  write produces brand-new files named ``{batch_id}-{i}.parquet``; nothing is
  ever mutated, so the same logical key can appear in many files. The reader's
  "current view" picks the winning row (doc §6.1).
* mutable single-file Parquet for metadata + reference tables. These are tiny
  and updated in place (status flips, priority changes, upserts), so we do an
  atomic read-modify-rewrite of one canonical file.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from . import config
from .schemas import TableSpec

_COMPRESSION = "zstd"


# --- path helpers -----------------------------------------------------------
def table_dir(spec: TableSpec, root=None) -> Path:
    return config.data_root(root) / spec.rel_path


def single_file_path(spec: TableSpec, root=None) -> Path:
    d = table_dir(spec, root)
    return d / f"{Path(spec.rel_path).name}.parquet"


def list_data_files(spec: TableSpec, root=None) -> list[str]:
    """Every live parquet file for a table (skips *.tmp scratch files)."""
    d = table_dir(spec, root)
    if not d.exists():
        return []
    return sorted(str(p) for p in d.rglob("*.parquet") if not p.name.endswith(".tmp"))


# --- input normalisation ----------------------------------------------------
def _to_records(data: Any) -> list[dict]:
    """Coerce arrow tables / polars / pandas / list-of-dicts into records."""
    if isinstance(data, pa.Table):
        return data.to_pylist()
    if isinstance(data, list):
        return data
    mod = type(data).__module__.split(".")[0]
    if mod == "polars":  # polars.DataFrame
        return data.to_dicts()
    if mod == "pandas":  # pandas.DataFrame
        return data.to_dict("records")
    raise TypeError(
        f"Unsupported data type {type(data)!r}; pass list[dict], pyarrow.Table, "
        "polars.DataFrame or pandas.DataFrame."
    )


def _map_value(e: Any) -> Any:
    """Normalise one _extras cell into list[(str, str|None)] for arrow."""
    if e is None:
        return None
    if isinstance(e, dict):
        return [(str(k), None if v is None else str(v)) for k, v in e.items()]
    # already list of (k, v) tuples (e.g. read back from parquet)
    return [(str(k), None if v is None else str(v)) for k, v in e]


def _conform_arrow(tbl: pa.Table, schema: pa.Schema) -> pa.Table:
    """Project an arrow table onto ``schema``: select, cast, null-fill."""
    cols = {}
    for fld in schema:
        if fld.name in tbl.column_names:
            arr = tbl.column(fld.name)
            if not arr.type.equals(fld.type):
                arr = arr.cast(fld.type)
            cols[fld.name] = arr
        else:
            cols[fld.name] = pa.nulls(tbl.num_rows, fld.type)
    return pa.table(cols, schema=schema)


def normalize_to_arrow(spec: TableSpec, data: Any) -> pa.Table:
    """Build an arrow table matching ``spec.arrow_schema()`` from any input.

    Missing columns become nulls; ``_extras`` accepts a per-row dict.
    """
    schema = spec.arrow_schema()
    if isinstance(data, pa.Table):
        return _conform_arrow(data, schema)

    rows = _to_records(data)
    arrays = []
    for fld in schema:
        name, typ = fld.name, fld.type
        if pa.types.is_map(typ):
            arrays.append(pa.array([_map_value(r.get(name)) for r in rows], type=typ))
        else:
            arrays.append(pa.array([r.get(name) for r in rows], type=typ))
    return pa.table(arrays, schema=schema)


def empty_arrow(spec: TableSpec) -> pa.Table:
    return spec.arrow_schema().empty_table()


# --- partition derivation ---------------------------------------------------
def _derive_partition(arr, transform: str):
    if transform == "year":
        out = pc.year(arr)
    elif transform == "month":
        out = pc.month(arr)
    elif transform == "quarter":
        out = pc.quarter(arr)
    else:
        raise ValueError(f"unknown partition transform {transform!r}")
    return pc.cast(out, pa.int32())


def add_partition_columns(spec: TableSpec, tbl: pa.Table) -> pa.Table:
    out = tbl
    for p in spec.partition:
        if p.name in out.column_names:
            continue
        out = out.append_column(p.name, _derive_partition(out.column(p.source), p.transform))
    return out


def partition_key_strings(spec: TableSpec, tbl: pa.Table) -> list[str]:
    """Distinct hive partition paths touched, e.g. 'year=2025/month=11'."""
    names = spec.partition_names()
    if not names:
        return []
    have = add_partition_columns(spec, tbl)
    series = [have.column(n).to_pylist() for n in names]
    combos = sorted(set(zip(*series)))
    return ["/".join(f"{n}={v}" for n, v in zip(names, combo)) for combo in combos]


# --- append-only write (raw tables) -----------------------------------------
def write_append(spec: TableSpec, data: Any, batch_id: str, root=None) -> list[str]:
    """Write a new immutable file set for ``batch_id``; return touched partitions."""
    if spec.mutable:
        raise ValueError(f"{spec.name} is mutable; use write_rows()")
    tbl = normalize_to_arrow(spec, data)
    tbl = add_partition_columns(spec, tbl)
    root_dir = table_dir(spec, root)
    root_dir.mkdir(parents=True, exist_ok=True)

    if spec.partition:
        fmt = ds.ParquetFileFormat()
        ds.write_dataset(
            tbl,
            root_dir,
            format=fmt,
            file_options=fmt.make_write_options(compression=_COMPRESSION),
            partitioning=spec.partition_names(),
            partitioning_flavor="hive",
            basename_template=f"{batch_id}-{{i}}.parquet",
            existing_data_behavior="overwrite_or_ignore",
        )
        return partition_key_strings(spec, tbl)

    # unpartitioned (e.g. calendar): one file per batch in the table dir
    pq.write_table(
        tbl.select(spec.all_names()),
        root_dir / f"{batch_id}.parquet",
        compression=_COMPRESSION,
    )
    return []


# --- mutable single-file read/modify/write (metadata + reference) -----------
def read_rows(spec: TableSpec, root=None) -> list[dict]:
    """All rows of a mutable table as dicts ([] if not yet created)."""
    p = single_file_path(spec, root)
    if not p.exists():
        return []
    rows = pq.read_table(p).to_pylist()
    if spec.has_extras():  # turn _extras list-of-tuples back into a dict
        for r in rows:
            if r.get("_extras") is not None:
                r["_extras"] = dict(r["_extras"])
    return rows


def write_rows(spec: TableSpec, rows: list[dict], root=None) -> None:
    """Atomically (re)write a mutable table's single canonical file."""
    tbl = normalize_to_arrow(spec, rows)
    p = single_file_path(spec, root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".parquet.tmp")
    pq.write_table(tbl, tmp, compression=_COMPRESSION)
    os.replace(tmp, p)


# --- layout bootstrap -------------------------------------------------------
def ensure_layout(root=None) -> Path:
    """Create the data/ tree incl. derived/ placeholders (doc §2/§4)."""
    base = config.data_root(root)
    for ns in config.NAMESPACES:
        (base / ns).mkdir(parents=True, exist_ok=True)
    for d in config.DERIVED_PLACEHOLDER_DIRS:
        sub = base / d
        sub.mkdir(parents=True, exist_ok=True)
        keep = sub / ".gitkeep"
        if not keep.exists():
            keep.write_text(
                "# Reserved namespace for the factor/strategy layer (doc §4). "
                "No column schema this period.\n"
            )
    return base
