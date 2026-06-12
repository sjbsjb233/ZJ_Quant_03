"""T-WP5-001…004 — quant_admission 资源准入闸(《内存治理总体方案》WP5).

预算闸并发上限 / ballast 零放行 / D16 滞留自报与恢复 / 排干-独占-恢复事件序列。
缩限不缩机制:预算与阈值缩小,判定/排干/台账机制全开。
"""
from __future__ import annotations

import pytest

from quant_admission import AdmissionGate, Deferred, Grant
from quant_data import logging as qd_logging


class FakeProbe:
    """可注入的余量探针:limit/available 由测试拨动。"""

    def __init__(self, limit_mb=None, available_mb=None):
        self._limit = limit_mb
        self._avail = available_mb

    def limit_mb(self):
        return self._limit

    def available_mb(self):
        return self._avail

    def set(self, *, limit_mb=None, available_mb=None):
        if limit_mb is not None:
            self._limit = limit_mb
        if available_mb is not None:
            self._avail = available_mb


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def events():
    captured: list = []
    qd_logging.register_sink(lambda rec: captured.append(rec))
    yield captured
    qd_logging.unregister_all_sinks()


def _names(events):
    return [e.event for e in events if e.logger == "quant_admission"]


def _gate(probe, clock=None, **kw):
    kw.setdefault("budgets", {"heavy": 600, "light": 100, "giant": 1500})
    kw.setdefault("reserve_mb", 0)
    kw.setdefault("exclusive_classes", ())
    # 运行时语义用例:关启动校验(giant 故意超 limit 以测运行时 deferred 路径;
    # 启动 fail-fast 单独由 test_wp5_budget_failfast_validation 覆盖)。
    kw.setdefault("validate_budgets", False)
    return AdmissionGate(probe=probe, clock=clock or Clock(), **kw)


# ── T-WP5-001: 预算只容 2 ⇒ in_flight ≤ 2 且出现 deferred 台账 ───────────────
def test_wp5_001_budget_caps_concurrency(events):
    gate = _gate(FakeProbe(limit_mb=1300, available_mb=1300))
    verdicts = [gate.try_acquire("heavy", task_id=f"t{i}") for i in range(4)]
    grants = [v for v in verdicts if isinstance(v, Grant)]
    deferred = [v for v in verdicts if isinstance(v, Deferred)]
    assert len(grants) == 2 and len(deferred) == 2
    assert gate.in_flight_mb() == 1200  # ≤ min(余量, limit−reserve)=1300
    assert all(d.reason == "draining" for d in deferred)  # 装得下独占 ⇒ 排干等位
    assert "admission.granted" in _names(events)
    assert "admission.deferred" in _names(events)
    # 决策台账字段齐全 (方案 WP5: task_class/est_mb/available_mb/in_flight_mb)
    g = next(e for e in events if e.event == "admission.granted")
    assert {"task_class", "task_id", "est_mb", "in_flight_mb"} <= set(g.extra or {})
    for v in grants:
        v.release()
    assert gate.in_flight_mb() == 0


def test_wp5_001_release_reopens_gate(events):
    gate = _gate(FakeProbe(limit_mb=700, available_mb=700))
    a = gate.try_acquire("heavy", task_id="a")
    assert isinstance(a, Grant)
    b = gate.try_acquire("heavy", task_id="b")
    assert isinstance(b, Deferred)
    a.release()
    b2 = gate.try_acquire("heavy", task_id="b")
    assert isinstance(b2, Grant)
    b2.release()


# ── T-WP5-002: ballast 预占内存 ⇒ 重任务全部 deferred、零放行 ────────────────
def test_wp5_002_ballast_pressure_defers_everything(events):
    probe = FakeProbe(limit_mb=12000, available_mb=80)   # ballast 吃掉余量
    gate = _gate(probe, reserve_mb=1024)
    for i in range(5):
        v = gate.try_acquire("heavy", task_id=f"t{i}")
        assert isinstance(v, Deferred), "cgroup 余量不足时必须全部暂缓"
    assert gate.in_flight_mb() == 0
    # 排干队头也不放行 —— 余量探针在队头放行点同样生效 (零 OOM)
    head_again = gate.try_acquire("heavy", task_id="t0")
    assert isinstance(head_again, Deferred)
    # ballast 释放后队头可独占放行
    probe.set(available_mb=11000)
    head = gate.try_acquire("heavy", task_id="t0")
    assert isinstance(head, Grant) and head.exclusive
    head.release()


