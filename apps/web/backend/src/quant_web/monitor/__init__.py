"""监控 / 外部观察层 (可观测层重构方案 §4).

= 进程哨兵 (外部观察, 独立容器) + keeper 自对账/回收 (内部自报) + web-backend 评估器
(症状/死人开关/告警生命周期)。本包是 web-backend 侧的评估器 + 告警台账 + ``/monitor/*``
读层；不动日志管道内部 (§11.3)，只在 source 枚举与读层做加法。
"""
from __future__ import annotations

from .evaluator import MonitorEvaluator, get_evaluator, start_evaluator, stop_evaluator
from .routes import router
from .store import (
    AlertStore,
    SignalState,
    get_alert_store,
    get_signal_state,
    init_alert_store,
    init_signal_state,
)


def init_monitor(settings, session_factory, keeper_base: str = ""):
    """Wire the monitor layer at app startup: alert store + signal buffer +
    evaluator thread. Returns the evaluator (or None when MONITOR_ENABLED=false)."""
    alert_store = init_alert_store(session_factory)
    signal_state = init_signal_state()
    return start_evaluator(settings, session_factory, alert_store, signal_state, keeper_base)


__all__ = [
    "MonitorEvaluator", "AlertStore", "SignalState",
    "init_monitor", "start_evaluator", "stop_evaluator", "get_evaluator",
    "init_alert_store", "init_signal_state", "get_alert_store", "get_signal_state",
    "router",
]
