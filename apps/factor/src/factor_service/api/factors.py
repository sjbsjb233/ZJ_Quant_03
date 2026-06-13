"""Factor metadata endpoints — list / get / patch / values export."""
from __future__ import annotations

import datetime as dt
import io
from typing import Any

import polars as pl
from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from quant_data import DataReader
from quant_data import logging as qd_logging
from quant_factor.registry import all_registered, instantiate, instantiate_from_code

from ..db.models import (
    EvalSummary,
    FactorRecord,
    FactorVersion,
    RunResourceSummary,
    Universe,
)
from ..deps import current_user, get_db, get_reader, require_admin, require_editor
from ..errors import ApiError
from ..services import resource_baseline as _rbl


router = APIRouter()

_qlog = qd_logging.get_logger("factor.factors")


# ─────────────────────────────────────────────────────────────────────────────
@router.get("/factors")
def list_factors(
    q: str | None = None,
    category: str | None = None,
    status: str | None = None,
    source: str | None = None,
    direction: str | None = None,
    tracking_only: bool = False,
    include_archived: bool = False,
    sort: str = "icir_annual_desc",
    page: int = 1,
    page_size: int = 25,
    format: str | None = None,
    db: Session = Depends(get_db),
    user=Depends(current_user),
) -> Any:
    """List factors with their latest summary scalars + derived flags
    (decaying / never_eval / ic_spark)."""
    query = db.query(FactorRecord)
    if status:
        query = query.filter(FactorRecord.status == status)
    elif not include_archived:
        query = query.filter(FactorRecord.status != "archived")
    if category:
        query = query.filter(FactorRecord.category == category)
    if source:
        query = query.filter(FactorRecord.source == source)
    if direction in ("+", "-"):
        query = query.filter(FactorRecord.direction == (1 if direction == "+" else -1))
    if tracking_only:
        query = query.filter(FactorRecord.tracking_enabled == True)  # noqa: E712
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(
            (FactorRecord.factor_id.ilike(like))
            | (FactorRecord.display_name.ilike(like))
            | (FactorRecord.base_name.ilike(like))
        )

    all_rows = query.all()

    # Decorate with latest summary
    items: list[dict[str, Any]] = []
    for f in all_rows:
        latest = (
            db.query(EvalSummary)
            .filter(EvalSummary.factor_id == f.factor_id)
            .order_by(EvalSummary.id.desc())
            .first()
        )
        # Decay / never-eval flags
        decaying = _is_decaying(db, f.factor_id)
        never_eval = latest is None
        d = _serialize_factor(f, latest)
        d["decaying"] = decaying
        d["never_eval"] = never_eval
        items.append(d)

    # Sort
    key, _, direction_ = sort.partition("_")
    desc = direction_ in ("desc", "")
    sort_map = {
        "icir": "icir",
        "icirAnnual": "icir_annual",
        "icir_annual": "icir_annual",
        "rank_ic": "rank_ic_mean",
        "icir_ann": "icir_annual",
        "updated": "updated_at",
    }
    sort_key = sort_map.get(key, "icir_annual")

    def _sort_val(d: dict) -> Any:
        v = d.get(sort_key)
        if v is None:
            return float("-inf") if desc else float("inf")
        return v

    items.sort(key=_sort_val, reverse=desc)

    total = len(items)
    start = max(0, (page - 1) * page_size)
    page_items = items[start : start + page_size]

    # CSV export — entire current selection (ignores pagination)
    if format == "csv":
        buf = io.StringIO()
        cols = [
            "factor_id",
            "display_name",
            "category",
            "direction",
            "source",
            "status",
            "rank_ic_mean",
            "icir",
            "icir_annual",
            "ic_win_rate",
            "ls_annual_return",
            "turnover",
            "tracking_enabled",
            "updated_at",
        ]
        buf.write(",".join(cols) + "\n")
        for d in items:
            buf.write(
                ",".join(_csv_safe(d.get(c)) for c in cols) + "\n"
            )
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="factors.csv"'
            },
        )

    # KPI rollup (design — top KPI strip on /library)
    enabled = sum(1 for d in items if d["status"] == "enabled")
    disabled = sum(1 for d in items if d["status"] == "disabled")
    archived = sum(1 for d in items if d["status"] == "archived")
    tracked = sum(1 for d in items if d.get("tracking_enabled"))
    decaying_n = sum(1 for d in items if d.get("decaying"))
    never_n = sum(1 for d in items if d.get("never_eval"))
    top_icir = max(
        (d.get("icir_annual") or 0.0) for d in items
    ) if items else 0.0
    top_icir_id = next(
        (
            d["factor_id"]
            for d in items
            if (d.get("icir_annual") or 0.0) == top_icir
        ),
        None,
    )

    return {
        "items": page_items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "kpi": {
            "total": total,
            "enabled": enabled,
            "disabled": disabled,
            "archived": archived,
            "tracked": tracked,
            "top_icir": round(top_icir, 2),
            "top_icir_id": top_icir_id,
            "decaying": decaying_n,
            "never_eval": never_n,
        },
    }


