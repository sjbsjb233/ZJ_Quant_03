"""因子篮子 API — CRUD · 修订 · 运行 · 快照 · 条目结果 (因子层重构 §12)."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from quant_data import DataReader
from quant_data import logging as qd_logging

from ..baskets import keys, ledger, revisions, runner
from ..baskets.orchestrator import cancel_run as _cancel_run
from ..config import get_settings
from ..db.models import (
    Basket,
    BasketItemState,
    BasketRevision,
    BasketRun,
    BasketRunItem,
    BasketSnapshot,
    FactorRecord,
    FactorRun,
    FactorVersion,
    Task,
)
from ..deps import current_user, get_db, require_editor
from ..errors import ApiError
from ..logging_ctx import trace_id_var

router = APIRouter()
_qlog = qd_logging.get_logger("factor.baskets")


# ─────────────────────────────────────────────────────────────────────────────
def _get_basket(db: Session, basket_id: str) -> Basket:
    b = db.get(Basket, basket_id)
    if b is None:
        raise ApiError("BASKET_NOT_FOUND", f"篮子 {basket_id} 不存在")
    return b


def _active_rev(db: Session, b: Basket) -> BasketRevision | None:
    return runner.active_revision(db, b)


def _resolve_items(db: Session, items: list[dict]) -> tuple[list[dict], dict]:
    """Fill missing version (enabled) + build default labels. Validates factors."""
    labels: dict[str, str] = {}
    out = []
    for it in items:
        fid = it.get("factor_id")
        if not fid:
            raise ApiError("INVALID_PARAMETER", "条目缺少 factor_id")
        rec = db.get(FactorRecord, fid)
        if rec is None:
            raise ApiError("FACTOR_NOT_FOUND", f"因子 {fid} 不存在")
        version = it.get("version")
        if not version:
            ver = (
                db.query(FactorVersion)
                .filter(FactorVersion.factor_id == fid, FactorVersion.enabled == True)  # noqa: E712
                .order_by(FactorVersion.id.desc())
                .first()
            )
            version = ver.version if ver else "v1"
        params = it.get("params") or {}
        base = rec.display_name or rec.base_name
        labels[fid] = base
        lbl = it.get("label")
        if not lbl:
            if params:
                lbl = f"{base}·" + "/".join(str(v) for v in params.values())
            else:
                lbl = base
        out.append({"item_id": it.get("item_id"), "factor_id": fid, "version": version,
                    "params": params, "label": lbl, "sort": it.get("sort")})
    return out, labels


def _latest_snapshot(db: Session, basket_id: str) -> BasketSnapshot | None:
    return (
        db.query(BasketSnapshot)
        .filter(BasketSnapshot.basket_id == basket_id)
        .order_by(BasketSnapshot.created_at.desc())
        .first()
    )


def _iso(d) -> str | None:
    if d is None:
        return None
    if isinstance(d, (dt.date, dt.datetime)):
        return d.isoformat()[:10] if isinstance(d, dt.date) and not isinstance(d, dt.datetime) else d.isoformat()
    return str(d)


def _tracking_block(b: Basket) -> dict:
    state = "off"
    if b.tracking_enabled:
        state = "paused" if b.tracking_paused_reason else "on"
    return {
        "enabled": b.tracking_enabled, "state": state,
        "hour": b.tracking_hour, "minute": b.tracking_minute,
        "time": f"{b.tracking_hour:02d}:{b.tracking_minute:02d}",
        "paused_reason": b.tracking_paused_reason,
        "next": f"今日 {b.tracking_hour:02d}:{b.tracking_minute:02d}" if state == "on" else None,
    }


def _stale_block(b: Basket) -> dict | None:
    if b.stale_scope == "none":
        return None
    scope = "全量重建" if b.stale_scope == "full" else f"条目 {','.join(b.stale_items or [])}"
    return {"is": True, "scope": scope, "scope_kind": b.stale_scope,
            "items": b.stale_items or [], "reason": b.stale_reason,
            "toRev": b.active_revision, "fromRev": max(1, b.active_revision - 1)}


def _serialize_basket(db: Session, b: Basket, *, detail: bool = False) -> dict:
    rev = _active_rev(db, b)
    shared = dict(rev.shared_config) if rev else {}
    snap = _latest_snapshot(db, b.basket_id)
    stats = dict(snap.basket_stats) if snap else {}
    out = {
        "basket_id": b.basket_id, "id": b.basket_id, "name": b.name,
        "description": b.description, "desc": b.description, "status": b.status,
        "rev": b.active_revision, "active_revision": b.active_revision,
        "tracking": _tracking_block(b), "stale": _stale_block(b),
        "corr": {"on": b.correlation_enabled, "method": b.correlation_method.capitalize()
                 if b.correlation_method else "Pearson", "threshold": b.correlation_threshold},
        "correlation_enabled": b.correlation_enabled,
        "correlation_method": b.correlation_method,
        "correlation_threshold": b.correlation_threshold,
        "corr_min_periods": b.corr_min_periods,
        "acknowledge_degraded": b.acknowledge_degraded,
        "calibration": keys.calibration_summary(shared) if shared else {},
        "common": _common_display(shared) if shared else {},
        "entry_count": len(rev.items) if rev else 0,
        "owner": b.owner,
        "asOf": _iso(snap.as_of) if snap else None,
        "snapshot_id": snap.snapshot_id if snap else None,
        "snapshot_count": db.query(BasketSnapshot).filter(
            BasketSnapshot.basket_id == b.basket_id).count(),
        "stats": {
            "okCnt": stats.get("ok_count"), "degCnt": stats.get("degraded_count"),
            "failCnt": stats.get("failed_count"), "avgAbsIC": stats.get("avg_abs_ic"),
            "avgICIR": stats.get("avg_icir"), "redundancy": stats.get("redundancy"),
        } if snap else {},
        "mini": {"avgIC": stats.get("avg_abs_ic"),
                 "redundancy": f"{int((stats.get('redundancy') or 0) * 100)}%"} if snap else {},
        "pendingSettle": _pending_settle(db, b.basket_id),
        "created_at": _isodt(b.created_at), "updated_at": _isodt(b.updated_at),
    }
    if detail and rev:
        out["shared_config"] = shared
        out["items"] = rev.items
        out["revision"] = rev.revision
        out["common_key"] = rev.common_key
        out["comparable_key"] = rev.comparable_key
    return out


def _common_display(shared: dict) -> dict:
    """Map normalized shared_config → the design's `common` shape (口径 chips)."""
    cal = keys.calibration_summary(shared)
    w = keys.normalize_window(shared.get("window"))
    return {
        "pool": cal.get("universe_id"),
        "window": {"mode": w.get("mode"), "len": w.get("span"),
                   "start": w.get("start"), "end": w.get("end")},
        "freq": cal.get("rebalance"),
        "mainCycle": f"{cal.get('primary_horizon')} 日",
        "returnDef": cal.get("return_price"),
        "icType": cal.get("ic_type"),
        "mktNeutral": {"on": shared.get("neutralize_size"), "scope": shared.get("size_field")},
        "indNeutral": {"on": shared.get("neutralize_industry"),
                       "system": shared.get("classification_system"),
                       "level": shared.get("industry_level")},
        "layers": shared.get("quantiles"), "layerWeight": shared.get("layer_weight"),
        "cost": shared.get("cost_bps"), "fwdPeriods": shared.get("horizons"),
    }


