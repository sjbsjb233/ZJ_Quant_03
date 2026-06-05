"""因子优选工作台 + 入选集合 API — 严格单篮子单快照 (因子层重构 §11/§12)."""
from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from quant_data import logging as qd_logging

from ..baskets import keys, scoring
from ..baskets.correlation import flat_matrix
from ..db.models import (
    Basket,
    BasketRevision,
    BasketSnapshot,
    FactorRecord,
    FactorSelection,
    FactorSelectionMember,
    ScreenPreset,
)
from ..deps import current_user, get_db, require_editor
from ..errors import ApiError

router = APIRouter()
_qlog = qd_logging.get_logger("factor.screening")


def _iso(d):
    if d is None:
        return None
    if isinstance(d, dt.datetime):
        return d.isoformat() + "Z"
    if isinstance(d, dt.date):
        return d.isoformat()
    return str(d)


# ═════════════════════════════════════════════════════════════════════════════
@router.get("/screening/baskets")
def screening_baskets(db: Session = Depends(get_db), user=Depends(current_user)) -> dict:
    """Basket + snapshot picker (§11.2) — calibration摘要, snapshot list, stale."""
    baskets = db.query(Basket).filter(Basket.status == "active").all()
    out = []
    for b in baskets:
        rev = (
            db.query(BasketRevision)
            .filter(BasketRevision.basket_id == b.basket_id,
                    BasketRevision.revision == b.active_revision)
            .first()
        )
        snaps = (
            db.query(BasketSnapshot)
            .filter(BasketSnapshot.basket_id == b.basket_id)
            .order_by(BasketSnapshot.as_of.desc())
            .all()
        )
        if not snaps:
            continue
        out.append({
            "basket_id": b.basket_id, "name": b.name,
            "calibration": keys.calibration_summary(rev.shared_config) if rev else {},
            "entry_count": len(rev.items) if rev else 0,
            "stale": b.stale_scope != "none",
            "snapshots": [{"snapshot_id": s.snapshot_id, "asOf": _iso(s.as_of),
                           "rev": s.revision} for s in snaps],
            "latest_snapshot_id": snaps[0].snapshot_id,
        })
    return {"items": out}


# ─────────────────────────────────────────────────────────────────────────────
def _get_ctx(db: Session, basket_id: str, sid: str):
    basket = db.get(Basket, basket_id)
    if basket is None:
        raise ApiError("BASKET_NOT_FOUND", f"篮子 {basket_id} 不存在")
    snap = db.get(BasketSnapshot, sid)
    if snap is None or snap.basket_id != basket_id:
        raise ApiError("SNAPSHOT_NOT_FOUND", f"快照 {sid} 不存在")
    rev = (
        db.query(BasketRevision)
        .filter(BasketRevision.basket_id == basket_id, BasketRevision.revision == snap.revision)
        .first()
    )
    return basket, snap, rev


def _base_rows(db: Session, basket: Basket, snap: BasketSnapshot, rev: BasketRevision,
               include_degraded: str) -> tuple[list[dict], dict]:
    """Build screening rows from item_summaries + revision meta + factor meta.
    Returns (rows, excluded_counts). Applies行级排除策略 (§11.2)."""
    item_defs = {it["item_id"]: it for it in (rev.items if rev else [])}
    fac_cache: dict[str, FactorRecord] = {}
    rows = []
    excluded_counts = {"failed": 0, "severe": 0, "metric_missing": 0}
    min_periods = basket.corr_min_periods or 12

    for item_id, summ in (snap.item_summaries or {}).items():
        idef = item_defs.get(item_id, {})
        fid = idef.get("factor_id") or summ.get("factor_id")
        if fid and fid not in fac_cache:
            fac_cache[fid] = db.get(FactorRecord, fid)
        rec = fac_cache.get(fid)
        status = summ.get("status", "failed")
        deg = summ.get("degradation_level", "none")
        settled_n = summ.get("settled_n") or 0
        coverage = summ.get("coverage")
        metric_missing = (summ.get("rank_ic_mean") is None and (coverage in (0, None)))

        # hard exclusions
        if status == "failed":
            excluded_counts["failed"] += 1
            continue
        if deg == "severe":
            if include_degraded != "all":
                excluded_counts["severe"] += 1
                continue
        if include_degraded == "none" and deg in ("partial", "severe"):
            continue

        if metric_missing:
            excluded_counts["metric_missing"] += 1

        row = {
            "item_key": idef.get("item_key"), "item_id": item_id, "factor_id": fid,
            "factor_name": (rec.display_name or rec.base_name) if rec else fid,
            "label": idef.get("label") or item_id, "params": idef.get("params") or {},
            "category": rec.category if rec else None,
            "tags": rec.tags if rec else [],
            "monotonic": bool(summ.get("monotonic")),
            "settled_n": settled_n, "degrade": deg, "status": status,
            "metric_missing": metric_missing,
            "corr_insufficient": settled_n < min_periods,
            "metrics": {k: summ.get(k) for k in scoring.METRIC_WHITELIST if k in summ},
        }
        rows.append(row)
    return rows, excluded_counts


