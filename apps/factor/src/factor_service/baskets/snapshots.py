"""Build the immutable result切面 (basket_snapshot) at a run's terminal (§6.3.4).

Called by the basket_corr executor (with the matrix) or by the orchestrator when
there is no correlation. Assembles item_summaries, basket_stats, excluded_items.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import uuid
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import (
    Basket,
    BasketItemState,
    BasketRevision,
    BasketRun,
    BasketRunItem,
    BasketSnapshot,
    FactorRun,
)
from . import ledger

SUMMARY_KEYS = (
    "ic_mean", "rank_ic_mean", "icir", "icir_annual", "ic_t_stat", "ic_win_rate",
    "ls_annual_return", "ls_sharpe", "ls_max_drawdown", "long_annual_return",
    "short_annual_return", "turnover", "turnover_after_cost_ann", "coverage",
)


def _revision_items(db: Session, basket_id: str, revision: int) -> dict[str, dict]:
    rev = (
        db.query(BasketRevision)
        .filter(BasketRevision.basket_id == basket_id, BasketRevision.revision == revision)
        .first()
    )
    out: dict[str, dict] = {}
    if rev:
        for it in rev.items or []:
            out[it.get("item_id")] = it
    return out


def _latest_full_summaries(
    db: Session, brun: BasketRun, item_defs: dict[str, dict]
) -> dict[str, dict]:
    """部分重算(repair)回填用:取同 basket/revision 最近一份覆盖整篮的快照的
    item_summaries(仅保留仍在 revision 内的 item)。无可用快照则返回空 dict。

    非 repair(full/incremental/corr_only)不回填:full 本就覆盖整集,回填反而会
    把全量重建意图丢弃的旧 item 复活。
    """
    if brun.mode != "repair":
        return {}
    full_ids = set(item_defs.keys())
    if not full_ids:
        return {}
    prior = (
        db.query(BasketSnapshot)
        .filter(BasketSnapshot.basket_id == brun.basket_id,
                BasketSnapshot.revision == brun.revision,
                BasketSnapshot.brun_id != brun.brun_id)
        .order_by(BasketSnapshot.created_at.desc())
        .all()
    )
    for snap in prior:
        summ = snap.item_summaries or {}
        # 「完整」= 覆盖该 revision 当前全部 item。
        if full_ids.issubset(set(summ.keys())):
            return {iid: dict(row) for iid, row in summ.items() if iid in full_ids}
    return {}


def build_snapshot(
    db: Session, brun: BasketRun, *, corr_json: dict | None = None
) -> str:
    """Assemble + persist a snapshot for ``brun``; return snapshot_id."""
    s = get_settings()
    basket = db.get(Basket, brun.basket_id)
    item_defs = _revision_items(db, brun.basket_id, brun.revision)
    bris = (
        db.query(BasketRunItem)
        .filter(BasketRunItem.brun_id == brun.brun_id)
        .order_by(BasketRunItem.seq_no)
        .all()
    )

    item_summaries: dict[str, dict] = {}
    as_of: dt.date | None = None
    min_periods = basket.corr_min_periods if basket else 12

    for bri in bris:
        run = db.get(FactorRun, bri.run_id) if bri.run_id else None
        st = db.get(BasketItemState, bri.item_key)
        summ = dict((run.summary if run else None) or {})
        status = (run.status if run else bri.status) or "failed"
        deg_level = getattr(run, "degradation_level", "none") if run else "none"
        settled_n = 0
        pending_n = 0
        if st:
            pending_n = len(st.pending_dates or [])
            # settled_n is rows_ic settled approximation
            settled_n = int(st.rows_ic or 0)
            if st.last_settled_date and (as_of is None or st.last_settled_date > as_of):
                as_of = st.last_settled_date

        row = {k: summ.get(k) for k in SUMMARY_KEYS}
        row.update({
            "monotonic": bool(summ.get("monotonic", False)),
            "settled_n": settled_n,
            "pending_n": pending_n,
            "degradation_level": deg_level,
            "status": status,
            "run_id": bri.run_id,
        })
        item_summaries[bri.item_id] = row

    # repair 等部分重算只为重算子集创建 BasketRunItem,build_snapshot 因此只看到子集。
    # 用同 basket/revision 的上一份完整快照回填未重算 item,本次重算结果覆盖其上,
    # 否则部分重算产出的「最新快照」会丢掉全部原本成功的 item(优选/筛选台账被截断)。
    prev = _latest_full_summaries(db, brun, item_defs)
    if prev:
        merged = dict(prev)
        merged.update(item_summaries)  # 本次重算结果优先
        item_summaries = merged

    # 聚合(excluded / 计数 / 分位)在合并后的整篮 item 集上计算,而非仅重算子集。
    excluded: list[dict] = []
    abs_ics: list[float] = []
    icirs: list[float] = []
    ok_cnt = deg_cnt = fail_cnt = 0
    for item_id, row in item_summaries.items():
        idef = item_defs.get(item_id, {})
        status = row.get("status") or "failed"
        deg_level = row.get("degradation_level", "none")
        settled_n = int(row.get("settled_n") or 0)

        if status == "success":
            ok_cnt += 1
        elif status == "degraded":
            ok_cnt += 1
            deg_cnt += 1
        elif status == "failed":
            fail_cnt += 1

        # excluded-from-correlation rules (§8.3)
        if status == "failed":
            excluded.append({"item_id": item_id, "label": idef.get("label"),
                             "reason": "failed"})
        elif deg_level == "severe" and not (basket and basket.acknowledge_degraded):
            excluded.append({"item_id": item_id, "label": idef.get("label"),
                             "reason": "severe_degraded"})
        elif settled_n < min_periods:
            excluded.append({"item_id": item_id, "label": idef.get("label"),
                             "reason": "insufficient_settled", "settled_n": settled_n})

        if status in ("success", "degraded"):
            ric = row.get("rank_ic_mean")
            if ric is not None:
                abs_ics.append(abs(float(ric)))
            ic = row.get("icir_annual")
            if ic is not None:
                icirs.append(float(icir_safe(ic)))

    total = len(item_summaries) or 1
    # redundancy = share of |ρ|>θ pairs among computed pairs
    redundancy = 0.0
    if corr_json:
        pairs = corr_json.get("high_pairs") or []
        labels = corr_json.get("order") or []
        n = len(labels)
        denom = n * (n - 1) / 2 if n > 1 else 1
        redundancy = round(len(pairs) / denom, 4) if denom else 0.0

    basket_stats = {
        "success_rate": round(ok_cnt / total, 4),
        "ok_count": ok_cnt,
        "degraded_count": deg_cnt,
        "failed_count": fail_cnt,
        "total": len(item_summaries),
        "abs_ic_p50": _pct(abs_ics, 50),
        "abs_ic_p90": _pct(abs_ics, 90),
        "icir_p50": _pct(icirs, 50),
        "icir_p90": _pct(icirs, 90),
        "avg_abs_ic": round(float(np.mean(abs_ics)), 4) if abs_ics else None,
        "avg_icir": round(float(np.mean(icirs)), 4) if icirs else None,
        "redundancy": redundancy,
        "excluded_count": len(excluded),
        "duration_sec": brun.duration_sec,
    }

    if as_of is None:
        as_of = brun.as_of or brun.resolved_end

    snap_id = f"snap_{uuid.uuid4().hex[:16]}"
    # artifacts
    corr_path = None
    try:
        sdir = ledger.snapshot_dir(s.factor_store_root, brun.basket_id, brun.revision,
                                   as_of.isoformat() if as_of else "unknown")
        os.makedirs(sdir, exist_ok=True)
        if corr_json:
            corr_path = os.path.join(sdir, "correlation.json")
            with open(corr_path, "w", encoding="utf-8") as f:
                json.dump(corr_json, f, ensure_ascii=False)
        with open(os.path.join(sdir, "basket_stats.json"), "w", encoding="utf-8") as f:
            json.dump(basket_stats, f, ensure_ascii=False)
    except Exception:  # noqa: BLE001 — artifact write is best-effort
        pass

    snap = BasketSnapshot(
        snapshot_id=snap_id,
        basket_id=brun.basket_id,
        revision=brun.revision,
        brun_id=brun.brun_id,
        as_of=as_of,
        resolved_start=brun.resolved_start,
        resolved_end=brun.resolved_end,
        item_summaries=item_summaries,
        corr_artifact=corr_path,
        corr_json=corr_json or {},
        basket_stats=basket_stats,
        excluded_items=excluded,
    )
    db.add(snap)
    brun.as_of = as_of
    db.commit()
    return snap_id


def icir_safe(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _pct(vals: list[float], p: int) -> float | None:
    if not vals:
        return None
    return round(float(np.percentile(vals, p)), 4)
