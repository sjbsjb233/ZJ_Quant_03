"""Job actor functions (doc §7.3) -- wrap the heavy quant_data operations.

Each takes a JobContext first and reports progress/log so the Web "进度抽屉"
can render a live bar. They run inside the thread pool, never in the request.
"""
from __future__ import annotations

from quant_data import compaction, migration, table_stats


def field_migration(ctx, table: str, field: str, dtype: str | None = None) -> dict:
    ctx.log(f"任务启动 · 迁移 _extras[{field}] -> 正式列 {field} on {table}")
    ctx.progress(0, 1, "files")

    def cb(done: int, total: int) -> None:
        ctx.progress(done, total, "files")
        if done == 1 or done == total or done % 5 == 0:
            ctx.log(f"文件 {done}/{total} 完成")

    result = migration.promote_field_to_core(
        table, field, dtype=dtype, progress_cb=cb, root=ctx.root
    )
    ctx.log(f"迁移完成 · 重写 {result['files_rewritten']} 个文件 · batch_id={result['batch_id']}")
    table_stats.refresh_all(root=ctx.root)
    return result


def vacuum(ctx, tables: list[str], older_than_days: int = 90) -> dict:
    deleted, total = [], len(tables)
    for i, t in enumerate(tables):
        ctx.log(f"vacuum {t} (older_than_days={older_than_days})")
        res = compaction.vacuum(t, older_than_days=older_than_days, confirm=True, root=ctx.root)
        deleted += res.get("deleted", [])
        ctx.progress(i + 1, total, "tables")
    ctx.log(f"vacuum 完成 · 删除 {len(deleted)} 个文件")
    table_stats.refresh_all(root=ctx.root)
    return {"deleted_files": len(deleted), "tables": tables, "files": deleted}


def compact(ctx, tables: list[str]) -> dict:
    merged, total = 0, len(tables)
    for i, t in enumerate(tables):
        ctx.log(f"compact {t}")
        res = compaction.compact(t, confirm=True, root=ctx.root)
        merged += res.get("compacted_partitions", 0)
        ctx.progress(i + 1, total, "tables")
    ctx.log(f"compact 完成 · 合并 {merged} 个分区")
    table_stats.refresh_all(root=ctx.root)
    return {"compacted_partitions": merged, "tables": tables}


def snapshot(ctx) -> dict:
    ctx.log("创建元数据快照")
    ctx.progress(0, 1, "snapshot")
    path = compaction.snapshot_metadata(root=ctx.root)
    ctx.progress(1, 1, "snapshot")
    ctx.log(f"快照完成 · {path}")
    return {"path": path}
