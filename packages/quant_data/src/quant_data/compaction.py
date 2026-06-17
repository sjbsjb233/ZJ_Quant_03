"""Background-style maintenance -- but MANUAL ONLY (doc §1.3 / §9.4).

Per the open question §9.4 the database never deletes files on its own: dead
files of invalidated batches carry audit value. Every destructive operation
here is a no-op preview unless the caller passes ``confirm=True``, mirroring the
"admin must run it by hand" rule.
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
from collections import defaultdict
from pathlib import Path

import pyarrow.compute as pc
import pyarrow.parquet as pq

from . import config, ids, storage
from .logging import get_logger
from .metadata import batches
from .schemas import METADATA_TABLES, get_spec

_log = get_logger("quant_data.compaction")


def _sum_bytes(files) -> int:
    total = 0
    for f in files:
        try:
            total += os.path.getsize(f)
        except OSError:
            pass
    return total


def _batch_ids_in(file: str) -> set[str]:
    col = pq.read_table(file, columns=["_batch_id"]).column("_batch_id")
    return set(pc.unique(col).to_pylist())


def dead_files(table: str, older_than_days: int | None = config.AUDIT_RETENTION_DAYS, root=None) -> list[dict]:
    """Files whose rows belong to invalidated/failed batches (audit candidates).

    With ``older_than_days`` set, only batches invalidated before the cutoff are
    listed -- younger ones stay within the audit window.
    """
    spec = get_spec(table)
    bad = {
        b["batch_id"]: b
        for b in batches.list_batches(target_table=table, root=root)
        if b["status"] in ("invalidated", "failed")
    }
    cutoff = ids.utc_now() - dt.timedelta(days=older_than_days) if older_than_days else None
    out = []
    for f in storage.list_data_files(spec, root=root):
        for bid in _batch_ids_in(f):
            b = bad.get(bid)
            if b is None:
                continue
            inv_at = b.get("invalidated_at") or b.get("finished_at")
            # 失败闭合: 时间戳缺失时无法确认已过审计窗口, 一律保留(不当作删除候选).
            if cutoff is not None and (inv_at is None or inv_at > cutoff):
                continue  # still inside audit window (or window cannot be confirmed)
            out.append({"file": f, "batch_id": bid, "status": b["status"], "invalidated_at": inv_at})
    return out


def vacuum(table: str, older_than_days: int = config.AUDIT_RETENTION_DAYS, confirm: bool = False, root=None) -> dict:
    """Preview (default) or, with confirm=True, delete dead files past the window.

    The batch ledger is never touched -- provenance survives even after the
    physical file is gone.
    """
    plan = dead_files(table, older_than_days=older_than_days, root=root)
    files = sorted({p["file"] for p in plan})
    total_bytes = _sum_bytes(files)
    if not confirm:
        _log.info("compaction.vacuum.preview", table=table, candidate_files=len(files),
                  total_bytes=total_bytes)
        return {"would_delete": files, "count": len(files),
                "note": "dry run -- pass confirm=True to delete (doc §9.4: manual only)"}
    for f in files:
        if os.path.exists(f):
            os.remove(f)
    _log.info("compaction.vacuum.executed", table=table, deleted_count=len(files),
              bytes_freed=total_bytes)
    return {"deleted": files, "count": len(files)}


def compact(table: str, confirm: bool = False, root=None) -> dict:
    """Merge each partition's many small files into one. Preview unless confirm=True.

    审计窗口意识: 只丢弃 ``dead_files()`` 判定可删的批次行(已失效/失败且已过
    AUDIT_RETENTION_DAYS 窗口), 仍在窗口内的失效/失败批次行被保留 -- 否则一次
    例行小文件合并会把审计价值仍在保护期内的溯源数据静默销毁(doc §9.4)。"""
    spec = get_spec(table)
    by_dir: dict[str, list[str]] = defaultdict(list)
    for f in storage.list_data_files(spec, root=root):
        by_dir[str(Path(f).parent)].append(f)
    targets = {d: fs for d, fs in by_dir.items() if len(fs) > 1}

    if not confirm:
        _log.info("compaction.compact.preview", table=table, partitions=len(targets),
                  candidate_files=sum(len(fs) for fs in targets.values()))
        return {
            "would_compact": {d: len(fs) for d, fs in targets.items()},
            "partitions": len(targets),
            "note": "dry run -- pass confirm=True to merge (doc §9.4: manual only)",
        }

    # 仅排除已过审计窗口的失效/失败批次(与 vacuum 同一判定), 其余行一律保留。
    drop_ids = {p["batch_id"] for p in dead_files(table, root=root)}

    con = config.connect_duckdb()
    try:
        lit = lambda s: "'" + str(s).replace("'", "''") + "'"  # noqa: E731
        merged = 0
        for d, fs in targets.items():
            out = Path(d) / f"compacted-{ids.new_batch_id()}.parquet"
            tmp = str(out) + ".tmp"
            where = ""
            if drop_ids:
                ids_sql = ",".join(lit(b) for b in drop_ids)
                where = f"WHERE _batch_id NOT IN ({ids_sql}) "
            con.execute(
                f"COPY (SELECT * FROM read_parquet([{','.join(lit(f) for f in fs)}], "
                f"union_by_name=true, hive_partitioning=false) "
                f"{where}) TO {lit(tmp)} (FORMAT PARQUET, COMPRESSION ZSTD)"
            )
            os.replace(tmp, out)
            for f in fs:
                os.remove(f)
            merged += 1
    finally:
        con.close()
    _log.info("compaction.compact.executed", table=table, compacted_partitions=merged)
    return {"compacted_partitions": merged}


def snapshot_metadata(root=None) -> str:
    """Back up the metadata ledgers to a timestamped folder (doc §8 stage 3).

    The metadata tables (batches/sources/field_registry) are the spine of the
    governance model, so they get their own point-in-time copy.
    """
    base = config.data_root(root)
    stamp = ids.utc_now().strftime("%Y%m%dT%H%M%S")
    dest = base / "metadata" / "_snapshots" / stamp
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for spec in METADATA_TABLES.values():
        src = storage.single_file_path(spec, root)
        if src.exists():
            shutil.copy2(src, dest / src.name)
            copied.append(dest / src.name)
    _log.info("compaction.snapshot.created", path=str(dest),
              size_bytes=_sum_bytes(copied), files=len(copied))
    return str(dest)