def _csv_safe(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).replace('"', '""')
    if "," in s or "\n" in s:
        return f'"{s}"'
    return s


def _is_decaying(db: Session, factor_id: str) -> bool:
    """Decaying rule (design §19.2-A):
       - last 3+ ICIRs strictly decreasing, OR
       - last ICIR > 30% below the peak of last 7
    """
    rows = (
        db.query(EvalSummary)
        .filter(EvalSummary.factor_id == factor_id)
        .order_by(EvalSummary.id.desc())
        .limit(7)
        .all()
    )
    if len(rows) < 3:
        return False
    icirs = [r.icir_annual or 0.0 for r in rows]  # newest first
    last3 = icirs[:3]
    monotonic_down = last3[0] < last3[1] < last3[2]
    peak = max(icirs)
    drop = (peak - icirs[0]) > 0.3 * peak if peak > 0 else False
    return bool(monotonic_down or drop)


def _serialize_factor(f: FactorRecord, latest: EvalSummary | None) -> dict[str, Any]:
    d: dict[str, Any] = {
        "factor_id": f.factor_id,
        "base_name": f.base_name,
        "display_name": f.display_name or f.base_name,
        "category": f.category,
        "direction": f.direction,
        "source": f.source,
        "status": f.status,
        "tracking_enabled": f.tracking_enabled,
        "tracking_universe_id": f.tracking_universe_id,
        "tags": list(f.tags or []),
        "description": f.description or "",
        "owner": f.owner or "",
        "updated_at": f.updated_at.isoformat() if f.updated_at else None,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }
    if latest is not None:
        d.update(
            {
                "rank_ic_mean": float(latest.rank_ic_mean),
                "ic_mean": float(latest.ic_mean),
                "icir": float(latest.icir),
                "icir_annual": float(latest.icir_annual),
                "ic_win_rate": float(latest.ic_win_rate),
                "ls_annual_return": float(latest.ls_annual_return),
                "turnover": float(latest.turnover),
                "coverage": float(latest.coverage),
                "monotonic": bool(latest.monotonic),
                "last_eval_at": latest.as_of.isoformat() if latest.as_of else None,
                "last_universe_id": latest.universe_id,
                "ic_spark": list(latest.ic_spark or []),
                # latest eval's degradation snapshot for the library badge (设计 §8.1/§11.1)
                "degradation_level": getattr(latest, "degradation_level", "none") or "none",
                "degradation_count": getattr(latest, "degradation_count", 0) or 0,
                "has_critical": bool(getattr(latest, "has_critical", False)),
            }
        )
    else:
        d.update(
            {
                "rank_ic_mean": None,
                "ic_mean": None,
                "icir": None,
                "icir_annual": None,
                "ic_win_rate": None,
                "ls_annual_return": None,
                "turnover": None,
                "ic_spark": [],
                "last_eval_at": None,
            }
        )
    return d


