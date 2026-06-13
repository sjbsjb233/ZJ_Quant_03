"""quant_admission — 进程内内存准入闸 (《内存治理总体方案》WP5)。

关键简化:三容器各有 cgroup ``mem_limit`` 互相隔离,准入只需防 **本进程**
多任务叠加 —— 纯进程内信号量 + 余量探针,无跨进程协调、无守护进程。

资源性质 (方案 §2.6):内存是存量资源 —— 暂停任务不释放内存,只有任务结束才
释放。所以这里只有两种武器:**不让进**(deferred / 排干)与上游的 **变小**
(列投影 / 分批)。明确不做自动抢占:杀任务=文明版 OOM-kill,丢已完成工作、
依赖负载快照不可复现,与无静默原则冲突;人工 cancel 保留。

判定 (方案 WP5):``granted ⇔ Σ(in_flight 预算) + 该任务预算 ≤
min(cgroup 余量, mem_limit − reserve)``。预算口径 = Arrow/polars 物化 +
计算瞬时拷贝 (join/group_by ≈ 面板 2–3×) + DuckDB 引擎份额。

exclusive 权重类与预留/排干 (v1.2):任务预算 > 当前空闲、但 ≤ 容器总预算 ⇒
闸进入 **排干模式**:停止放行一切新任务(只出不进),在跑任务自然结束、绝不
打断;预算攒够后该任务独占运行,结束即恢复。排干语义本身即防饿死(队头巨型
任务对后来者有优先权)。独占期间吞吐无损失 —— 内存受限时,超预算的并发只增加
OOM 概率、不增加吞吐。

台账:每次决策写 app_log(经 quant_data.logging 沉到统一日志链):
``admission.granted / admission.deferred / admission.draining /
admission.released``;滞留自报 ``admission.deferred.stuck`` 与
``admission.draining.timeout``(D16 哨兵的触发源,默认 10/30 分钟)。

接入点:① factor dispatcher claim 前;② keeper JobRunner 执行前;
③ web-backend 重端点(FastAPI 依赖,Deferred → 429 + Retry-After)。

测试纪律:缩限不缩机制 —— 测试环境缩 budgets/reserve,机制全开(方案 §2.3)。
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field

from quant_data.logging import get_logger

__all__ = [
    "AdmissionGate",
    "Grant",
    "Deferred",
    "MemoryProbe",
    "DEFAULT_BUDGETS",
    "BUDGETS_ENV",
    "RESERVE_ENV",
]

_log = get_logger("quant_admission")

# ── 静态预算表 (MB; 方案 WP5 — WP2/3 完成后按实测重标定) ────────────────────
DEFAULT_BUDGETS: dict[str, int] = {
    "factor.eval.daily": 600,
    "factor.eval.minute": 4500,
    "factor.correlation": 400,
    "keeper.compact": 800,
    "web.rows.transform": 1200,
    # 接入点附带的轻类(不在方案主表;按各自小预算记账,排干期一并暂停)
    "web.charts.heavy": 512,
    "factor.task.light": 64,
    "keeper.job.light": 64,
}

BUDGETS_ENV = "ADMISSION_BUDGETS_JSON"
RESERVE_ENV = "ADMISSION_RESERVE_MB"
DEFERRED_WARN_ENV = "ADMISSION_DEFERRED_WARN_MIN"    # D16: deferred 滞留阈值 (分钟)
DRAINING_WARN_ENV = "ADMISSION_DRAINING_WARN_MIN"    # D16: 排干滞留阈值 (分钟)
EXCLUSIVE_CLASSES_ENV = "ADMISSION_EXCLUSIVE_CLASSES"

DEFAULT_RESERVE_MB = 1024
DEFAULT_BUDGET_MB = 256            # 未注册任务类的保守预算 (打 warn 引导登记)
DEFAULT_DEFERRED_WARN_MIN = 10
DEFAULT_DRAINING_WARN_MIN = 30
# 声明式独占类:预算 ≈ 整容器的任务天然只能独占跑 (方案 §3.1 分钟线行)
DEFAULT_EXCLUSIVE_CLASSES = ("factor.eval.minute",)

_WAITER_TTL_S = 30.0    # 排干等位者租约:不续租 (重试) 即过期,防 429 客户端弃单卡死闸
_STUCK_RELOG_S = 60.0   # 滞留事件重报间隔 (告警生命周期靠持续事件保活/自动恢复)


# ─────────────────────────────────────────────────────────────────────────────
class MemoryProbe:
    """容器内存余量探针 — cgroup v2 → v1 → psutil → None。

    psutil 在容器内读到的是 **宿主** 口径,必须先试 cgroup(方案 WP5);
    全部不可用时返回 None,闸退化为纯预算记账(仍然防叠加)。

    口径修正(验收 E2E-07 实测): ``memory.current`` 含 page cache —— 容器
    读 parquet 后 cache 可涨到 GB 级且不主动回落,naive ``max − current``
    会把可回收缓存当占用,造成闸的**假性饥饿**(任务永远 deferred)。内核
    在分配压力下会立刻回收 ``inactive_file``,故余量把它计回:
    ``available = max − (current − inactive_file)``。
    """

    _V2_MAX = "/sys/fs/cgroup/memory.max"
    _V2_CUR = "/sys/fs/cgroup/memory.current"
    _V2_STAT = "/sys/fs/cgroup/memory.stat"
    _V1_MAX = "/sys/fs/cgroup/memory/memory.limit_in_bytes"
    _V1_CUR = "/sys/fs/cgroup/memory/memory.usage_in_bytes"
    _V1_STAT = "/sys/fs/cgroup/memory/memory.stat"

    @staticmethod
    def _read_bytes(path: str) -> float | None:
        try:
            raw = open(path).read().strip()
        except OSError:
            return None
        if raw == "max":  # cgroup v2 "no limit"
            return None
        try:
            v = float(raw)
        except ValueError:
            return None
        # cgroup v1 reports a huge sentinel (~2^63) when unlimited
        return None if v >= 2**60 else v

    @staticmethod
    def _reclaimable_bytes(stat_path: str) -> float:
        """可快速回收的 page cache(inactive_file;v1 名 total_inactive_file)。"""
        try:
            for line in open(stat_path):
                key, _, val = line.partition(" ")
                if key in ("inactive_file", "total_inactive_file"):
                    return float(val)
        except (OSError, ValueError):
            pass
        return 0.0

    def limit_mb(self) -> float | None:
        for p in (self._V2_MAX, self._V1_MAX):
            v = self._read_bytes(p)
            if v is not None:
                return v / 1048576.0
        return None

    def available_mb(self) -> float | None:
        for mx, cur, stat in (
            (self._V2_MAX, self._V2_CUR, self._V2_STAT),
            (self._V1_MAX, self._V1_CUR, self._V1_STAT),
        ):
            m, c = self._read_bytes(mx), self._read_bytes(cur)
            if m is not None and c is not None:
                effective = max(0.0, c - self._reclaimable_bytes(stat))
                return max(0.0, (m - effective) / 1048576.0)
        try:  # host-scope fallback (dev machines / no cgroup limit)
            import psutil  # type: ignore

            return psutil.virtual_memory().available / 1048576.0
        except Exception:  # noqa: BLE001 - psutil optional
            return None


# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Deferred:
    """try_acquire 的"暂缓"裁决 — 含原因与建议等待。"""

    task_class: str
    task_id: str
    est_mb: float
    available_mb: float | None
    in_flight_mb: float
    reason: str                       # insufficient-budget | draining | exclusive-running
    suggested_wait_s: float = 5.0
    draining: bool = False


class Grant:
    """持有期间计入 in_flight;``release()``(或 with 块退出)归还预算。"""

    def __init__(self, gate: "AdmissionGate", task_class: str, task_id: str,
                 est_mb: float, exclusive: bool = False):
        self._gate = gate
        self.task_class = task_class
        self.task_id = task_id
        self.est_mb = est_mb
        self.exclusive = exclusive
        self._released = False

    def release(self) -> None:
        if not self._released:
            self._released = True
            self._gate._release(self)

    def __enter__(self) -> "Grant":
        return self

    def __exit__(self, *exc) -> None:
        self.release()


@dataclass
class _Waiter:
    task_class: str
    task_id: str
    est_mb: float
    first_seen: float
    last_seen: float = field(default=0.0)


# ─────────────────────────────────────────────────────────────────────────────
class AdmissionGate:
    def __init__(
        self,
        budgets: dict[str, int] | None = None,
        reserve_mb: int = DEFAULT_RESERVE_MB,
        *,
        default_budget_mb: int = DEFAULT_BUDGET_MB,
        exclusive_classes: tuple[str, ...] = DEFAULT_EXCLUSIVE_CLASSES,
        probe: MemoryProbe | None = None,
        clock=time.monotonic,
        deferred_warn_s: float = DEFAULT_DEFERRED_WARN_MIN * 60.0,
        draining_warn_s: float = DEFAULT_DRAINING_WARN_MIN * 60.0,
        validate_budgets=True,
    ):
        self._budgets = dict(DEFAULT_BUDGETS)
        self._budgets.update(budgets or {})
        self._reserve_mb = float(reserve_mb)
        self._default_budget_mb = float(default_budget_mb)
        self._exclusive_classes = frozenset(exclusive_classes or ())
        self._probe = probe or MemoryProbe()
        self._clock = clock
        if validate_budgets:
            # fail-fast (验收 T-WP5-008): 单类预算 > mem_limit − reserve 的任务
            # 永远进不来 — 配置错误在进程启动时就报,而不是运行时静默饿死。
            # 预算表经 compose 锚点全局共享下发,小容器只对 **自己会申请的类**
            # 负责 — 接入点传本进程任务类集合;True = 校验全部(独立使用)。
            limit = self._probe.limit_mb()
            if limit is not None:
                ceiling = max(0.0, limit - self._reserve_mb)
                scope = (set(self._budgets) if validate_budgets is True
                         else set(validate_budgets))
                bad = {k: v for k, v in self._budgets.items()
                       if k in scope and v > ceiling}
                if bad:
                    raise ValueError(
                        f"ADMISSION_BUDGETS_JSON 配置错误: {bad} 超过容器可用预算 "
                        f"(mem_limit {limit:.0f}MB − reserve {self._reserve_mb:.0f}MB "
                        f"= {ceiling:.0f}MB),该类任务将永远无法放行 — 调小预算或"
                        "调大容器 mem_limit(内存治理方案 WP5)。")
        self._deferred_warn_s = deferred_warn_s
        self._draining_warn_s = draining_warn_s

        self._lock = threading.Lock()
        self._in_flight: dict[str, Grant] = {}          # task_id -> Grant
        self._exclusive_active: Grant | None = None
        self._waiters: list[_Waiter] = []               # 排干等位队列 (FIFO)
        self._draining_since: float | None = None
        self._deferred_since: dict[str, float] = {}     # task_class -> first defer ts
        self._last_stuck_log: dict[str, float] = {}     # rate-limit stuck re-logs
        self._warned_unknown: set[str] = set()

    # ── construction helpers ─────────────────────────────────────────────
    @classmethod
    def from_env(cls, **overrides) -> "AdmissionGate":
        """Build from ADMISSION_* env vars (deploy compose 注入)。"""
        budgets = None
        raw = os.environ.get(BUDGETS_ENV)
        if raw:
            try:
                budgets = {str(k): int(v) for k, v in json.loads(raw).items()}
            except (ValueError, TypeError, AttributeError):
                _log.error("admission.budgets.invalid", raw=raw[:200])
                budgets = None
        kw: dict = {
            "budgets": budgets,
            "reserve_mb": int(os.environ.get(RESERVE_ENV, DEFAULT_RESERVE_MB)),
            "deferred_warn_s": float(
                os.environ.get(DEFERRED_WARN_ENV, DEFAULT_DEFERRED_WARN_MIN)) * 60.0,
            "draining_warn_s": float(
                os.environ.get(DRAINING_WARN_ENV, DEFAULT_DRAINING_WARN_MIN)) * 60.0,
        }
        excl = os.environ.get(EXCLUSIVE_CLASSES_ENV)
        if excl is not None:
            kw["exclusive_classes"] = tuple(c.strip() for c in excl.split(",") if c.strip())
        kw.update(overrides)
        return cls(**kw)

    # ── introspection ────────────────────────────────────────────────────
    def budget_for(self, task_class: str) -> float:
        b = self._budgets.get(task_class)
        if b is None:
            if task_class not in self._warned_unknown:
                self._warned_unknown.add(task_class)
                _log.warn("admission.unknown_class", task_class=task_class,
                          default_mb=self._default_budget_mb)
            return self._default_budget_mb
        return float(b)

    def in_flight_mb(self) -> float:
        with self._lock:
            return self._in_flight_total()

    def is_draining(self) -> bool:
        """排干中(只出不进)— dispatcher 用它决定本轮是否继续 claim。"""
        with self._lock:
            self._prune_waiters(self._clock())
            return bool(self._waiters) or self._exclusive_active is not None

    # ── the gate ─────────────────────────────────────────────────────────
    def try_acquire(self, task_class: str, *, task_id: str,
                    est_mb: float | None = None,
                    allow_wait: bool = True) -> Grant | Deferred:
        """Grant 持有期间计入 in_flight;Deferred 含原因与建议等待。

        只决定重任务 **何时开始**,不碰怎么算(方案 §2.2);每次决策写 app_log。

        ``allow_wait`` (安全审查 P2): 排干等位队列假设同一 ``task_id`` 每轮**重复
        呈现**(factor dispatcher 重复 peek、keeper 循环同 job)。请求-响应型调用方
        (web 端点)不会重新呈现 —— 它拿 Deferred 即返回 429,客户端用 **新** id
        重试。这类调用方必须传 ``allow_wait=False``:拿不到就立即 Deferred,**不入
        排干队列、不触发排干模式**,否则会留下永不被认领的孤儿队头,30s 内把所有
        同类端点拖进 draining(连环 429)。
        """
        est = float(est_mb) if est_mb is not None else self.budget_for(task_class)
        with self._lock:
            now = self._clock()
            self._prune_waiters(now)
            verdict = self._decide(task_class, task_id, est, now, allow_wait=allow_wait)
            self._log_decision(verdict, now)
            return verdict

    # ── internals (call with lock held) ──────────────────────────────────
    def _in_flight_total(self) -> float:
        return sum(g.est_mb for g in self._in_flight.values())

    def _headroom_mb(self) -> float | None:
        """min(cgroup 余量, mem_limit − reserve);两者皆不可知 ⇒ None (无界)。"""
        avail = self._probe.available_mb()
        limit = self._probe.limit_mb()
        caps = [c for c in (
            avail,
            (limit - self._reserve_mb) if limit is not None else None,
        ) if c is not None]
        return min(caps) if caps else None

    def _fits_alone(self, est: float) -> bool:
        limit = self._probe.limit_mb()
        if limit is None:
            return True
        return est <= max(0.0, limit - self._reserve_mb)

    def _prune_waiters(self, now: float) -> None:
        # 等位者租约过期 (客户端弃单) ⇒ 移除,防止排干模式被永久卡死。
        kept = [w for w in self._waiters if now - w.last_seen <= _WAITER_TTL_S]
        if len(kept) != len(self._waiters):
            for w in self._waiters:
                if w not in kept:
                    _log.warn("admission.waiter.expired", task_class=w.task_class,
                              task_id=w.task_id)
            self._waiters = kept
        if not self._waiters and self._exclusive_active is None:
            self._draining_since = None

    def _decide(self, task_class: str, task_id: str, est: float, now: float,
                allow_wait: bool = True):
        headroom = self._headroom_mb()
        in_flight = self._in_flight_total()
        declared_exclusive = task_class in self._exclusive_classes

        def defer(reason: str, *, draining: bool = False, wait: float = 5.0) -> Deferred:
            self._deferred_since.setdefault(task_class, now)
            return Deferred(task_class, task_id, est, headroom, in_flight,
                            reason, suggested_wait_s=wait, draining=draining)

        def grant(exclusive: bool = False) -> Grant:
            g = Grant(self, task_class, task_id, est, exclusive=exclusive)
            self._in_flight[task_id] = g
            if exclusive:
                self._exclusive_active = g
            self._deferred_since.pop(task_class, None)
            return g

        # ① 独占任务在跑 ⇒ 只出不进
        if self._exclusive_active is not None:
            return defer("exclusive-running", draining=True)

        # ② 排干等位队列非空 ⇒ 只有队头、场上已清空、且 **余量探针复查通过**
        #    时放行(独占)。复查防的是进程常驻/外部 ballast 占住 cgroup 的
        #    情况 —— in_flight 记账为 0 不等于内存真空闲 (T-WP5-002 零 OOM)。
        if self._waiters:
            head = self._waiters[0]
            mine = next((w for w in self._waiters if w.task_id == task_id), None)
            if mine is not None:
                mine.last_seen = now
            if head.task_id == task_id:
                if not self._in_flight and (headroom is None or est <= headroom):
                    self._waiters.pop(0)
                    if not self._waiters:
                        self._draining_since = None
                    return grant(exclusive=True)
                return defer("draining", draining=True)
            # 后到者 (无论轻重) 一律暂缓 —— 排干语义即防饿死
            return defer("draining", draining=True)

        # ③ 正常预算判定: Σin_flight + est ≤ min(cgroup 余量, limit − reserve)
        fits_now = headroom is None or (in_flight + est) <= headroom
        if fits_now and not declared_exclusive:
            return grant()
        if (
            declared_exclusive
            and not self._in_flight
            and self._fits_alone(est)
            and (headroom is None or est <= headroom)
        ):
            return grant(exclusive=True)

        # ④ 现在装不下,但独占可行 ⇒ 入排干队列 (FIFO),进入只出不进。
        #    请求-响应型调用方 (allow_wait=False) 不入队 —— 直接 Deferred,避免
        #    留下永不认领的孤儿队头把同类端点拖进连环 draining (安全审查 P2)。
        if self._fits_alone(est):
            if not allow_wait:
                return defer("insufficient-budget", wait=5.0)
            self._waiters.append(_Waiter(task_class, task_id, est, now, now))
            if self._draining_since is None:
                self._draining_since = now
                _log.info("admission.draining", task_class=task_class, task_id=task_id,
                          est_mb=est, in_flight_mb=in_flight,
                          available_mb=headroom)
            return defer("draining", draining=True)

        # ⑤ 连独占都装不下 (预算 > 容器总预算) ⇒ 永远进不来,显式报错级事件
        _log.error("admission.budget.impossible", task_class=task_class,
                   task_id=task_id, est_mb=est,
                   limit_mb=self._probe.limit_mb(), reserve_mb=self._reserve_mb)
        return defer("insufficient-budget", wait=60.0)

    def _log_decision(self, verdict, now: float) -> None:
        if isinstance(verdict, Grant):
            _log.info("admission.granted", task_class=verdict.task_class,
                      task_id=verdict.task_id, est_mb=verdict.est_mb,
                      exclusive=verdict.exclusive,
                      in_flight_mb=self._in_flight_total(),
                      available_mb=self._probe.available_mb())
            return
        # Deferred —— 决策链全记录;同一 task 的高频重试降为 DEBUG 防洪
        first = self._deferred_since.get(verdict.task_class, now)
        emit = _log.info if (now - first) < 1.0 else _log.debug
        emit("admission.deferred", task_class=verdict.task_class,
             task_id=verdict.task_id, est_mb=verdict.est_mb,
             available_mb=verdict.available_mb, in_flight_mb=verdict.in_flight_mb,
             reason=verdict.reason)
        # D16 自报:滞留超阈值 ⇒ ERROR 级 stuck 标记 (持续重报保活告警,恢复即停)
        waited = now - first
        last = self._last_stuck_log.get(verdict.task_class, -1e18)
        if waited > self._deferred_warn_s and (now - last) >= _STUCK_RELOG_S:
            self._last_stuck_log[verdict.task_class] = now
            _log.error("admission.deferred.stuck", task_class=verdict.task_class,
                       task_id=verdict.task_id, waited_s=int(waited),
                       threshold_s=int(self._deferred_warn_s), reason=verdict.reason)
        if (
            self._draining_since is not None
            and (now - self._draining_since) > self._draining_warn_s
            and (now - self._last_stuck_log.get("__draining__", -1e18)) >= _STUCK_RELOG_S
        ):
            self._last_stuck_log["__draining__"] = now
            _log.error("admission.draining.timeout",
                       draining_s=int(now - self._draining_since),
                       threshold_s=int(self._draining_warn_s),
                       waiters=[w.task_class for w in self._waiters])

    def _release(self, grant: Grant) -> None:
        with self._lock:
            self._in_flight.pop(grant.task_id, None)
            if self._exclusive_active is grant:
                self._exclusive_active = None
                if not self._waiters:
                    self._draining_since = None
            _log.info("admission.released", task_class=grant.task_class,
                      task_id=grant.task_id, est_mb=grant.est_mb,
                      in_flight_mb=self._in_flight_total())


def new_task_id(prefix: str = "adm") -> str:
    """Convenience id for callers without a natural task id (web requests)."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
