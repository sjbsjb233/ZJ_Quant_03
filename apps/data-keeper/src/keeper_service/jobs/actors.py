"""Job actor functions — wrap the heavy quant_data operations + autonomous tasks.

Each takes a JobContext first and reports progress/log so a live progress bar can
render (the web "进度抽屉" polls keeper via the proxy). They run inside the
thread pool, never in the request. The UI-triggered actors (field_migration,
vacuum, compact, snapshot) are ported verbatim from quant_web.jobs.actors; the
crawl / quality / maintenance actors are new (autonomous, scheduler-driven).
"""
from __future__ import annotations

from quant_data import compaction, migration, quality_log, table_stats


# ── UI-triggered heavy writes (ported from quant_web) ───────────────────────
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


def refresh_stats(ctx) -> dict:
    ctx.log("刷新表统计 (table_stats)")
    ctx.progress(0, 1, "tables")
    rows = table_stats.refresh_all(root=ctx.root)
    ctx.progress(1, 1, "tables")
    ctx.log(f"刷新完成 · {len(rows)} 张表")
    return {"refreshed": len(rows)}


# ── autonomous (scheduler-driven) tasks ─────────────────────────────────────
def quality_scan(ctx) -> dict:
    """Periodic data-quality patrol: refresh stats and report open issues by
    table so the dashboard freshness / quality KPIs stay current and a spike in
    unacknowledged issues is visible in the logs (V3 §4.1 定时质检)."""
    ctx.log("定时质检巡检 · 刷新表统计")
    table_stats.refresh_all(root=ctx.root)
    rows = quality_log.by_table(root=ctx.root)
    total_open = 0
    for i, r in enumerate(rows):
        # by_table returns per-table aggregates; sum the unacknowledged ones.
        open_count = int(r.get("unacknowledged") or r.get("open") or 0)
        total_open += open_count
        ctx.progress(i + 1, max(1, len(rows)), "tables")
        if open_count:
            ctx.log(f"{r.get('target_table')}: {open_count} 个未确认质量问题")
    ctx.log(f"巡检完成 · 共 {total_open} 个未确认质量问题")
    return {"tables": len(rows), "open_issues": total_open}


def scheduled_maintenance(ctx, tables: list[str] | None = None) -> dict:
    """Off-hours maintenance: compact every raw table then refresh stats and
    take a metadata snapshot (V3 §4.1 定时维护 compact/refresh-stats/snapshot)."""
    from quant_data.schemas import RAW_TABLES

    tables = tables or list(RAW_TABLES)
    merged, total = 0, len(tables)
    for i, t in enumerate(tables):
        ctx.log(f"compact {t}")
        res = compaction.compact(t, confirm=True, root=ctx.root)
        merged += res.get("compacted_partitions", 0)
        ctx.progress(i + 1, total, "tables")
    ctx.log("刷新表统计")
    table_stats.refresh_all(root=ctx.root)
    snap = compaction.snapshot_metadata(root=ctx.root)
    ctx.log(f"维护完成 · 合并 {merged} 个分区 · 快照 {snap}")
    return {"compacted_partitions": merged, "tables": tables, "snapshot": snap}


def crawl(ctx, table: str, **params) -> dict:
    """Fetch + ingest one raw table via the akshare→tushare fallback pipeline.

    The whole run (fetch → ingest → built-in quality → batch register) shares the
    job's trace_id (re-bound by the runner), so /logs/trace/{id} stitches the
    autonomous采集 chain together (V3 §6.4)."""
    from ..ingest import pipeline

    ctx.log(f"采集启动 · {table} · params={params}")
    ctx.progress(0, 1, table)
    result = pipeline.crawl_table(table, root=ctx.root, **params)
    ctx.progress(1, 1, table)
    ctx.log(f"采集完成 · {table} · {result}")
    return result
