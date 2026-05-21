"""Dashboard summary (doc §4.1) -- the overview KPIs / health / hotspots feed."""
from __future__ import annotations

import datetime as dt
import os

from fastapi import APIRouter, Depends

from quant_data import compaction, table_stats
from quant_data.metadata import batches, field_registry
from quant_data.schemas import RAW_TABLES

from ..deps import get_current_user, get_root

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _vacuumable(root: str) -> tuple[int, int]:
    seen: dict[str, int] = {}
    for table in RAW_TABLES:
        try:
            for d in compaction.dead_files(table, older_than_days=90, root=root):
                f = d["file"]
                if f not in seen and os.path.exists(f):
                    seen[f] = os.path.getsize(f)
        except Exception:  # noqa: BLE001
            continue
    return sum(seen.values()), len(seen)


@router.get("/summary")
def summary(root: str = Depends(get_root), user=Depends(get_current_user)):
    now = dt.datetime.utcnow()
    today0 = dt.datetime(now.year, now.month, now.day)
    week_ago = now - dt.timedelta(days=7)
    all_batches = batches.list_batches(root=root)

    today = [b for b in all_batches if (b.get("started_at") or dt.datetime.min) >= today0]
    t_succ = sum(1 for b in today if b["status"] == "success")
    t_fail = sum(1 for b in today if b["status"] == "failed")
    t_run = sum(1 for b in today if b["status"] == "running")

    wk = [b for b in all_batches
          if (b.get("finished_at") or b.get("started_at") or dt.datetime.min) >= week_ago
          and b["status"] in ("success", "failed")]
    wk_succ = sum(1 for b in wk if b["status"] == "success")
    rate = round(wk_succ / len(wk), 4) if wk else 1.0

    stale = field_registry.stale_extras(days=90, root=root)
    vac_bytes, vac_files = _vacuumable(root)

    stats = table_stats.get_stats(root=root)
    by_id = {b["batch_id"]: b for b in all_batches}
    tables = []
    for s in stats:
        lb = by_id.get(s.get("latest_batch_id"))
        tables.append({
            "name": s["table_name"],
            "row_count": s.get("row_count"),
            "disk_bytes": s.get("disk_bytes"),
            "latest_batch": ({
                "batch_id": lb["batch_id"],
                "started_at": lb.get("started_at"),
                "source": lb.get("source"),
            } if lb else None),
            "freshness": s.get("freshness_status") or "info",
        })

    stale_running = [b for b in all_batches
                     if b["status"] == "running"
                     and (b.get("started_at") or now) < now - dt.timedelta(hours=24)]

    hotspots = [
        {"type": "stale_running", "count": len(stale_running),
         "detail": (f"{stale_running[0]['target_table']} · 可能 ingest 崩溃"
                    if stale_running else "无 stale running 批次")},
        {"type": "stale_extras", "count": len(stale),
         "detail": ", ".join(s["field_name"] for s in stale[:3]) or "无陈旧 extras 字段"},
        {"type": "vacuumable", "count": vac_files,
         "detail": f"{_fmt_gb(vac_bytes)} 已过保留期"},
    ]

    recent = sorted(all_batches, key=lambda b: b.get("started_at") or dt.datetime.min,
                    reverse=True)[:20]
    recent_out = [{
        "batch_id": b["batch_id"],
        "status": b["status"],
        "target_table": b["target_table"],
        "source": b["source"],
        "started_at": b.get("started_at"),
        "row_count": b.get("row_count"),
        "summary": (f"{b['row_count']:,} 行" if b.get("row_count")
                    else ("进行中" if b["status"] == "running"
                          else (b.get("error_message") or "—"))),
    } for b in recent]

    return {
        "as_of": now,
        "kpis": {
            "batches_today": {"total": len(today), "success": t_succ,
                              "failed": t_fail, "running": t_run},
            "success_rate_7d": {"value": rate, "baseline": 0.95},
            "stale_extras": {"count": len(stale),
                             "examples": [s["field_name"] for s in stale[:3]]},
            "vacuumable": {"bytes": vac_bytes, "files": vac_files},
        },
        "tables": tables,
        "hotspots": hotspots,
        "recent_batches": recent_out,
    }


def _fmt_gb(n: int) -> str:
    if n >= 1024**3:
        return f"{n / 1024**3:.1f} GB"
    if n >= 1024**2:
        return f"{n / 1024**2:.1f} MB"
    return f"{n} B"