def _isodt(d) -> str | None:
    return (d.isoformat() + "Z") if isinstance(d, dt.datetime) else (_iso(d) if d else None)


def _pending_settle(db: Session, basket_id: str) -> list[dict]:
    rows = (
        db.query(BasketItemState)
        .filter(BasketItemState.basket_id == basket_id)
        .all()
    )
    dates: set[str] = set()
    for r in rows:
        for d in (r.pending_dates or []):
            dates.add(d)
    out = []
    for d in sorted(dates)[:3]:
        try:
            dd = dt.date.fromisoformat(d)
            eta = (dd + dt.timedelta(days=7)).isoformat()[5:]
            out.append({"date": d[5:], "eta": eta})
        except Exception:  # noqa: BLE001
            pass
    return out


# ═════════════════════════════════════════════════════════════════════════════
@router.post("/baskets")
def create_basket(body: dict[str, Any], db: Session = Depends(get_db),
                  user=Depends(require_editor)) -> dict:
    name = (body.get("name") or "").strip()
    if not name:
        raise ApiError("INVALID_PARAMETER", "name 是必填项")
    if len(name) > 200:
        raise ApiError("INVALID_PARAMETER", "name 过长(上限 200 字符)")
    if len(body.get("items") or []) > 500:
        raise ApiError("INVALID_PARAMETER", "条目数超过上限 500")
    shared = dict(body.get("shared_config") or {})
    # validate window
    try:
        keys.window_descriptor(shared.get("window"))
    except keys.WindowError as e:
        raise ApiError("INVALID_PARAMETER", str(e))
    items_in = body.get("items") or []
    if not items_in:
        raise ApiError("INVALID_PARAMETER", "篮子至少需要 1 个条目")
    resolved, labels = _resolve_items(db, items_in)

    bid = f"bsk_{uuid.uuid4().hex[:16]}"
    b = Basket(
        basket_id=bid, name=name, description=body.get("description") or "",
        owner=user.username,
        correlation_enabled=bool(body.get("correlation_enabled", True)),
        correlation_method=(body.get("correlation_method") or "pearson"),
        correlation_threshold=float(body.get("correlation_threshold", 0.7)),
        corr_min_periods=int(body.get("corr_min_periods", 12)),
    )
    # tracking — refuse fixed window
    track = body.get("tracking") or {}
    if track.get("enabled"):
        if keys.is_fixed(shared.get("window")):
            raise ApiError("TRACKING_ON_FIXED_WINDOW", "固定窗口篮子不支持自动追踪")
        b.tracking_enabled = True
        b.tracking_hour = int(track.get("hour", 18))
        b.tracking_minute = int(track.get("minute", 30))
    db.add(b)
    db.commit()
    revisions.save_revision(db, b, shared_config=shared, items=resolved,
                            reason=body.get("reason") or "初始配置", author=user.username,
                            default_labels=labels)
    # rev 1 is not stale
    b.stale_scope = "none"
    b.stale_items = []
    b.tracking_paused_reason = None
    db.commit()
    _qlog.info("basket.create", basket_id=bid, name=name, n_items=len(resolved))

    run = None
    if body.get("run_now", True):
        reader = DataReader(root=get_settings().quant_data_root)
        run = runner.create_run(db, b, mode="full", trigger="manual",
                                initiated_by=user.username, trace_id=trace_id_var.get(),
                                reader=reader)
    out = _serialize_basket(db, b, detail=True)
    out["first_run"] = {"brun_id": run.brun_id} if run else None
    return out


