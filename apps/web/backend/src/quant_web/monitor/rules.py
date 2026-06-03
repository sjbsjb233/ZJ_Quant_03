"""Detection-rule registry (D1–D12) for the 监控 / 外部观察 layer.

每条规则都声明：触发源 (sentinel / data-keeper / evaluator)、严重级、所属层、可调
参数 (默认值取自 :class:`Settings`，UI/admin 可覆盖 → ``monitor_rule`` 表)、以及面向
人类的"抓的是什么故障 / 参数含义"说明 (规则页 §7.3⑧)。

The registry is the single source of truth shared by the evaluator (which reads
``param`` to threshold) and the ``/monitor/rules`` API (which renders the table).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from ..config import Settings
from ..db.models import MonitorAlert, MonitorRule


@dataclass(frozen=True)
class Param:
    key: str            # stable key (also the monitor_rule.params key)
    label: str          # zh label shown in the rules table
    setting: str        # Settings attribute holding the default


@dataclass(frozen=True)
class Rule:
    id: str             # 'D2.restart_storm' — also MonitorAlert.rule_id
    name: str           # zh display name
    source: str         # 哨兵 | 数据写者 | 评估器
    severity: str       # info|warn|error|critical (base; D8 escalates)
    layer: str          # which layer this rule judges
    catches: str        # "抓的是什么故障"
    detail: str         # "参数含义"
    params: tuple[Param, ...] = field(default_factory=tuple)
    enabled_default: bool = True
    # 检测方 (可观测层重构方案 §6.5)：sentinel = 哨兵 (docker.sock + statvfs 直采, 绕开
    # backend);backend = 评估器 (日志派生)。生命周期台账始终在 backend (唯一台账)。
    detector_owner: str = "backend"
    # container = 挂在某容器上;host = 宿主级 (磁盘)。前端据此归层/渲染。
    target_kind: str = "container"


# L0 + D1–D15 (可观测层重构方案 §4.2). source labels mirror the design's 规则页.
# detector_owner=sentinel ⇒ 检测在哨兵 (绕开 backend);生命周期仍在 backend 唯一台账。
RULES: tuple[Rule, ...] = (
    Rule(
        "L0.unreachable", "存活探测", "哨兵", "error", "backend",
        "关键容器对存活探测 (HTTP/TCP) 不可达 —— 「是否还活着」的真相源, 不读任何业务接口。",
        "T = 单次探测超时秒。连续探测失败即判不可达 (启动宽限期内不误报)。",
        (Param("timeout_sec", "超时 T 秒", "liveness_probe_timeout_sec"),),
        detector_owner="sentinel",
    ),
    Rule(
        "D1.oom_killed", "OOM 强杀", "哨兵", "critical", "data",
        "容器被 cgroup OOM killer 杀死 —— 数据 / 写入链路可能中断。",
        "检测到 OOMKilled 即刻触发，CRITICAL 级，无需窗口聚合。",
        detector_owner="sentinel",
    ),
    Rule(
        "D2.restart_storm", "重启风暴", "哨兵", "critical", "data",
        "容器在短窗口内反复重启 —— 通常是崩溃循环或被反复强杀 (上次 P0 的根因)。",
        "N = 触发阈值次数 · W = 滑动统计窗口 (分钟)。窗口内重启 ≥ N 次即触发。",
        (Param("count", "N 次", "restart_storm_count"),
         Param("window_min", "W 分钟", "restart_storm_window_min")),
        detector_owner="sentinel",
    ),
    Rule(
        "D3.unhealthy", "容器不健康", "哨兵", "error", "data",
        "容器 healthcheck 持续 unhealthy 或健康状态反复抖动。",
        "S = unhealthy 须持续的秒数 · F/窗口 = 健康状态在窗口内翻转的次数阈值。",
        (Param("for_sec", "持续 S 秒", "unhealthy_for_sec"),
         Param("flap_count", "抖动 F 次", "health_flap_count"),
         Param("flap_window_min", "窗口分钟", "health_flap_window_min")),
        detector_owner="sentinel",
    ),
    Rule(
        "D4.mem_pressure", "内存压力", "哨兵", "warn", "data",
        "容器内存占用持续逼近上限 —— 是 OOM 被强杀前的早期信号。",
        "P = 内存占用百分比阈值 · S = 须持续的秒数。占用 ≥ P% 连续 ≥ S 秒即触发。",
        (Param("pct", "达 P%", "mem_pressure_pct"),
         Param("for_sec", "持续 S 秒", "mem_pressure_for_sec")),
        detector_owner="sentinel",
    ),
    Rule(
        "D5.job_interrupted", "启动对账", "数据写者", "error", "data",
        "进程重启后留下 status=running 的孤儿任务 —— 进程猝死不写日志，靠对账补一条 ERROR。",
        "data-keeper 启动时把上一代残留 running 的 Job 翻成 failed(interrupted)。无可调参数。",
    ),
    Rule(
        # 阈值在 data-keeper 侧 (JOB_TIMEOUT_MIN / JOB_HEARTBEAT_STALE_MIN)；web 评估器只把
        # keeper 造的 job.*.timeout ERROR 升为告警，不持有可调参数，故此处 params 为空 (规则页只读说明)。
        "D6.job_timeout", "任务超时回收", "数据写者", "error", "data",
        "running 任务超过 per-type 超时且无进度心跳 —— 卡死任务被回收为 failed(timeout)。",
        "超时 (按类型 maintenance/quality/crawl) + 心跳过期 H 分钟无更新即回收。阈值在 data-keeper 配置。",
    ),
    Rule(
        "D7.deadman", "死人开关", "哨兵", "error", "data",
        "定时任务超过 H 小时没有「成功」过 —— 专治「它根本没跑成功」的静默故障。",
        "H = 容许的最长无成功间隔 (小时)。距上次成功 > H 即触发，不依赖任务是否报错。",
        (Param("hours", "超过 H 小时", "deadman_hours"),),
        detector_owner="sentinel",   # 哨兵直采 keeper /health (不再经 backend)
    ),
    Rule(
        "D8.error_rate", "错误率", "评估器", "warn", "backend",
        "某层 / 路由在统计窗口内错误条数超阈值 —— 表示批量性故障。",
        "T = WARN 阈值 · Tc = ERROR 阈值 · 窗口分钟。窗口内 ERROR+ ≥ T 即触发，≥ Tc 升级。",
        (Param("warn", "WARN 超 T 条", "err_rate_warn"),
         Param("crit", "ERROR 超 Tc 条", "err_rate_crit"),
         Param("window_min", "窗口分钟", "err_rate_window_min")),
    ),
    Rule(
        "D9.pipeline_degraded", "日志管道", "评估器", "error", "monitor",
        "日志管道自身故障 —— 队列丢弃 / 落库或落盘 sink 关闭 / 队列水位过高。",
        "dropped>0 或 sink off → ERROR；队列水位 ≥ Q% → WARN。",
        (Param("queue_pct", "队列水位 Q%", "pipeline_queue_pct"),),
    ),
    Rule(
        # 阈值是 per-layer 字符串 (LAYER_SILENCE_MIN="data=20,factor=30,...")，不符合单 int 的
        # param() 契约；_d10_silence 直接读 settings.layer_silence_by，不走 param 覆盖。故此处
        # 不声明可调 Param (规则页只读说明)，避免"改了 UI 却不生效"。
        "D10.layer_silent", "某层静默", "评估器", "warn", "data",
        "24h 内有日志的层连续 M 分钟 0 条 —— 容器卡死但没死 (没有崩溃日志)。",
        "M = 按层静默阈值 (分钟，data=20/factor=30/backend=15)，由 LAYER_SILENCE_MIN 配置。超时即触发。",
    ),
    Rule(
        "D11.frontend_crash", "前端崩溃", "评估器", "warn", "frontend",
        "单个用户会话内前端崩溃次数过多 —— 指向特定页面 / 版本回归。",
        "Cf = 每会话阈值。同一会话内 react.boundary / window.error ≥ Cf 即触发。",
        (Param("per_session", "每会话 ≥", "fe_crash_per_session"),),
    ),
    Rule(
        "D12.sentinel_offline", "哨兵离线", "评估器", "warn", "monitor",
        "哨兵自身停止心跳 —— 观测能力本身失效 (监控的监控)，面板可能不可信。",
        "Sh = 容许的最长心跳间隔 (秒)。超时即触发。",
        (Param("heartbeat_sec", "心跳超 S 秒", "sentinel_heartbeat_sec"),),
    ),
    # ── 第一层新检测器 (本次盲区, 可观测层重构方案 §4.2 · §9.2) ──────────────
    Rule(
        "D13.disk", "磁盘水位", "哨兵", "warn", "backend",
        "宿主关键分区用量超阈值 —— 上次 P0 的直接盲区 (崩溃转储写爆系统盘)。",
        "Pw = WARN 阈值% · Pc = CRITICAL 阈值%。哨兵 statvfs 直采宿主分区, 用量 ≥ Pw → WARN, ≥ Pc → CRITICAL。",
        (Param("warn_pct", "WARN Pw%", "disk_warn_pct"),
         Param("crit_pct", "CRIT Pc%", "disk_crit_pct")),
        detector_owner="sentinel", target_kind="host",
    ),
    Rule(
        "D14.cpu_saturation", "CPU 饱和", "哨兵", "warn", "backend",
        "容器 CPU 占用持续打满 —— 健康检查/轻请求被饿死 (P0 时最轻 lookup 排队 7.9s)。",
        "P = CPU% 阈值 · S = 须持续秒数。占用 ≥ P% 连续 ≥ S 秒即触发。",
        (Param("pct", "达 P%", "cpu_sat_pct"),
         Param("for_sec", "持续 S 秒", "cpu_sat_for_sec")),
        detector_owner="sentinel",
    ),
    Rule(
        "D15.core_dump", "崩溃转储", "哨兵", "critical", "data",
        "容器可写层体积突增 —— 崩溃 (SIGABRT/SIGSEGV) 写出数 G core dump, 直接写爆磁盘。",
        "G = 可写层 (SizeRw) 突增阈值 (GiB)。超过即判疑似 core-dump, CRITICAL。",
        (Param("rw_gb", "可写层 G GiB", "core_dump_rw_gb"),),
        detector_owner="sentinel",
    ),
)

RULES_BY_ID: dict[str, Rule] = {r.id: r for r in RULES}


def _default_param(settings: Settings, p: Param) -> int | str:
    return getattr(settings, p.setting)


def effective_params(settings: Settings, rule: Rule, override: dict | None) -> dict:
    """Resolve a rule's params: env/code defaults overlaid by a DB override row."""
    out: dict[str, int | str] = {}
    for p in rule.params:
        val: int | str = _default_param(settings, p)
        if override and p.key in override and override[p.key] not in (None, ""):
            ov = override[p.key]
            # keep ints int (the layer_silence_min one stays a str)
            if isinstance(val, int):
                try:
                    val = int(ov)
                except (TypeError, ValueError):
                    pass
            else:
                val = str(ov)
        out[p.key] = val
    return out


