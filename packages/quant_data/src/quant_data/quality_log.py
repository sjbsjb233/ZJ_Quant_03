"""Persistent quality-issue log (doc §7.1).

``ingest`` runs advisory quality checks but historically only stamped a count
into ``batches.notes``. The Web platform needs to *query* historical issues,
filter them, and acknowledge known ones -- so findings are persisted into the
``metadata.quality_issues`` ledger here.

Issues live in the data layer (next to the batch ledger that produced them);
the ``acknowledged`` columns capture the Web user's "known issue" decision.
"""
from __future__ import annotations

import datetime as dt
import json

from . import ids
from .metadata import _common as c
from .schemas import QUALITY_ISSUES


def _next_id(rows: list[dict]) -> int:
    return (max((r["id"] for r in rows), default=0) or 0) + 1


def _naive_utc(value: dt.datetime | None) -> dt.datetime | None:
    """把可能带时区的 datetime 归一成 naive UTC,以匹配 ``ids.utc_now()``
    写入的 naive TIMESTAMP 列;否则 aware/naive 比较会抛 TypeError。"""
    if value is not None and value.tzinfo is not None:
        return value.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return value


@c.locked
def record_issues(batch_id: str, target_table: str, issues, root=None) -> list[dict]:
    """Append a batch's quality findings. ``issues`` are quality.QualityIssue
    instances or plain dicts with check/level/count/message[/sample]."""
    rows = c.load(QUALITY_ISSUES, root)
    now = ids.utc_now()
    out = []
    nid = _next_id(rows)
    for iss in issues:
        if hasattr(iss, "check"):  # quality.QualityIssue dataclass
            check_name, level, count, message = iss.check, iss.level, iss.count, iss.message
            sample = getattr(iss, "sample", None)
        else:
            check_name = iss.get("check") or iss.get("check_name")
            level = iss.get("level", "warn")
            count = iss.get("count", 0)
            message = iss.get("message")
            sample = iss.get("sample")
        row = c.blank(QUALITY_ISSUES)
        row.update(
            id=nid,
            batch_id=batch_id,
            target_table=target_table,
            check_name=check_name,
            level=level,
            count=int(count or 0),
            message=message,
            sample=json.dumps(sample, default=str) if sample is not None else None,
            detected_at=now,
            acknowledged=False,
        )
        rows.append(row)
        out.append(row)
        nid += 1
    c.save(QUALITY_ISSUES, rows, root)
    return out


def list_issues(
    table: str | None = None,
    check_name: str | None = None,
    level=None,
    batch_id: str | None = None,
    acknowledged: bool | None = None,
    start: dt.datetime | None = None,
    end: dt.datetime | None = None,
    root=None,
) -> list[dict]:
    """Filtered issues, most-recently-detected first. ``level`` may be a single
    value or an iterable of values."""
    levels = {level} if isinstance(level, str) else (set(level) if level else None)
    start = _naive_utc(start)
    end = _naive_utc(end)
    rows = c.load(QUALITY_ISSUES, root)
    out = [
        r for r in rows
        if (table is None or r["target_table"] == table)
        and (check_name is None or r["check_name"] == check_name)
        and (levels is None or r["level"] in levels)
        and (batch_id is None or r["batch_id"] == batch_id)
        and (acknowledged is None or bool(r.get("acknowledged")) == acknowledged)
        and (start is None or (r.get("detected_at") and r["detected_at"] >= start))
        and (end is None or (r.get("detected_at") and r["detected_at"] <= end))
    ]
    out.sort(key=lambda r: r.get("detected_at") or dt.datetime.min, reverse=True)
    return out


def get_issue(issue_id: int, root=None) -> dict | None:
    return c.get(QUALITY_ISSUES, c.load(QUALITY_ISSUES, root), (issue_id,))


@c.locked
def acknowledge(issue_id: int, by: str, reason: str | None = None,
                expires_at: dt.datetime | None = None, acknowledged: bool = True,
                root=None) -> dict:
    expires_at = _naive_utc(expires_at)
    rows = c.load(QUALITY_ISSUES, root)
    row = c.require(QUALITY_ISSUES, rows, (issue_id,))
    row.update(
        acknowledged=bool(acknowledged),
        ack_by=by if acknowledged else None,
        ack_reason=reason if acknowledged else None,
        ack_expires_at=expires_at if acknowledged else None,
    )
    c.save(QUALITY_ISSUES, rows, root)
    return row


def by_table(root=None) -> list[dict]:
    """Issue counts grouped by target_table (dashboard / quality overview)."""
    agg: dict[str, dict] = {}
    for r in c.load(QUALITY_ISSUES, root):
        a = agg.setdefault(r["target_table"], {"target_table": r["target_table"], "total": 0,
                                               "warn": 0, "error": 0})
        a["total"] += 1
        a[r["level"]] = a.get(r["level"], 0) + 1
    return sorted(agg.values(), key=lambda a: -a["total"])


def by_check(root=None) -> list[dict]:
    agg: dict[str, dict] = {}
    for r in c.load(QUALITY_ISSUES, root):
        a = agg.setdefault(r["check_name"], {"check_name": r["check_name"], "total": 0})
        a["total"] += 1
    return sorted(agg.values(), key=lambda a: -a["total"])


def timeseries(days: int = 30, group_by: str = "check_name", root=None) -> list[dict]:
    """Per-day issue counts for the last ``days``, split by ``group_by``
    (check_name | level | target_table)."""
    if group_by not in ("check_name", "level", "target_table"):
        raise ValueError(f"invalid group_by {group_by!r}")
    cutoff = ids.utc_now() - dt.timedelta(days=days)
    buckets: dict[tuple, int] = {}
    for r in c.load(QUALITY_ISSUES, root):
        det = r.get("detected_at")
        if det is None or det < cutoff:
            continue
        day = det.date().isoformat()
        key = (day, r.get(group_by))
        buckets[key] = buckets.get(key, 0) + 1
    return [
        {"date": day, "group": grp, "count": n}
        for (day, grp), n in sorted(buckets.items())
    ]
