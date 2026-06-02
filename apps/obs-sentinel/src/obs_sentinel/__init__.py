"""obs-sentinel — 进程哨兵 / 外部观察容器 (可观测层重构方案 §4.6).

最小权限的旁路容器：只读挂 ``docker.sock``，每 ``SENTINEL_INTERVAL_SEC`` 巡检一次
本项目的容器，抓内核/Docker 才看得见的故障 (OOMKilled / RestartCount / Health /
mem%)，然后：

  1. 永远先把原始观测 POST 给 web-backend ``/monitor/signals`` (评估器维护时间窗,
     这个 POST 本身就是哨兵心跳, 供 D12 监控的监控);
  2. 本地判定 D1 (OOM) / D2 (重启风暴) / D4 (内存压力)，命中即以 ``source=monitor``
     ship 一条日志到 ``/logs/ingest`` (复用现有投递管道, 立刻出现在实时流/trace)。

纯 stdlib，无第三方依赖 —— 镜像极小，且哨兵自身不可达绝不影响业务 (POST 失败只丢上报)。
"""
from __future__ import annotations

__version__ = "0.1.0"
