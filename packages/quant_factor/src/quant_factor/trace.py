"""RunTrace — 运行过程采集台账（与 :class:`DegradationLedger` 并列的旁路记录器）。

设计目标：把引擎跑一轮评估时"每一步发生了什么 / 每个调仓日的中间数据"
结构化地记下来，供详情页「运行透视 / Run Trace」Tab 可视化核对
（取证工具：管理员逐帧核对因子怎么算出来的）。

红线（设计 §0.3/§0.4）：本模块**只旁路记录**，绝不参与任何数值计算，绝不改变
``result``。全部产出为可 JSON 序列化的普通 dict / list / float / None；采集开关
（``trace_enabled``）可关，关闭时引擎不创建 trace、不埋点、行为逐字节等价于改造前。

与 :mod:`quant_factor.degradation` 一样，本模块是纯数据 —— 无 FastAPI / DB /
DataReader 依赖，可在 notebook 和引擎里复用。
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np


TRACE_SCHEMA_VERSION = 1

# 单日 top/bottom 抽样名单上限（防止名单过长把 artifact 撑爆）。
MAX_SAMPLE_NAMES = 20

# 逐调仓日明细上限（日频一年 ~244 天；超出则截断并置 ``days_truncated`` 标记）。
MAX_TRACE_DAYS = 800


def _f(x: Any) -> float | None:
    """安全转 float，``NaN`` / ``Inf`` / 不可转 → ``None``（JSON 友好）。"""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def describe(arr, *, histogram: bool = True) -> dict[str, Any]:
    """对一个截面数组做"五数概括 + 均值/标准差/有效数/缺失数"。

    这是单日分布核对的基石：管理员一眼能看出标准化后是否 ``mean≈0 / std≈1``、
    去极值是否真的削了尾、是否退化成常量。

    返回的键是固定的（``n`` 为 0 时数值键取 ``None``），便于前端无脑对接；
    ``histogram=False`` 时省略 16 桶直方图以控制体量（对应服务侧
    ``trace_histogram_enabled`` 开关）。
    """
    if arr is None:
        return {
            "n": 0, "n_nan": 0, "min": None, "p25": None, "p50": None,
            "p75": None, "max": None, "mean": None, "std": None,
        }
    a = np.asarray(arr, dtype=float)
    n_total = int(a.size)
    finite = a[np.isfinite(a)]
    n = int(finite.size)
    if n == 0:
        return {
            "n": 0, "n_nan": n_total, "min": None, "p25": None, "p50": None,
            "p75": None, "max": None, "mean": None, "std": None,
        }
    q = np.quantile(finite, [0.0, 0.25, 0.5, 0.75, 1.0])
    out: dict[str, Any] = {
        "n": n,
        "n_nan": n_total - n,
        "min": _f(q[0]), "p25": _f(q[1]), "p50": _f(q[2]),
        "p75": _f(q[3]), "max": _f(q[4]),
        "mean": _f(finite.mean()),
        "std": _f(finite.std(ddof=1)) if n > 1 else 0.0,
    }
    if histogram:
        h = _histogram(finite, bins=16)
        if h is not None:
            out["hist"] = h
    return out


def _histogram(finite: np.ndarray, bins: int = 16) -> dict | None:
    """16 桶直方图供前端画分布小图；样本太少（<4）返回 ``None``。"""
    if finite.size < 4:
        return None
    counts, edges = np.histogram(finite, bins=bins)
    return {
        "counts": [int(c) for c in counts],
        "edges": [_f(e) for e in edges],
    }


@dataclass
class StageRecord:
    """流水线某一阶段的元信息记录。"""

    stage: str                          # load|preflight|universe_resolve|compute|write|eval
    status: str = "ok"                  # ok|degrade|blocked|error|skipped
    started_at: str | None = None
    duration_ms: float | None = None
    detail: dict = field(default_factory=dict)
    error: str | None = None


class RunTrace:
    """一轮运行的过程采集台账。

    ``day(date)`` 返回一个**可变 dict**，引擎里 ``_prep_all`` / ``_ic_series`` /
    ``_layered`` / ``_turnover`` 各处都往同一天的 dict 里塞自己负责的字段，最后由
    ``to_jsonable`` 汇总 —— 这就是"针线"：不动各步骤的计算，只在每步算完后顺手记一笔。
    """

    def __init__(self, run_id: str | None = None, *, histogram_enabled: bool = True):
        self.run_id = run_id
        # 单日分布直方图开关（体量敏感时关掉；只影响 describe 的 hist 字段）。
        self.histogram_enabled = histogram_enabled
        self._stages: list[StageRecord] = []
        # 按调仓日聚合：date(str) -> 该日的过程数据 dict。
        self._days: dict[str, dict[str, Any]] = {}
        self._day_order: list[str] = []
        self.meta: dict[str, Any] = {}     # 全局元信息（因子/版本/区间…）

    # ── 阶段记录 ────────────────────────────────────────────────────────
    def stage(self, stage: str, **detail) -> StageRecord:
        """开启（或追加）一个阶段记录；返回 ``StageRecord`` 供 ``finish_stage`` 收尾。"""
        rec = StageRecord(
            stage=stage,
            started_at=dt.datetime.utcnow().isoformat() + "Z",
            detail=dict(detail),
        )
        self._stages.append(rec)
        return rec

    def finish_stage(
        self,
        rec: StageRecord,
        *,
        status: str = "ok",
        duration_ms: float | None = None,
        error: str | None = None,
        **extra_detail,
    ) -> None:
        if rec is None:
            return
        rec.status = status
        if duration_ms is not None:
            rec.duration_ms = duration_ms
        rec.error = error
        rec.detail.update(extra_detail)

    def stage_record(self, stage: str) -> StageRecord | None:
        """返回某阶段最近一次的记录（按需取回，例如 preflight 复用）。"""
        for rec in reversed(self._stages):
            if rec.stage == stage:
                return rec
        return None

    # ── 调仓日记录（多处埋点 merge 到同一天）────────────────────────────
    def day(self, d) -> dict[str, Any]:
        """取（或创建）某调仓日的可变记录 dict，供各处 merge 进来。"""
        key = str(d)
        rec = self._days.get(key)
        if rec is None:
            rec = {"date": key, "degradation_codes": []}
            self._days[key] = rec
            self._day_order.append(key)
        return rec

    def note_day_degradation(self, d, code: str) -> None:
        rec = self.day(d)
        if code not in rec["degradation_codes"]:
            rec["degradation_codes"].append(code)

    # ── 序列化 ──────────────────────────────────────────────────────────
    def to_jsonable(self) -> dict[str, Any]:
        # Sort chronologically (ISO date keys → lexicographic == chronological),
        # so day order is stable regardless of which埋点 created the record first.
        days = sorted(
            (self._days[k] for k in self._day_order),
            key=lambda r: r.get("date") or "",
        )
        truncated = False
        if len(days) > MAX_TRACE_DAYS:
            days = days[:MAX_TRACE_DAYS]
            truncated = True
        return {
            "schema_version": TRACE_SCHEMA_VERSION,
            "run_id": self.run_id,
            "meta": self.meta,
            "stages": [s.__dict__ for s in self._stages],
            "days": days,
            "days_truncated": truncated,
            "n_days": len(self._day_order),
        }
