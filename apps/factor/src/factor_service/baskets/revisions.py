"""Revision lifecycle + 失效矩阵 (§9.1 / §9.2).

Saving a config change produces a new ``basket_revision`` with a structured diff
and an ``invalidation`` verdict, and flips the basket's ``stale_scope`` (pausing
tracking so a tick never grows the old ledger).
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from sqlalchemy.orm import Session

from ..db.models import Basket, BasketRevision
from . import keys

# Hard (考卷) vs soft (答题方式) dimensions for the diff color-coding (§11.1).
HARD_FIELDS = {
    "universe_id", "window", "rebalance", "primary_horizon", "return_price", "ic_type",
}


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


def normalize_items(items: list[dict], common: str, *, default_labels: dict | None = None) -> list[dict]:
    """Assign item_id/sort/item_key; fill missing labels."""
    out = []
    default_labels = default_labels or {}
    for i, it in enumerate(items):
        iid = it.get("item_id") or f"bit_{uuid.uuid4().hex[:12]}"
        version = it.get("version") or ""
        params = it.get("params") or {}
        label = it.get("label") or default_labels.get(it["factor_id"]) or it["factor_id"]
        sort = it.get("sort")
        out.append({
            "item_id": iid,
            "factor_id": it["factor_id"],
            "version": version,
            "params": params,
            "label": label,
            "sort": sort if sort is not None else i,
            "item_key": keys.item_key(common, it["factor_id"], version, params),
        })
    return out


def compute_diff(prev: BasketRevision | None, new_shared: dict, new_items: list[dict]) -> dict:
    """Structured diff vs the previous revision (§9.1 change_summary)."""
    shared_changed = []
    if prev:
        old_shared = keys.normalize_shared_config(prev.shared_config)
        cur_shared = keys.normalize_shared_config(new_shared)
        all_keys = set(old_shared) | set(cur_shared)
        for k in sorted(all_keys):
            if old_shared.get(k) != cur_shared.get(k):
                shared_changed.append({
                    "field": k, "old": old_shared.get(k), "new": cur_shared.get(k),
                    "level": "hard" if k in HARD_FIELDS else "soft",
                })
    old_by_id = {it["item_id"]: it for it in (prev.items if prev else [])}
    new_by_id = {it["item_id"]: it for it in new_items}
    added = [iid for iid in new_by_id if iid not in old_by_id]
    removed = [iid for iid in old_by_id if iid not in new_by_id]
    modified = []
    for iid, it in new_by_id.items():
        if iid in old_by_id and old_by_id[iid].get("item_key") != it.get("item_key"):
            modified.append(iid)
    return {
        "shared_changed": shared_changed,
        "items_added": added,
        "items_removed": removed,
        "items_modified": modified,
    }


def invalidation_from_diff(prev: BasketRevision | None, new_shared: dict, diff: dict) -> tuple[str, list[str]]:
    """失效矩阵 (§9.2). Returns (invalidation, affected_item_ids)."""
    if prev is None:
        return "full", []
    common_changed = keys.common_key(prev.shared_config) != keys.common_key(new_shared)
    if common_changed:
        return "full", []
    affected = sorted(set(diff["items_added"]) | set(diff["items_modified"]))
    if affected:
        return f"items:{','.join(affected)}", affected
    return "none", []


def save_revision(
    db: Session,
    basket: Basket,
    *,
    shared_config: dict,
    items: list[dict],
    reason: str | None,
    author: str,
    default_labels: dict | None = None,
) -> BasketRevision:
    common = keys.common_key(shared_config)
    norm_items = normalize_items(items, common, default_labels=default_labels)
    prev = (
        db.query(BasketRevision)
        .filter(BasketRevision.basket_id == basket.basket_id,
                BasketRevision.revision == basket.active_revision)
        .first()
    )
    diff = compute_diff(prev, shared_config, norm_items)
    invalidation, affected = invalidation_from_diff(prev, shared_config, diff)
    new_no = (prev.revision + 1) if prev else 1
    rev = BasketRevision(
        basket_id=basket.basket_id, revision=new_no,
        shared_config=dict(shared_config),
        items=norm_items,
        common_key=common,
        comparable_key=keys.comparable_key(shared_config),
        change_summary=diff, invalidation=invalidation, reason=reason, author=author,
    )
    db.add(rev)
    basket.active_revision = new_no

    # stale_scope + tracking pause (§9.1)
    if invalidation == "full":
        basket.stale_scope = "full"
        basket.stale_items = []
    elif invalidation.startswith("items:"):
        basket.stale_scope = "partial"
        basket.stale_items = affected
    else:
        basket.stale_scope = "none"
        basket.stale_items = []
    if basket.stale_scope != "none":
        basket.stale_reason = reason or _summarize_reason(diff)
        if basket.tracking_enabled and not basket.tracking_paused_reason:
            basket.tracking_paused_reason = "config_stale"
    db.commit()
    return rev


def _summarize_reason(diff: dict) -> str:
    parts = []
    for c in diff.get("shared_changed", [])[:2]:
        parts.append(f"{c['field']} {c.get('old')}→{c.get('new')}")
    if diff.get("items_added"):
        parts.append(f"+{len(diff['items_added'])} 条目")
    if diff.get("items_modified"):
        parts.append(f"~{len(diff['items_modified'])} 条目")
    return "; ".join(parts) or "配置修订"