@router.get("/baskets")
def list_baskets(status: str | None = None, q: str | None = None,
                 db: Session = Depends(get_db), user=Depends(current_user)) -> dict:
    query = db.query(Basket)
    if status:
        query = query.filter(Basket.status == status)
    if q:
        query = query.filter(Basket.name.ilike(f"%{q}%"))
    rows = query.order_by(Basket.updated_at.desc()).all()
    return {"items": [_serialize_basket(db, b) for b in rows],
            "total": len(rows)}


@router.get("/baskets/migration/legacy-tracked")
def legacy_tracked(db: Session = Depends(get_db), user=Depends(current_user)) -> dict:
    """单因子 track 下线迁移横幅 (§13) — factors that still have tracking on."""
    rows = (
        db.query(FactorRecord)
        .filter(FactorRecord.tracking_enabled == True)  # noqa: E712
        .all()
    )
    return {"items": [{"factor_id": r.factor_id, "name": r.display_name or r.base_name,
                       "tracking_universe_id": r.tracking_universe_id} for r in rows]}


@router.get("/baskets/{basket_id}")
def get_basket(basket_id: str, db: Session = Depends(get_db),
               user=Depends(current_user)) -> dict:
    b = _get_basket(db, basket_id)
    return _serialize_basket(db, b, detail=True)