def param(settings: Settings, overrides: dict[str, dict], rule_id: str, key: str,
          default: int | None = None) -> int:
    """Convenience for the evaluator: effective int value of one rule param."""
    rule = RULES_BY_ID.get(rule_id)
    if rule is None:
        return default or 0
    # ``overrides`` is keyed rule_id → {"params": {...}, "enabled": ...} (load_overrides);
    # effective_params wants the inner params dict, NOT the wrapper — unwrap it, else every
    # admin threshold override is silently ignored while the rules table still shows it.
    ov = (overrides.get(rule_id) or {}).get("params")
    eff = effective_params(settings, rule, ov)
    val = eff.get(key, default)
    try:
        return int(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default or 0


def load_overrides(db) -> dict[str, dict]:
    """Read every monitor_rule row → ``{rule_id: {"params": {...}, "enabled": bool|None}}``."""
    out: dict[str, dict] = {}
    for row in db.query(MonitorRule).all():
        out[row.rule_id] = {"params": row.params or {}, "enabled": row.enabled}
    return out


def is_enabled(rule: Rule, overrides: dict[str, dict]) -> bool:
    ov = overrides.get(rule.id)
    if ov and ov.get("enabled") is not None:
        return bool(ov["enabled"])
    return rule.enabled_default


def list_rules(settings: Settings, db) -> list[dict]:
    """Render the /monitor/rules table: effective params + enable + 24h fired count."""
    overrides = {r.rule_id: {"params": r.params or {}, "enabled": r.enabled}
                 for r in db.query(MonitorRule).all()}
    since = dt.datetime.utcnow() - dt.timedelta(hours=24)
    # fired_24h per rule (alerts whose last_seen is within 24h)
    fired: dict[str, int] = {}
    from sqlalchemy import func
    for rid, c in (db.query(MonitorAlert.rule_id, func.count(MonitorAlert.id))
                   .filter(MonitorAlert.last_seen >= since)
                   .group_by(MonitorAlert.rule_id).all()):
        fired[rid] = int(c)
    out = []
    for r in RULES:
        ov = overrides.get(r.id, {})
        eff = effective_params(settings, r, ov.get("params"))
        out.append({
            "id": r.id, "name": r.name, "source": r.source, "severity": r.severity,
            "layer": r.layer, "enabled": is_enabled(r, overrides),
            "detector_owner": r.detector_owner, "target_kind": r.target_kind,
            "params": [{"key": p.key, "label": p.label, "value": eff.get(p.key)}
                       for p in r.params],
            "fired_24h": fired.get(r.id, 0),
            "catches": r.catches, "detail": r.detail,
        })
    return out