def _resolve_weights(db: Session, weights_param: str | None) -> dict:
    if not weights_param:
        return dict(scoring.WEIGHT_TEMPLATES[scoring.DEFAULT_TEMPLATE])
    if weights_param.startswith("preset:"):
        pid = weights_param.split(":", 1)[1]
        p = db.get(ScreenPreset, pid)
        if p is None:
            raise ApiError("PRESET_NOT_FOUND", f"方案 {pid} 不存在")
        return dict(p.weights_json or scoring.WEIGHT_TEMPLATES[scoring.DEFAULT_TEMPLATE])
    try:
        return json.loads(weights_param)
    except Exception:
        raise ApiError("INVALID_PARAMETER", "weights 非法 JSON")


def _parse_json_param(s: str | None, name: str):
    if not s:
        return None
    try:
        return json.loads(s)
    except Exception:
        raise ApiError("INVALID_PARAMETER", f"{name} 非法 JSON")


@router.get("/baskets/{basket_id}/snapshots/{sid}/grid")
def grid(basket_id: str, sid: str,
         filters: str | None = None, weights: str | None = None,
         collapse: str = "best", include_degraded: str = "partial",
         sort: str = "score_desc", page: int = 1, page_size: int = 100,
         db: Session = Depends(get_db), user=Depends(current_user)) -> dict:
    basket, snap, rev = _get_ctx(db, basket_id, sid)
    rows, excluded_counts = _base_rows(db, basket, snap, rev, include_degraded)

    # score over the whole cohort (§11.4 顺序约定: standardize before filter)
    w = _resolve_weights(db, weights)
    scoring.score_rows(rows, w)

    # filter (hide only)
    try:
        filtered = scoring.apply_filters(rows, _parse_json_param(filters, "filters"))
    except ValueError as e:
        raise ApiError("INVALID_PARAMETER", str(e))

    # collapse
    collapsed = scoring.collapse_rows(filtered, collapse)

    # sort
    if sort and sort != "score_desc":
        key = sort.rsplit("_", 1)[0]
        desc = sort.endswith("_desc")
        if key in scoring.METRIC_WHITELIST:
            collapsed.sort(key=lambda r: ((r.get("metrics") or {}).get(key) or 0.0), reverse=desc)

    total = len(collapsed)
    start = (page - 1) * page_size
    page_rows = collapsed[start:start + page_size]

    return {
        "rows": page_rows, "total": total, "page": page, "page_size": page_size,
        "excluded": excluded_counts,
        "weights": w,
        "correlation": snap.corr_json or {},
        "calibration": keys.calibration_summary(rev.shared_config) if rev else {},
        "stale": basket.stale_scope != "none",
    }