@router.patch("/baskets/{basket_id}")
def patch_basket(basket_id: str, body: dict[str, Any], db: Session = Depends(get_db),
                 user=Depends(require_editor)) -> dict:
    b = _get_basket(db, basket_id)
    if "name" in body:
        name = (body["name"] or "").strip()
        if not name or len(name) > 200:
            raise ApiError("INVALID_PARAMETER", "name 为空或过长")
        b.name = name
    if "description" in body:
        b.description = body["description"]
    # correlation settings (§9.2: none-invalidation, takes effect next run) —
    # validate before persisting so a bad threshold/method can't reach prune (review M4).
    if "correlation_method" in body and body["correlation_method"] not in ("pearson", "spearman"):
        raise ApiError("INVALID_PARAMETER", "correlation_method 仅支持 pearson|spearman")
    if "correlation_threshold" in body:
        try:
            thr = float(body["correlation_threshold"])
        except (TypeError, ValueError):
            raise ApiError("INVALID_PARAMETER", "correlation_threshold 非法")
        if not 0.0 <= thr <= 1.0:
            raise ApiError("INVALID_PARAMETER", "correlation_threshold 必须在 [0,1]")
    if "corr_min_periods" in body and int(body["corr_min_periods"]) < 0:
        raise ApiError("INVALID_PARAMETER", "corr_min_periods 不能为负")
    for k_in, attr in (("correlation_enabled", "correlation_enabled"),
                       ("correlation_method", "correlation_method"),
                       ("correlation_threshold", "correlation_threshold"),
                       ("corr_min_periods", "corr_min_periods"),
                       ("acknowledge_degraded", "acknowledge_degraded")):
        if k_in in body:
            setattr(b, attr, body[k_in])
    db.commit()
    return _serialize_basket(db, b, detail=True)


@router.delete("/baskets/{basket_id}")
def archive_basket(basket_id: str, db: Session = Depends(get_db),
                   user=Depends(require_editor)) -> dict:
    b = _get_basket(db, basket_id)
    b.status = "archived"
    b.tracking_enabled = False
    db.commit()
    return {"basket_id": basket_id, "status": "archived"}


# ─── Revisions ───────────────────────────────────────────────────────────────
@router.post("/baskets/{basket_id}/revisions")
def save_revision_ep(basket_id: str, body: dict[str, Any], db: Session = Depends(get_db),
                     user=Depends(require_editor)) -> dict:
    b = _get_basket(db, basket_id)
    rev = _active_rev(db, b)
    shared = dict(body.get("shared_config") or (rev.shared_config if rev else {}))
    try:
        keys.window_descriptor(shared.get("window"))
    except keys.WindowError as e:
        raise ApiError("INVALID_PARAMETER", str(e))
    items_in = body.get("items")
    if items_in is None:
        items_in = list(rev.items) if rev else []
    resolved, labels = _resolve_items(db, items_in)
    new_rev = revisions.save_revision(db, b, shared_config=shared, items=resolved,
                                      reason=body.get("reason"), author=user.username,
                                      default_labels=labels)
    _qlog.info("basket.revision", basket_id=basket_id, revision=new_rev.revision,
               invalidation=new_rev.invalidation)
    return {"basket_id": basket_id, "revision": new_rev.revision,
            "invalidation": new_rev.invalidation,
            "change_summary": new_rev.change_summary,
            "stale_scope": b.stale_scope, "stale_items": b.stale_items}


@router.get("/baskets/{basket_id}/revisions")
def list_revisions(basket_id: str, db: Session = Depends(get_db),
                   user=Depends(current_user)) -> dict:
    _get_basket(db, basket_id)
    rows = (
        db.query(BasketRevision)
        .filter(BasketRevision.basket_id == basket_id)
        .order_by(BasketRevision.revision.desc())
        .all()
    )
    items = []
    for r in rows:
        brun = (
            db.query(BasketRun)
            .filter(BasketRun.basket_id == basket_id, BasketRun.revision == r.revision)
            .order_by(BasketRun.created_at.desc())
            .first()
        )
        items.append({
            "revision": r.revision, "rev": r.revision, "time": _isodt(r.created_at),
            "author": r.author, "reason": r.reason,
            "scope": _scope_label(r.invalidation), "invalidation": r.invalidation,
            "change_summary": r.change_summary,
            "changes": _changes_for_ui(r.change_summary),
            "runId": brun.brun_id if brun else None,
        })
    return {"items": items}


def _scope_label(inv: str) -> str:
    if inv == "full":
        return "全量重建"
    if inv.startswith("items:"):
        return "仅相关条目"
    return "仅相关性"