# ─────────────────────────────────────────────────────────────────────────────
@router.get("/factors/{factor_id}")
def get_factor(
    factor_id: str,
    db: Session = Depends(get_db),
    user=Depends(current_user),
) -> dict[str, Any]:
    f = db.get(FactorRecord, factor_id)
    if f is None:
        raise ApiError("FACTOR_NOT_FOUND", f"因子 {factor_id} 不存在")

    versions = (
        db.query(FactorVersion)
        .filter(FactorVersion.factor_id == factor_id)
        .order_by(FactorVersion.id.desc())
        .all()
    )
    latest = (
        db.query(EvalSummary)
        .filter(EvalSummary.factor_id == factor_id)
        .order_by(EvalSummary.id.desc())
        .first()
    )

    serialized = _serialize_factor(f, latest)
    serialized["versions"] = [
        {
            "version": v.version,
            "class_name": v.class_name,
            "code_sha256": v.code_sha256,
            "enabled": v.enabled,
            "requires_pit": v.requires_pit,
            "dependencies": list(v.dependencies or []),
            "param_schema": dict(v.param_schema or {}),
            "created_at": v.created_at.isoformat() if v.created_at else None,
        }
        for v in versions
    ]
    serialized["dependencies"] = (
        list(versions[0].dependencies or []) if versions else []
    )
    serialized["decaying"] = _is_decaying(db, factor_id)
    return serialized


# ─────────────────────────────────────────────────────────────────────────────
@router.get("/factors/{factor_id}/versions")
def list_versions(
    factor_id: str, db: Session = Depends(get_db), user=Depends(current_user)
) -> dict:
    rows = (
        db.query(FactorVersion)
        .filter(FactorVersion.factor_id == factor_id)
        .order_by(FactorVersion.id.desc())
        .all()
    )
    return {
        "items": [
            {
                "version": v.version,
                "class_name": v.class_name,
                "code_sha256": v.code_sha256,
                "enabled": v.enabled,
                "requires_pit": v.requires_pit,
                "dependencies": list(v.dependencies or []),
                "param_schema": dict(v.param_schema or {}),
                "validation_report": dict(v.validation_report or {}),
                "created_at": v.created_at.isoformat() if v.created_at else None,
            }
            for v in rows
        ]
    }


@router.get("/factors/{factor_id}/dependencies")
def get_dependencies(
    factor_id: str, db: Session = Depends(get_db), user=Depends(current_user)
) -> dict:
    v = (
        db.query(FactorVersion)
        .filter(FactorVersion.factor_id == factor_id, FactorVersion.enabled == True)  # noqa: E712
        .order_by(FactorVersion.id.desc())
        .first()
    )
    deps = list(v.dependencies or []) if v else []
    return {"factor_id": factor_id, "dependencies": deps}