# ── T-WP5-003: D16 哨兵 — 滞留自报与恢复 ────────────────────────────────────
def test_wp5_003_d16_stuck_marker_and_recovery(events):
    clock = Clock()
    probe = FakeProbe(limit_mb=700, available_mb=700)
    gate = _gate(probe, clock=clock, deferred_warn_s=60.0, draining_warn_s=120.0)
    a = gate.try_acquire("heavy", task_id="a")
    assert isinstance(a, Grant)
    assert isinstance(gate.try_acquire("heavy", task_id="b"), Deferred)
    assert "admission.deferred.stuck" not in _names(events)  # 未到阈值

    def wait_retrying(seconds: float, step: float = 10.0) -> None:
        # 模拟 dispatcher 轮询续租 (真实节拍 1s ≪ 等位租约 30s)
        left = seconds
        while left > 0:
            clock.t += min(step, left)
            left -= step
            assert isinstance(gate.try_acquire("heavy", task_id="b"), Deferred)

    wait_retrying(61)
    assert "admission.deferred.stuck" in _names(events)      # 滞留 > 阈值 ⇒ 自报
    stuck = next(e for e in events if e.event == "admission.deferred.stuck")
    assert stuck.level == "ERROR" and (stuck.extra or {})["task_class"] == "heavy"
    # 排干超时自报
    wait_retrying(120)
    assert "admission.draining.timeout" in _names(events)
    # 恢复:释放 → 放行 → 滞留计时清零(granted 后不再重报)
    a.release()
    g = gate.try_acquire("heavy", task_id="b")
    assert isinstance(g, Grant)
    n_stuck = _names(events).count("admission.deferred.stuck")
    g.release()
    clock.t += 200
    c = gate.try_acquire("heavy", task_id="c")
    assert isinstance(c, Grant)
    assert _names(events).count("admission.deferred.stuck") == n_stuck
    c.release()


# ── T-WP5-004: 排干-独占-恢复 全事件序列 ────────────────────────────────────
def test_wp5_004_drain_exclusive_resume_sequence(events):
    clock = Clock()
    gate = _gate(FakeProbe(limit_mb=2000, available_mb=2000), clock=clock)
    lights = [gate.try_acquire("light", task_id=f"l{i}") for i in range(3)]
    assert all(isinstance(v, Grant) for v in lights)
    # 巨型任务 (1500) 现在装不下 (300+1500>2000−0? in_flight=300, headroom=2000 →
    # 300+1500=1800 ≤ 2000 装得下!) —— 把可用余量压小制造排干
    gate2 = _gate(FakeProbe(limit_mb=2000, available_mb=2000), clock=clock,
                  budgets={"light": 300, "giant": 1500})
    l1 = gate2.try_acquire("light", task_id="l1")
    l2 = gate2.try_acquire("light", task_id="l2")
    l3 = gate2.try_acquire("light", task_id="l3")
    assert all(isinstance(v, Grant) for v in (l1, l2, l3))   # 900 in flight
    g = gate2.try_acquire("giant", task_id="G")
    assert isinstance(g, Deferred) and g.draining            # 900+1500 > 2000 ⇒ 排干
    assert gate2.is_draining()
    # 排干期间:新轻任务一律暂缓 (只出不进, 防饿死)
    l4 = gate2.try_acquire("light", task_id="l4")
    assert isinstance(l4, Deferred) and l4.reason == "draining"
    # 轻任务自然跑完 (绝不打断)
    l1.release(); l2.release()
    assert isinstance(gate2.try_acquire("giant", task_id="G"), Deferred)  # 还有在跑
    l3.release()
    G = gate2.try_acquire("giant", task_id="G")              # 场清 ⇒ 队头独占放行
    assert isinstance(G, Grant) and G.exclusive
    # 独占期间仍只出不进
    l5 = gate2.try_acquire("light", task_id="l5")
    assert isinstance(l5, Deferred) and l5.reason == "exclusive-running"
    G.release()                                              # 结束 ⇒ 恢复正常放行
    assert not gate2.is_draining()
    l6 = gate2.try_acquire("light", task_id="l6")
    assert isinstance(l6, Grant)
    l6.release()
    # app_log 事件序列逐条断言 (方案 T-WP5-004)
    seq = _names(events)
    i_drain = seq.index("admission.draining")
    i_excl = next(i for i, e in enumerate(events)
                  if e.event == "admission.granted" and (e.extra or {}).get("exclusive"))
    assert i_drain < i_excl, "先排干后独占"
    assert "admission.released" in seq[i_excl + 1:], "独占结束有归还台账"