def _changes_for_ui(cs: dict) -> list[dict]:
    out = []
    for c in (cs or {}).get("shared_changed", []):
        out.append({"field": c["field"], "old": c.get("old"), "new": c.get("new"),
                    "level": "red" if c.get("level") == "hard" else "yellow"})
    if (cs or {}).get("items_added"):
        out.append({"field": "条目", "old": "—", "new": f"+{len(cs['items_added'])}",
                    "level": "normal"})
    if (cs or {}).get("items_modified"):
        out.append({"field": "条目修改", "old": "—", "new": f"~{len(cs['items_modified'])}",
                    "level": "yellow"})
    return out


# ─── Tracking ────────────────────────────────────────────────────────────────
@router.post("/baskets/{basket_id}/tracking")
def set_tracking(basket_id: str, body: dict[str, Any], db: Session = Depends(get_db),
                 user=Depends(require_editor)) -> dict:
    b = _get_basket(db, basket_id)
    enabled = bool(body.get("enabled"))
    if enabled:
        rev = _active_rev(db, b)
        if rev and keys.is_fixed((rev.shared_config or {}).get("window")):
            raise ApiError("TRACKING_ON_FIXED_WINDOW", "固定窗口篮子不支持自动追踪")
    b.tracking_enabled = enabled
    if "hour" in body:
        b.tracking_hour = int(body["hour"])
    if "minute" in body:
        b.tracking_minute = int(body["minute"])
    if not enabled:
        b.tracking_paused_reason = None
    db.commit()
    return {"basket_id": basket_id, "tracking": _tracking_block(b)}


# ─── Runs ────────────────────────────────────────────────────────────────────
@router.post("/baskets/{basket_id}/runs")
def submit_basket_run(basket_id: str, body: dict[str, Any], db: Session = Depends(get_db),
                      user=Depends(require_editor)) -> dict:
    b = _get_basket(db, basket_id)
    if b.status == "archived":
        raise ApiError("CONFLICT", "已归档篮子不可发起运行", status_code=409)
    mode = body.get("mode", "full")
    if mode not in ("full", "incremental", "repair", "corr_only"):
        raise ApiError("INVALID_PARAMETER", f"非法运行模式 {mode}")
    if mode == "incremental" and b.stale_scope == "full":
        raise ApiError("BASKET_STALE_CONFIG", "配置已全量失效,请先发起 full 重建")
    reader = DataReader(root=get_settings().quant_data_root)
    run = runner.create_run(
        db, b, mode=mode, trigger=body.get("trigger", "manual"),
        with_correlation=body.get("with_correlation"),
        initiated_by=user.username, trace_id=trace_id_var.get(), reader=reader,
        repair_item_ids=body.get("item_ids"),
    )
    return {"brun_id": run.brun_id, "basket_id": basket_id, "mode": mode,
            "status": run.status, "total_children": run.total_children}


@router.get("/baskets/{basket_id}/runs")
def list_runs(basket_id: str, db: Session = Depends(get_db),
              user=Depends(current_user)) -> dict:
    _get_basket(db, basket_id)
    rows = (
        db.query(BasketRun)
        .filter(BasketRun.basket_id == basket_id)
        .order_by(BasketRun.created_at.desc())
        .all()
    )
    return {"items": [_serialize_run(db, r) for r in rows]}


@router.get("/baskets/{basket_id}/runs/{brun_id}")
def get_run(basket_id: str, brun_id: str, db: Session = Depends(get_db),
            user=Depends(current_user)) -> dict:
    run = db.get(BasketRun, brun_id)
    if run is None or run.basket_id != basket_id:
        raise ApiError("BRUN_NOT_FOUND", f"运行 {brun_id} 不存在")
    return _serialize_run(db, run, with_children=True)


@router.post("/baskets/{basket_id}/runs/{brun_id}/cancel")
def cancel_basket_run(basket_id: str, brun_id: str, db: Session = Depends(get_db),
                      user=Depends(require_editor)) -> dict:
    run = db.get(BasketRun, brun_id)
    if run is None or run.basket_id != basket_id:
        raise ApiError("BRUN_NOT_FOUND", f"运行 {brun_id} 不存在")
    _cancel_run(db, run, actor=user.username)
    return {"brun_id": brun_id, "status": "canceled"}