@router.get("/factors/{factor_id}/consumers")
def get_consumers(
    factor_id: str, db: Session = Depends(get_db), user=Depends(current_user)
) -> dict:
    # Until the strategy layer ships, only evaluation runs reference factors.
    from ..db.models import FactorRun

    runs = (
        db.query(FactorRun)
        .filter(FactorRun.factor_id == factor_id)
        .order_by(FactorRun.created_at.desc())
        .limit(20)
        .all()
    )
    return {
        "factor_id": factor_id,
        "evaluations": [
            {
                "run_id": r.run_id,
                "status": r.status,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in runs
        ],
        "strategies": [],
    }


@router.patch("/factors/{factor_id}")
def patch_factor(
    factor_id: str,
    body: dict[str, Any],
    db: Session = Depends(get_db),
    user=Depends(require_editor),
) -> dict:
    f = db.get(FactorRecord, factor_id)
    if f is None:
        raise ApiError("FACTOR_NOT_FOUND", f"因子 {factor_id} 不存在")
    allowed = {
        "status",
        "display_name",
        "description",
        "tags",
        "tracking_enabled",
        "tracking_universe_id",
        "category",
        "direction",
    }
    changed = [k for k in body if k in allowed]
    for k, v in body.items():
        if k in allowed:
            setattr(f, k, v)
    db.commit()
    db.refresh(f)
    _qlog.info("factor.update", factor_id=factor_id, fields=sorted(changed))
    latest = (
        db.query(EvalSummary)
        .filter(EvalSummary.factor_id == factor_id)
        .order_by(EvalSummary.id.desc())
        .first()
    )
    return _serialize_factor(f, latest)


@router.delete("/factors/{factor_id}")
def archive_factor(
    factor_id: str,
    db: Session = Depends(get_db),
    user=Depends(require_editor),
) -> dict:
    f = db.get(FactorRecord, factor_id)
    if f is None:
        raise ApiError("FACTOR_NOT_FOUND", f"因子 {factor_id} 不存在")
    f.status = "archived"
    db.commit()
    _qlog.info("factor.archive", factor_id=factor_id)
    return {"factor_id": factor_id, "status": "archived"}


@router.post("/factors/{factor_id}/versions/{version}/enable")
def enable_version(
    factor_id: str,
    version: str,
    db: Session = Depends(get_db),
    user=Depends(require_editor),
) -> dict:
    v = (
        db.query(FactorVersion)
        .filter(FactorVersion.factor_id == factor_id, FactorVersion.version == version)
        .first()
    )
    if v is None:
        raise ApiError("VERSION_NOT_FOUND", f"版本 {factor_id}@{version} 不存在")
    # Disable any other enabled version (one-at-a-time semantics)
    db.query(FactorVersion).filter(
        FactorVersion.factor_id == factor_id,
        FactorVersion.version != version,
        FactorVersion.enabled == True,  # noqa: E712
    ).update({"enabled": False})
    v.enabled = True
    db.commit()
    _qlog.info("factor.version.enable", factor_id=factor_id, version=version,
               expr_hash=v.code_sha256)
    return {"factor_id": factor_id, "version": version, "enabled": True}


# ─────────────────────────────────────────────────────────────────────────────
@router.post("/factors/{factor_id}/values")
def factor_values(
    factor_id: str,
    body: dict[str, Any],
    db: Session = Depends(get_db),
    reader: DataReader = Depends(get_reader),
    user=Depends(current_user),
):
    """Compute factor values on demand for a [start, end] window.

    Body:
      { params: {...}, symbols: [...], start: 'YYYY-MM-DD', end: '...',
        format: 'json' | 'csv' | 'parquet', limit: int (optional) }
    """
    rec = db.get(FactorRecord, factor_id)
    if rec is None:
        raise ApiError("FACTOR_NOT_FOUND", f"因子 {factor_id} 不存在")
    ver = (
        db.query(FactorVersion)
        .filter(FactorVersion.factor_id == factor_id, FactorVersion.enabled == True)  # noqa: E712
        .order_by(FactorVersion.id.desc())
        .first()
    )
    if ver is None:
        raise ApiError(
            "PRECONDITION_FAILED", "该因子无 enabled 版本,无法计算"
        )
    params = body.get("params") or {}
    symbols = body.get("symbols") or None
    if not symbols:
        raise ApiError(
            "INVALID_PARAMETER", "symbols 是必填项 (示例: ['600519.SH'])"
        )
    start = body.get("start")
    end = body.get("end")
    if not (start and end):
        raise ApiError("INVALID_PARAMETER", "start / end 是必填项")
    start_d = dt.date.fromisoformat(start)
    end_d = dt.date.fromisoformat(end)
    fmt = body.get("format") or "json"
    limit = body.get("limit")

    from quant_factor.base import FactorContext

    if ver.code_path:
        with open(ver.code_path, encoding="utf-8") as fh:
            factor = instantiate_from_code(fh.read(), ver.class_name, params)
    else:
        factor = instantiate(rec.base_name, params)

    ctx = FactorContext(reader, list(symbols), start_d, end_d)
    if factor.requires_pit:
        # Compute per-day for the requested range.
        chunks: list[pl.DataFrame] = []
        for d in pl.date_range(start_d, end_d, "1d", eager=True):
            try:
                xs = factor.compute_cross_section(ctx, d)
            except NotImplementedError:
                raise ApiError(
                    "PRECONDITION_FAILED",
                    "该 PIT 因子未实现 compute_cross_section",
                )
            if xs is None or xs.height == 0:
                continue
            xs = xs.with_columns(pl.lit(d).cast(pl.Date).alias("date"))
            chunks.append(xs.select(["date", "symbol", "value"]))
        out = pl.concat(chunks) if chunks else pl.DataFrame(
            schema={"date": pl.Date, "symbol": pl.Utf8, "value": pl.Float64}
        )
    else:
        out = factor.compute(ctx).select(["date", "symbol", "value"])

    if limit:
        out = out.head(int(limit))

    if fmt == "csv":
        buf = io.StringIO()
        out.write_csv(buf)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{factor_id}_values.csv"'
            },
        )
    if fmt == "parquet":
        buf = io.BytesIO()
        out.write_parquet(buf)
        buf.seek(0)
        return StreamingResponse(
            iter([buf.read()]),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{factor_id}_values.parquet"'
            },
        )
    return {
        "factor_id": factor_id,
        "rows": out.height,
        "items": out.to_dicts(),
    }