@router.post("/baskets/{basket_id}/snapshots/{sid}/prune")
def prune(basket_id: str, sid: str, body: dict[str, Any], db: Session = Depends(get_db),
          user=Depends(current_user)) -> dict:
    basket, snap, rev = _get_ctx(db, basket_id, sid)
    rows, _ = _base_rows(db, basket, snap, rev, body.get("include_degraded", "partial"))
    scoring.score_rows(rows, _resolve_weights(db, body.get("weights")))
    by_key = {r["item_key"]: r for r in rows}

    cand_keys = body.get("candidates")
    if cand_keys:
        for k in cand_keys:
            if k not in by_key:
                raise ApiError("SNAPSHOT_MISMATCH", f"候选 {k} 不属于该快照")
        candidates = [by_key[k] for k in cand_keys if k in by_key]
    else:
        candidates = [r for r in rows if not r["corr_insufficient"]]

    matrix = flat_matrix(snap.corr_json or {})
    try:
        res = scoring.greedy_prune(
            candidates, matrix,
            threshold=float(body.get("threshold", basket.correlation_threshold)),
            max_n=body.get("max_n"),
            forced=set(body.get("forced") or []),
            order_mode=body.get("order_mode", "score"),
            manual_order=body.get("manual_order"),
        )
    except ValueError as e:
        raise ApiError("INVALID_PARAMETER", str(e))
    return {"kept": _slim(res["kept"]), "pruned": _slim(res["pruned"]),
            "threshold": float(body.get("threshold", basket.correlation_threshold))}