@router.post("/baskets/{basket_id}/runs/{brun_id}/repair")
def repair_basket_run(basket_id: str, brun_id: str, db: Session = Depends(get_db),
                      user=Depends(require_editor)) -> dict:
    b = _get_basket(db, basket_id)
    run = db.get(BasketRun, brun_id)
    if run is None or run.basket_id != basket_id:
        raise ApiError("BRUN_NOT_FOUND", f"运行 {brun_id} 不存在")
    failed = runner.failed_item_ids(db, brun_id)
    if not failed:
        raise ApiError("INVALID_PARAMETER", "该运行没有失败条目")
    reader = DataReader(root=get_settings().quant_data_root)
    new_run = runner.create_run(db, b, mode="repair", trigger="manual",
                                initiated_by=user.username, trace_id=trace_id_var.get(),
                                reader=reader, repair_item_ids=failed)
    return {"brun_id": new_run.brun_id, "mode": "repair", "item_ids": failed}


def _serialize_run(db: Session, run: BasketRun, *, with_children: bool = False) -> dict:
    out = {
        "brun_id": run.brun_id, "id": run.brun_id, "basket_id": run.basket_id,
        "revision": run.revision, "mode": _mode_label(run.mode), "mode_raw": run.mode,
        "trigger": _trigger_label(run.trigger), "trigger_raw": run.trigger,
        "status": _run_status_label(run.status), "status_raw": run.status,
        "with_correlation": run.with_correlation,
        "win": _iso(run.resolved_end), "asOf": _iso(run.as_of),
        "resolved_start": _iso(run.resolved_start), "resolved_end": _iso(run.resolved_end),
        "total": run.total_children, "done": run.done_children,
        "sub": {"ok": run.done_children - run.failed_children - run.degraded_children,
                "deg": run.degraded_children, "fail": run.failed_children},
        "progress": {"done": run.done_children, "failed": run.failed_children,
                     "total": run.total_children},
        "est_total_sec": run.est_total_sec, "snapshot_id": run.snapshot_id,
        "dur": _dur(run.duration_sec), "duration_sec": run.duration_sec,
        "error": run.error, "created_at": _isodt(run.created_at),
    }
    if with_children:
        out["children"] = _children(db, run)
    return out


def _children(db: Session, run: BasketRun) -> list[dict]:
    rev = (
        db.query(BasketRevision)
        .filter(BasketRevision.basket_id == run.basket_id, BasketRevision.revision == run.revision)
        .first()
    )
    labels = {it["item_id"]: it.get("label") for it in (rev.items if rev else [])}
    bris = (
        db.query(BasketRunItem)
        .filter(BasketRunItem.brun_id == run.brun_id)
        .order_by(BasketRunItem.seq_no)
        .all()
    )
    out = []
    for bri in bris:
        t = db.get(Task, bri.task_id) if bri.task_id else None
        frun = db.get(FactorRun, bri.run_id) if bri.run_id else None
        out.append({
            "i": bri.seq_no, "type": "eval", "item_id": bri.item_id,
            "entryId": bri.item_id, "label": labels.get(bri.item_id) or bri.item_id,
            "status": _run_status_label(bri.status), "status_raw": bri.status,
            "task_id": bri.task_id, "run_id": bri.run_id,
            "degrade": getattr(frun, "degradation_level", "none") if frun else "none",
            "used": _dur(frun.duration_sec) if frun and frun.duration_sec else None,
            "remain": _eta(t) if t and t.status in ("queued", "running") else None,
        })
    # corr task last
    if run.corr_task_id:
        ct = db.get(Task, run.corr_task_id)
        if ct:
            out.append({
                "i": 9999, "type": "corr", "label": "篮内相关性",
                "status": "等待评估" if ct.gate == "waiting" else _run_status_label(ct.status),
                "status_raw": ct.status, "gate": ct.gate, "task_id": ct.task_id,
                "note": "等待全部评估完成" if ct.gate == "waiting" else None,
            })
    return out