# ─────────────────────────────────────────────────────────────────────────────
@router.get("/factors/{factor_id}/ic")
def factor_ic(
    factor_id: str,
    db: Session = Depends(get_db),
    user=Depends(current_user),
) -> dict:
    """Latest IC summary + decay across horizons (from the most recent run)."""
    from .results import _latest_run_id  # local helper

    run_id = _latest_run_id(db, factor_id)
    if not run_id:
        return {"factor_id": factor_id, "items": []}
    from ..services.results_writer import read_decay

    decay = read_decay(factor_id, run_id)
    items = []
    for h, stats in (decay or {}).items():
        items.append({"horizon": int(h), **stats})
    items.sort(key=lambda x: x["horizon"])
    return {"factor_id": factor_id, "run_id": run_id, "items": items}


@router.get("/factors/{factor_id}/resource-baseline")
def factor_resource_baseline(
    factor_id: str,
    signature: str | None = None,
    class_signature: str | None = None,
    db: Session = Depends(get_db),
    user=Depends(require_admin),
) -> dict:
    """资源画像基线 (资源画像方案 §6) — 两级签名 stat 行 + 最近 10 次 run summary.

    管理员可见 (性能分析定位为管理功能, 方案 §6 权限)。``signature`` =本次运行的
    精确签名 (前端从 trace ``resources.summary`` 取得);省略时仅返回历史趋势点列。
    """
    def _stat(sig: str | None, kind: str) -> dict | None:
        if not sig:
            return None
        st = _rbl.read_stat(db, sig, kind)
        if st is None:
            return None
        return {
            "signature": sig, "attribution_kind": kind,
            "p50_peak_mb": round(st.p50_peak_mb, 1), "p90_peak_mb": round(st.p90_peak_mb, 1),
            "p50_cpu_sec": round(st.p50_cpu_sec, 1), "p90_cpu_sec": round(st.p90_cpu_sec, 1),
            "p50_peak_per_mrow": round(st.p50_peak_per_mrow, 1),
            "p90_peak_per_mrow": round(st.p90_peak_per_mrow, 1),
            "p50_cpu_sec_per_mrow": round(st.p50_cpu_sec_per_mrow, 2),
            "p90_cpu_sec_per_mrow": round(st.p90_cpu_sec_per_mrow, 2),
            "sample_n": st.sample_n, "version": st.version,
        }

    recent = (
        db.query(RunResourceSummary)
        .filter(RunResourceSummary.factor_id == factor_id)
        .order_by(RunResourceSummary.created_at.desc())
        .limit(10)
        .all()
    )
    recent_pts = [{
        "run_id": s.run_id, "version": s.version,
        "peak_rss_mb": round(s.peak_rss_mb, 1), "peak_per_mrow": round(s.peak_per_mrow, 1),
        "cpu_sec": round(s.cpu_sec, 1), "attribution": s.attribution,
        "profiled": s.profiled,
        "created_at": s.created_at.isoformat() + "Z" if s.created_at else None,
    } for s in reversed(recent)]  # chronological for the trend line

    return {
        "factor_id": factor_id,
        "signature": signature,
        "class_signature": class_signature,
        "exact": {
            "exact": _stat(signature, "exact"),
            "shared": _stat(signature, "shared"),
        },
        "klass": {
            "exact": _stat(class_signature, "exact"),
            "shared": _stat(class_signature, "shared"),
        },
        "recent": recent_pts,
    }


@router.get("/factors/{factor_id}/tracking")
def factor_tracking(
    factor_id: str,
    db: Session = Depends(get_db),
    user=Depends(current_user),
) -> dict:
    rows = (
        db.query(EvalSummary)
        .filter(EvalSummary.factor_id == factor_id)
        .order_by(EvalSummary.as_of.asc())
        .all()
    )
    return {
        "factor_id": factor_id,
        "items": [
            {
                "as_of": r.as_of.isoformat() if r.as_of else None,
                "universe_id": r.universe_id,
                "icir": r.icir,
                "icir_annual": r.icir_annual,
                "rank_ic_mean": r.rank_ic_mean,
                "ls_annual_return": r.ls_annual_return,
                "turnover": r.turnover,
            }
            for r in rows
        ],
        "decaying": _is_decaying(db, factor_id),
    }