def _slim(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        out.append({
            "item_key": r["item_key"], "item_id": r.get("item_id"),
            "factor_id": r.get("factor_id"), "label": r.get("label"),
            "score": r.get("score"), "rank": r.get("rank"),
            "decision": r.get("decision"), "blocked_by": r.get("blocked_by"),
            "rho": r.get("rho"), "metrics": r.get("metrics"),
            "score_detail": r.get("score_detail"),
        })
    return out


# ═════════════════════════════════════════════════════════════════════════════
# Selections (§11.7)
@router.post("/selections")
def create_selection(body: dict[str, Any], db: Session = Depends(get_db),
                     user=Depends(require_editor)) -> dict:
    basket_id = body.get("basket_id")
    sid = body.get("snapshot_id")
    if not (basket_id and sid):
        raise ApiError("INVALID_PARAMETER", "basket_id 与 snapshot_id 必填")
    basket, snap, rev = _get_ctx(db, basket_id, sid)
    criteria = body.get("criteria") or {}

    rows, _ = _base_rows(db, basket, snap, rev, criteria.get("include_degraded", "partial"))
    scoring.score_rows(rows, _resolve_weights_from_criteria(db, criteria))
    by_key = {r["item_key"]: r for r in rows}
    # candidates default = filtered + collapsed + sufficient
    try:
        filtered = scoring.apply_filters(rows, criteria.get("filters"))
    except ValueError as e:
        raise ApiError("INVALID_PARAMETER", str(e))
    collapsed = scoring.collapse_rows(filtered, criteria.get("collapse", "best"))
    candidates = [r for r in collapsed if not r["corr_insufficient"]]

    forced = set(criteria.get("forced") or [])
    matrix = flat_matrix(snap.corr_json or {})
    try:
        res = scoring.greedy_prune(
            candidates, matrix,
            threshold=float(criteria.get("threshold", basket.correlation_threshold)),
            max_n=criteria.get("max_n"), forced=forced,
            order_mode=criteria.get("order_mode", "score"),
            manual_order=criteria.get("manual_order"),
        )
    except ValueError as e:
        raise ApiError("INVALID_PARAMETER", str(e))

    members = res["kept"] + res["pruned"]
    # severe ack gate (§11.7)
    ack = bool(criteria.get("acknowledge_degraded"))
    severe = [m for m in members if by_key.get(m["item_key"], {}).get("degrade") == "severe"]
    if severe and not ack:
        raise ApiError("DEGRADED_INPUT_BLOCKED",
                       "存在严重降级成员,需 acknowledge_degraded 后方可创建")

    sel_id = f"sel_{uuid.uuid4().hex[:16]}"
    sel = FactorSelection(
        selection_id=sel_id, name=body.get("name") or "未命名集合",
        basket_id=basket_id, snapshot_id=sid, status="draft",
        criteria_json=criteria, corr_method=(snap.corr_json or {}).get("method", "pearson"),
        corr_threshold=float(criteria.get("threshold", basket.correlation_threshold)),
        corr_matrix_json=snap.corr_json or {}, acknowledge_degraded=ack,
        owner=user.username,
    )
    db.add(sel)
    for m in members:
        src = by_key.get(m["item_key"], {})
        db.add(FactorSelectionMember(
            selection_id=sel_id, rank=m.get("rank") or 0, decision=m["decision"],
            item_key=m["item_key"], item_id=src.get("item_id") or "",
            factor_id=src.get("factor_id") or "",
            params_digest=keys.params_digest(src.get("params")),
            score=m.get("score") or 0.0, metrics_json=src.get("metrics") or {},
            blocked_by=m.get("blocked_by"), rho=m.get("rho"),
        ))
    db.commit()
    _qlog.info("selection.create", sel_id=sel_id, basket_id=basket_id,
               n_kept=len(res["kept"]), n_pruned=len(res["pruned"]))
    return _serialize_selection(db, sel)


def _resolve_weights_from_criteria(db, criteria: dict) -> dict:
    w = criteria.get("weights")
    if isinstance(w, str):
        return _resolve_weights(db, w)
    if isinstance(w, dict) and w:
        return w
    return dict(scoring.WEIGHT_TEMPLATES[scoring.DEFAULT_TEMPLATE])


@router.get("/selections")
def list_selections(basket_id: str | None = None, status: str | None = None,
                    db: Session = Depends(get_db), user=Depends(current_user)) -> dict:
    q = db.query(FactorSelection)
    if basket_id:
        q = q.filter(FactorSelection.basket_id == basket_id)
    if status:
        q = q.filter(FactorSelection.status == status)
    rows = q.order_by(FactorSelection.created_at.desc()).all()
    return {"items": [_serialize_selection(db, s, brief=True) for s in rows]}


@router.get("/selections/{sid}")
def get_selection(sid: str, db: Session = Depends(get_db),
                  user=Depends(current_user)) -> dict:
    sel = db.get(FactorSelection, sid)
    if sel is None:
        raise ApiError("SELECTION_NOT_FOUND", f"集合 {sid} 不存在")
    return _serialize_selection(db, sel)


@router.patch("/selections/{sid}")
def patch_selection(sid: str, body: dict[str, Any], db: Session = Depends(get_db),
                    user=Depends(require_editor)) -> dict:
    sel = _editable(db, sid)
    if "name" in body:
        sel.name = body["name"]
    db.commit()
    return _serialize_selection(db, sel)


@router.post("/selections/{sid}/finalize")
def finalize_selection(sid: str, db: Session = Depends(get_db),
                       user=Depends(require_editor)) -> dict:
    sel = _editable(db, sid)
    sel.status = "final"
    sel.finalized_at = dt.datetime.utcnow()
    db.commit()
    return _serialize_selection(db, sel)


@router.post("/selections/{sid}/archive")
def archive_selection(sid: str, db: Session = Depends(get_db),
                      user=Depends(require_editor)) -> dict:
    sel = db.get(FactorSelection, sid)
    if sel is None:
        raise ApiError("SELECTION_NOT_FOUND", f"集合 {sid} 不存在")
    if sel.status != "final":
        raise ApiError("INVALID_PARAMETER", "仅已定稿集合可归档")
    sel.status = "archived"
    db.commit()
    return _serialize_selection(db, sel)


@router.delete("/selections/{sid}")
def delete_selection(sid: str, db: Session = Depends(get_db),
                     user=Depends(require_editor)) -> dict:
    sel = _editable(db, sid)
    db.query(FactorSelectionMember).filter(
        FactorSelectionMember.selection_id == sid).delete()
    db.delete(sel)
    db.commit()
    return {"selection_id": sid, "deleted": True}


@router.get("/selections/{sid}/export")
def export_selection(sid: str, format: str = "json", db: Session = Depends(get_db),
                     user=Depends(current_user)) -> dict:
    sel = db.get(FactorSelection, sid)
    if sel is None:
        raise ApiError("SELECTION_NOT_FOUND", f"集合 {sid} 不存在")
    snap = db.get(BasketSnapshot, sel.snapshot_id)
    rev = (
        db.query(BasketRevision)
        .filter(BasketRevision.basket_id == sel.basket_id,
                BasketRevision.revision == (snap.revision if snap else 1))
        .first()
    )
    members = (
        db.query(FactorSelectionMember)
        .filter(FactorSelectionMember.selection_id == sid,
                FactorSelectionMember.decision.in_(["kept", "kept_manual"]))
        .order_by(FactorSelectionMember.rank)
        .all()
    )
    item_defs = {it["item_id"]: it for it in (rev.items if rev else [])}
    payload = {
        "selection_id": sid, "finalized_at": _iso(sel.finalized_at),
        "source": {
            "basket_id": sel.basket_id, "revision": snap.revision if snap else None,
            "snapshot_id": sel.snapshot_id, "as_of": _iso(snap.as_of) if snap else None,
            "window": keys.window_descriptor((rev.shared_config or {}).get("window")) if rev else None,
            "shared_config": rev.shared_config if rev else {},
        },
        "members": [{
            "item_key": m.item_key, "factor_id": m.factor_id,
            "version": (item_defs.get(m.item_id) or {}).get("version"),
            "params": (item_defs.get(m.item_id) or {}).get("params"),
            "rank": m.rank, "decision": m.decision, "score": m.score,
            "metrics": m.metrics_json,
        } for m in members],
    }
    if format == "csv":
        return Response(content=_selection_csv(sel, payload),
                        media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{sid}.csv"'})
    return payload


def _csv_cell(v) -> str:
    """RFC4180 escape + CSV-injection neutralization (API-SCR-09)."""
    s = "" if v is None else str(v)
    if s and s[0] in ("=", "+", "-", "@", "\t", "\r"):
        s = "'" + s                      # neutralize spreadsheet formula
    if any(ch in s for ch in (",", '"', "\n", "\r")):
        s = '"' + s.replace('"', '""') + '"'
    return s


def _selection_csv(sel: FactorSelection, payload: dict) -> str:
    import json as _json
    src = payload["source"]
    lines = [
        f"# selection,{_csv_cell(sel.name)}",
        f"# basket,{_csv_cell(src['basket_id'])},snapshot,{_csv_cell(src['snapshot_id'])},"
        f"as_of,{_csv_cell(src['as_of'])},window,{_csv_cell(src['window'])}",
    ]
    metric_keys = ["icir_annual", "rank_ic_mean", "ic_win_rate", "ls_sharpe",
                   "ls_max_drawdown", "turnover", "coverage"]
    header = ["rank", "item_key", "factor_id", "version", "params", "score"] + metric_keys
    lines.append(",".join(_csv_cell(h) for h in header))
    for m in payload["members"]:
        metrics = m.get("metrics") or {}
        row = [m["rank"], m["item_key"], m["factor_id"], m["version"],
               _json.dumps(m["params"], ensure_ascii=False), m["score"]]
        row += [metrics.get(k) for k in metric_keys]
        lines.append(",".join(_csv_cell(c) for c in row))
    return "\n".join(lines) + "\n"


def _editable(db: Session, sid: str) -> FactorSelection:
    sel = db.get(FactorSelection, sid)
    if sel is None:
        raise ApiError("SELECTION_NOT_FOUND", f"集合 {sid} 不存在")
    if sel.status != "draft":
        raise ApiError("SELECTION_FINALIZED", "集合已定稿,不可修改")
    return sel


def _serialize_selection(db: Session, sel: FactorSelection, *, brief: bool = False) -> dict:
    basket = db.get(Basket, sel.basket_id)
    snap = db.get(BasketSnapshot, sel.snapshot_id)
    latest = (
        db.query(BasketSnapshot)
        .filter(BasketSnapshot.basket_id == sel.basket_id)
        .order_by(BasketSnapshot.as_of.desc())
        .first()
    )
    stale = bool(latest and latest.snapshot_id != sel.snapshot_id)
    out = {
        "selection_id": sel.selection_id, "id": sel.selection_id, "name": sel.name,
        "basket_id": sel.basket_id, "basketName": basket.name if basket else sel.basket_id,
        "snapshot_id": sel.snapshot_id, "asOf": _iso(snap.as_of) if snap else None,
        "rev": snap.revision if snap else None, "status": _sel_status(sel.status),
        "status_raw": sel.status, "stale": stale, "creator": sel.owner,
        "created": _iso(sel.created_at), "finalized_at": _iso(sel.finalized_at),
        "threshold": sel.corr_threshold, "corr_method": sel.corr_method,
        "criteria": sel.criteria_json,
    }
    if brief:
        out["member_count"] = db.query(FactorSelectionMember).filter(
            FactorSelectionMember.selection_id == sel.selection_id).count()
        return out
    members = (
        db.query(FactorSelectionMember)
        .filter(FactorSelectionMember.selection_id == sel.selection_id)
        .order_by(FactorSelectionMember.rank)
        .all()
    )
    kept = [m for m in members if m.decision in ("kept", "kept_manual")]
    pruned = [m for m in members if m.decision in ("pruned_corr", "pruned_capped")]
    out["members"] = [{"rank": m.rank, "entryId": m.item_id, "item_key": m.item_key,
                       "label": _member_label(db, sel, m), "score": m.score,
                       "decision": _decision_label(m.decision), "decision_raw": m.decision,
                       "metrics": m.metrics_json} for m in kept]
    out["excluded"] = [{"label": _member_label(db, sel, m),
                        "reason": _prune_reason(m), "by": m.blocked_by, "rho": m.rho}
                       for m in pruned]
    out["corr_matrix"] = sel.corr_matrix_json
    return out


def _member_label(db, sel, m) -> str:
    snap = db.get(BasketSnapshot, sel.snapshot_id)
    rev = (
        db.query(BasketRevision)
        .filter(BasketRevision.basket_id == sel.basket_id,
                BasketRevision.revision == (snap.revision if snap else 1))
        .first()
    )
    for it in (rev.items if rev else []):
        if it["item_id"] == m.item_id:
            return it.get("label") or m.item_id
    return m.item_id


def _prune_reason(m) -> str:
    if m.decision == "pruned_corr":
        return f"与挡掉它的条目相关 {m.rho}，超过阈值"
    return "超出保留上限"


# ─── presets (§11.9) ─────────────────────────────────────────────────────────
@router.get("/screen-presets")
def list_presets(db: Session = Depends(get_db), user=Depends(current_user)) -> dict:
    rows = db.query(ScreenPreset).order_by(ScreenPreset.builtin.desc(),
                                           ScreenPreset.created_at).all()
    return {"items": [_serialize_preset(p) for p in rows]}


@router.post("/screen-presets")
def create_preset(body: dict[str, Any], db: Session = Depends(get_db),
                  user=Depends(require_editor)) -> dict:
    pid = f"spre_{uuid.uuid4().hex[:12]}"
    p = ScreenPreset(preset_id=pid, name=body.get("name") or "未命名方案",
                     owner=user.username, filters_json=body.get("filters") or {},
                     weights_json=body.get("weights") or {}, builtin=False)
    db.add(p)
    db.commit()
    return _serialize_preset(p)


@router.patch("/screen-presets/{pid}")
def patch_preset(pid: str, body: dict[str, Any], db: Session = Depends(get_db),
                 user=Depends(require_editor)) -> dict:
    p = db.get(ScreenPreset, pid)
    if p is None:
        raise ApiError("PRESET_NOT_FOUND", f"方案 {pid} 不存在")
    if p.builtin:
        raise ApiError("PERMISSION_DENIED", "内置方案不可修改")
    if "name" in body:
        p.name = body["name"]
    if "filters" in body:
        p.filters_json = body["filters"]
    if "weights" in body:
        p.weights_json = body["weights"]
    db.commit()
    return _serialize_preset(p)


@router.delete("/screen-presets/{pid}")
def delete_preset(pid: str, db: Session = Depends(get_db),
                  user=Depends(require_editor)) -> dict:
    p = db.get(ScreenPreset, pid)
    if p is None:
        raise ApiError("PRESET_NOT_FOUND", f"方案 {pid} 不存在")
    if p.builtin:
        raise ApiError("PERMISSION_DENIED", "内置方案不可删除")
    db.delete(p)
    db.commit()
    return {"preset_id": pid, "deleted": True}


def _serialize_preset(p: ScreenPreset) -> dict:
    return {"preset_id": p.preset_id, "id": p.preset_id, "name": p.name,
            "builtin": p.builtin, "filters": p.filters_json, "weights": p.weights_json,
            "owner": p.owner}


# ─── robustness (§11.8) — the ONLY legal cross-basket view, read-only ─────────
@router.get("/factors/{factor_id}/robustness")
def robustness(factor_id: str, basket_id: str | None = None,
               db: Session = Depends(get_db), user=Depends(current_user)) -> dict:
    ref_shared = None
    if basket_id:
        b = db.get(Basket, basket_id)
        if b:
            rev = (
                db.query(BasketRevision)
                .filter(BasketRevision.basket_id == basket_id,
                        BasketRevision.revision == b.active_revision)
                .first()
            )
            ref_shared = keys.normalize_shared_config(rev.shared_config) if rev else None

    baskets = db.query(Basket).filter(Basket.status == "active").all()
    out_rows = []
    for b in baskets:
        snap = (
            db.query(BasketSnapshot)
            .filter(BasketSnapshot.basket_id == b.basket_id)
            .order_by(BasketSnapshot.as_of.desc())
            .first()
        )
        if snap is None:
            continue
        rev = (
            db.query(BasketRevision)
            .filter(BasketRevision.basket_id == b.basket_id,
                    BasketRevision.revision == snap.revision)
            .first()
        )
        item_defs = {it["item_id"]: it for it in (rev.items if rev else [])}
        cur_shared = keys.normalize_shared_config(rev.shared_config) if rev else {}
        for item_id, summ in (snap.item_summaries or {}).items():
            idef = item_defs.get(item_id, {})
            if idef.get("factor_id") != factor_id:
                continue
            out_rows.append({
                "basket_id": b.basket_id, "basket_name": b.name,
                "label": idef.get("label"), "asOf": _iso(snap.as_of),
                "params": idef.get("params"),
                "metrics": {k: summ.get(k) for k in
                            ("icir_annual", "rank_ic_mean", "ic_win_rate", "ls_sharpe",
                             "ls_max_drawdown", "turnover", "coverage")},
                "diff": _shared_diff(ref_shared, cur_shared) if ref_shared else [],
            })
    return {"factor_id": factor_id, "reference_basket": basket_id, "rows": out_rows,
            "readonly": True}


def _shared_diff(ref: dict, cur: dict) -> list[dict]:
    out = []
    for k in sorted(set(ref) | set(cur)):
        if ref.get(k) != cur.get(k):
            out.append({"field": k, "ref": ref.get(k), "cur": cur.get(k),
                        "level": "hard" if k in
                        {"universe_id", "window", "rebalance", "primary_horizon",
                         "return_price", "ic_type"} else "soft"})
    return out


_SEL_STATUS = {"draft": "草稿", "final": "已定稿", "archived": "已归档"}
_DECISION = {"kept": "保留", "kept_manual": "手动保留",
             "pruned_corr": "因相关剔除", "pruned_capped": "超上限剔除"}


def _sel_status(s): return _SEL_STATUS.get(s, s)
def _decision_label(d): return _DECISION.get(d, d)