# ─── Snapshots ───────────────────────────────────────────────────────────────
@router.get("/baskets/{basket_id}/snapshots")
def list_snapshots(basket_id: str, db: Session = Depends(get_db),
                   user=Depends(current_user)) -> dict:
    _get_basket(db, basket_id)
    rows = (
        db.query(BasketSnapshot)
        .filter(BasketSnapshot.basket_id == basket_id)
        .order_by(BasketSnapshot.as_of.desc(), BasketSnapshot.created_at.desc())
        .all()
    )
    return {"items": [{"snapshot_id": s.snapshot_id, "id": s.snapshot_id,
                       "asOf": _iso(s.as_of), "rev": s.revision, "brun_id": s.brun_id,
                       "created_at": _isodt(s.created_at)} for s in rows]}


# NB: /snapshots/diff MUST be registered before /snapshots/{sid}, else FastAPI
# matches "diff" as a snapshot id and 404s (factor层验证 API-SNP-01).
@router.get("/baskets/{basket_id}/snapshots/diff")
def diff_snapshots(basket_id: str, a: str, b: str, db: Session = Depends(get_db),
                   user=Depends(current_user)) -> dict:
    sa = _get_snapshot(db, basket_id, a)
    sb = _get_snapshot(db, basket_id, b)
    keys_a = set(sa.item_summaries or {})
    keys_b = set(sb.item_summaries or {})
    metric_delta = {}
    for iid in keys_a & keys_b:
        ra, rb = sa.item_summaries[iid], sb.item_summaries[iid]
        metric_delta[iid] = {k: _delta(ra.get(k), rb.get(k))
                             for k in ("icir_annual", "rank_ic_mean", "ls_sharpe")}
    return {"a": {"snapshot_id": a, "asOf": _iso(sa.as_of)},
            "b": {"snapshot_id": b, "asOf": _iso(sb.as_of)},
            "items_added": sorted(keys_b - keys_a),
            "items_removed": sorted(keys_a - keys_b),
            "metric_delta": metric_delta}


def _get_snapshot(db: Session, basket_id: str, sid: str) -> BasketSnapshot:
    s = db.get(BasketSnapshot, sid)
    if s is None or s.basket_id != basket_id:
        raise ApiError("SNAPSHOT_NOT_FOUND", f"快照 {sid} 不存在")
    return s


@router.get("/baskets/{basket_id}/snapshots/{sid}")
def get_snapshot(basket_id: str, sid: str, db: Session = Depends(get_db),
                 user=Depends(current_user)) -> dict:
    s = _get_snapshot(db, basket_id, sid)
    rev = (
        db.query(BasketRevision)
        .filter(BasketRevision.basket_id == basket_id, BasketRevision.revision == s.revision)
        .first()
    )
    item_meta = {it["item_id"]: it for it in (rev.items if rev else [])}
    return {
        "snapshot_id": s.snapshot_id, "asOf": _iso(s.as_of), "revision": s.revision,
        "resolved_start": _iso(s.resolved_start), "resolved_end": _iso(s.resolved_end),
        "item_summaries": s.item_summaries, "basket_stats": s.basket_stats,
        "excluded_items": s.excluded_items, "item_meta": item_meta,
        "has_correlation": bool(s.corr_json),
    }


@router.get("/baskets/{basket_id}/snapshots/{sid}/correlation")
def get_correlation(basket_id: str, sid: str, db: Session = Depends(get_db),
                    user=Depends(current_user)) -> dict:
    s = _get_snapshot(db, basket_id, sid)
    return s.corr_json or {"labels": [], "matrix": {}, "high_pairs": [], "clusters": []}


def _delta(x, y):
    if x is None or y is None:
        return None
    return round(float(y) - float(x), 4)