def test_wp5_declared_exclusive_class_runs_alone(events):
    gate = AdmissionGate(
        budgets={"factor.eval.minute": 450, "light": 100}, reserve_mb=0,
        probe=FakeProbe(limit_mb=2000, available_mb=2000),
        exclusive_classes=("factor.eval.minute",), clock=Clock(),
    )
    lt = gate.try_acquire("light", task_id="l1")
    assert isinstance(lt, Grant)
    m = gate.try_acquire("factor.eval.minute", task_id="m1")
    assert isinstance(m, Deferred) and m.draining            # 声明独占 ⇒ 必须排干
    lt.release()
    m2 = gate.try_acquire("factor.eval.minute", task_id="m1")
    assert isinstance(m2, Grant) and m2.exclusive
    m2.release()


def test_wp5_waiter_lease_expiry_unblocks_gate(events):
    """排干等位者弃单 (web 429 客户端不重试) ⇒ 租约过期,闸自动解除排干。"""
    clock = Clock()
    gate = _gate(FakeProbe(limit_mb=2000, available_mb=2000), clock=clock,
                 budgets={"light": 300, "giant": 1500})
    holders = [gate.try_acquire("light", task_id=f"l{i}") for i in range(3)]
    assert isinstance(gate.try_acquire("giant", task_id="G"), Deferred)
    assert gate.is_draining()
    clock.t += 31  # > _WAITER_TTL_S, G 不再续租
    assert not gate.is_draining()
    ok = gate.try_acquire("light", task_id="l9")
    assert isinstance(ok, Grant)
    ok.release()
    for h in holders:
        h.release()


def test_wp5_unknown_class_gets_default_budget_and_warn(events):
    gate = _gate(FakeProbe(limit_mb=2000, available_mb=2000), default_budget_mb=123)
    v = gate.try_acquire("unregistered.class", task_id="x")
    assert isinstance(v, Grant) and v.est_mb == 123
    assert "admission.unknown_class" in _names(events)  # 引导登记 (守则 §4)
    v.release()


def test_wp5_impossible_budget_reports_error(events):
    gate = _gate(FakeProbe(limit_mb=1000, available_mb=1000), reserve_mb=600)
    v = gate.try_acquire("giant", task_id="g")  # 1500 > 1000−600 ⇒ 永不可行
    assert isinstance(v, Deferred) and v.reason == "insufficient-budget"
    assert "admission.budget.impossible" in _names(events)


def test_wp5_budget_failfast_validation():
    """T-WP5-008: 单类预算 > mem_limit−reserve ⇒ 进程启动即 fail-fast。"""
    with pytest.raises(ValueError, match="永远无法放行"):
        AdmissionGate(budgets={"x": 5000}, reserve_mb=256,
                      probe=FakeProbe(limit_mb=3072, available_mb=3000))
    # 校验范围参数化:小容器只对自己的任务类负责(共享预算表不误炸)
    g = AdmissionGate(budgets={"x": 5000, "mine": 100}, reserve_mb=256,
                      probe=FakeProbe(limit_mb=3072, available_mb=3000),
                      validate_budgets=("mine",))
    assert g.budget_for("mine") == 100
    # 探针无 limit(dev 机)⇒ 跳过校验不抛
    AdmissionGate(budgets={"x": 5000},
                  probe=FakeProbe(limit_mb=None, available_mb=None))


def test_wp5_from_env_budgets(monkeypatch):
    monkeypatch.setenv("ADMISSION_BUDGETS_JSON", '{"factor.eval.daily": 42}')
    monkeypatch.setenv("ADMISSION_RESERVE_MB", "7")
    gate = AdmissionGate.from_env(probe=FakeProbe(limit_mb=None, available_mb=None))
    assert gate.budget_for("factor.eval.daily") == 42      # env 覆盖
    assert gate.budget_for("keeper.compact") == 800        # 缺省表兜底
    assert gate._reserve_mb == 7
    # 预算表设 ∞ 即旁路 (方案 §8 回滚位): 无探针 + 巨预算照样放行
    v = gate.try_acquire("factor.eval.daily", task_id="t")
    assert isinstance(v, Grant)
    v.release()
