"""Sentinel runtime config (env-driven, 可观测层重构方案 §4.3 / §11.1)."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


@dataclass
class Config:
    # where to ship to (web-backend); both reuse the shared LOG_INGEST_TOKEN
    signals_url: str = ""
    log_ingest_url: str = ""
    token: str = ""
    # docker + cadence
    docker_socket: str = "/var/run/docker.sock"
    interval_sec: int = 30
    label_filter: str = "com.docker.compose.project=zj-quant"
    # local D1/D2/D4 thresholds (for the monitor logs the sentinel ships itself)
    restart_storm_count: int = 3
    restart_storm_window_min: int = 10
    mem_pressure_pct: int = 90

    @classmethod
    def from_env(cls) -> "Config":
        token = os.environ.get("MONITOR_SIGNALS_TOKEN") or os.environ.get("LOG_INGEST_TOKEN", "")
        return cls(
            signals_url=os.environ.get("MONITOR_SIGNALS_URL", ""),
            log_ingest_url=os.environ.get("LOG_INGEST_URL", ""),
            token=token,
            docker_socket=os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock"),
            interval_sec=_int("SENTINEL_INTERVAL_SEC", 30),
            label_filter=os.environ.get("SENTINEL_LABEL_FILTER",
                                        "com.docker.compose.project=zj-quant"),
            restart_storm_count=_int("RESTART_STORM_COUNT", 3),
            restart_storm_window_min=_int("RESTART_STORM_WINDOW_MIN", 10),
            mem_pressure_pct=_int("MEM_PRESSURE_PCT", 90),
        )