# ─── Item detail (§10.3) ─────────────────────────────────────────────────────
@router.get("/baskets/{basket_id}/items/{item_id}")
def get_item(basket_id: str, item_id: str, snapshot: str | None = None,
             db: Session = Depends(get_db), user=Depends(current_user)) -> dict:
    b = _get_basket(db, basket_id)
    snap = _get_snapshot(db, basket_id, snapshot) if snapshot else _latest_snapshot(db, basket_id)
    rev = _active_rev(db, b)
    idef = next((it for it in (rev.items if rev else []) if it["item_id"] == item_id), None)
    if idef is None:
        raise ApiError("INVALID_PARAMETER", f"条目 {item_id} 不存在")
    st = (
        db.query(BasketItemState)
        .filter(BasketItemState.basket_id == basket_id, BasketItemState.item_id == item_id)
        .order_by(BasketItemState.updated_at.desc())
        .first()
    )
    summary = (snap.item_summaries.get(item_id) if snap else None) or {}
    run_id = summary.get("run_id")
    rec = db.get(FactorRecord, idef["factor_id"])
    return {
        "item_id": item_id, "basket_id": basket_id,
        "label": idef.get("label"), "factor_id": idef["factor_id"],
        "factor_name": rec.display_name or rec.base_name if rec else idef["factor_id"],
        "direction": rec.direction if rec else 1, "category": rec.category if rec else None,
        "version": idef.get("version"), "params": idef.get("params"),
        "item_key": idef.get("item_key"),
        "readable_id": f"{basket_id}.{item_id}",
        "rows_factor": st.rows_factor if st else 0, "rows_ic": st.rows_ic if st else 0,
        "settled_n": summary.get("settled_n"), "pending_n": summary.get("pending_n"),
        "degradation_level": summary.get("degradation_level", "none"),
        "metrics": summary, "snapshot_id": snap.snapshot_id if snap else None,
        "asOf": _iso(snap.as_of) if snap else None,
        "run_id": run_id,
    }


@router.get("/baskets/{basket_id}/items/{item_id}/series")
def get_item_series(basket_id: str, item_id: str, kind: str = "ic",
                    snapshot: str | None = None, db: Session = Depends(get_db),
                    user=Depends(current_user)) -> dict:
    b = _get_basket(db, basket_id)
    snap = _get_snapshot(db, basket_id, snapshot) if snapshot else _latest_snapshot(db, basket_id)
    rev = (
        db.query(BasketRevision)
        .filter(BasketRevision.basket_id == basket_id,
                BasketRevision.revision == (snap.revision if snap else b.active_revision))
        .first()
    )
    idef = next((it for it in (rev.items if rev else []) if it["item_id"] == item_id), None)
    if idef is None:
        raise ApiError("INVALID_PARAMETER", f"条目 {item_id} 不存在")
    revision = snap.revision if snap else b.active_revision
    idir = ledger.item_dir(get_settings().factor_store_root, basket_id, revision, idef["item_key"])
    start = snap.resolved_start if snap else None
    end = snap.resolved_end if snap else None
    return {"kind": kind, "rows": ledger.read_series(idir, kind, start, end)}


@router.get("/baskets/{basket_id}/items/{item_id}/history")
def get_item_history(basket_id: str, item_id: str, db: Session = Depends(get_db),
                     user=Depends(current_user)) -> dict:
    _get_basket(db, basket_id)
    snaps = (
        db.query(BasketSnapshot)
        .filter(BasketSnapshot.basket_id == basket_id)
        .order_by(BasketSnapshot.as_of.asc())
        .all()
    )
    series = []
    for s in snaps:
        m = (s.item_summaries or {}).get(item_id)
        if not m:
            continue
        series.append({"asOf": _iso(s.as_of), "snapshot_id": s.snapshot_id,
                       "icir_annual": m.get("icir_annual"), "ic_mean": m.get("ic_mean"),
                       "ls_sharpe": m.get("ls_sharpe"), "rank_ic_mean": m.get("rank_ic_mean")})
    return {"item_id": item_id, "series": series}


# ─── small label helpers ─────────────────────────────────────────────────────
_MODE = {"full": "全量", "incremental": "增量", "repair": "修复", "corr_only": "仅相关性"}
_TRIGGER = {"manual": "手动", "tracking": "自动追踪", "config_change": "配置变更",
            "data_refingerprint": "数据修订"}
_RUN_STATUS = {"queued": "排队", "running": "运行中", "success": "成功", "degraded": "降级",
               "partial": "部分成功", "failed": "失败", "canceled": "已取消"}


def _mode_label(m): return _MODE.get(m, m)
def _trigger_label(t): return _TRIGGER.get(t, t)
def _run_status_label(s): return _RUN_STATUS.get(s, s)


def _dur(sec):
    if sec is None:
        return None
    sec = int(sec)
    return f"{sec // 60}m {sec % 60:02d}s"


def _eta(t: Task):
    if t is None:
        return None
    if t.status == "running":
        return f"~{int(t.est_duration_sec)}s"
    return f"~{int(t.est_duration_sec)}s"
