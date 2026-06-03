"""Sentinel runtime config (env-driven, 可观测层重构方案 §5 / §9.2).

The sentinel is the *first layer* (心跳/基建): besides the legacy signal/log ship
targets it now owns L0 liveness probing, D13 disk, D14 CPU, D15 core-dump, the D7
dead-man probe, a local JSONL fallback, and a tiny read-only HTTP/SSE status
service. Every knob is an env var so ops can tune thresholds the same way they
tune the evaluator (规则页 detector_owner=sentinel reads these).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# ── L0 liveness probe target ─────────────────────────────────────────────────
@dataclass(frozen=True)
class ProbeTarget:
    layer: str          # observability layer this container belongs to
    name: str           # real container name (matches docker observations)
    url: str            # health URL probed over HTTP (GET, expect <500)


def _parse_targets(raw: str) -> list[ProbeTarget]:
    """Parse ``layer|name|url;layer|name|url`` → [ProbeTarget]. Blank → []."""
    out: list[ProbeTarget] = []
    for seg in (raw or "").split(";"):
        seg = seg.strip()
        if not seg:
            continue
        parts = [p.strip() for p in seg.split("|")]
        if len(parts) == 3 and all(parts):
            out.append(ProbeTarget(parts[0], parts[1], parts[2]))
    return out


def _parse_mounts(raw: str) -> list[tuple[str, str]]:
    """Parse ``mount=statvfs_path,mount=path`` → [(display_mount, fs_path)]."""
    out: list[tuple[str, str]] = []
    for seg in (raw or "").split(","):
        seg = seg.strip()
        if not seg or "=" not in seg:
            continue
        mount, _, path = seg.partition("=")
        if mount.strip() and path.strip():
            out.append((mount.strip(), path.strip()))
    return out


_DEFAULT_TARGETS = (
    "frontend|zj-quant-web-frontend|http://web-frontend:80/;"
    "backend|zj-quant-web-backend|http://web-backend:8000/api/v1/health;"
    "data|zj-quant-data-keeper|http://data-keeper:8300/health;"
    "factor|zj-quant-factor-api|http://factor-api:8100/health"
)
# Inside the container the host partitions are bind-mounted read-only; statvfs is
# called on the *container* path while the frame reports the *host* mount name.
_DEFAULT_MOUNTS = "/=/host-root,/mnt/sdb=/host-sdb"


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

    # -- D1/D2/D3/D4 thresholds (sentinel detects them now, with duration/window) --
    restart_storm_count: int = 3
    restart_storm_window_min: int = 10
    unhealthy_for_sec: int = 90
    mem_pressure_pct: int = 90
    mem_pressure_for_sec: int = 120

    # -- L0 liveness (心跳真相源) --
    liveness_targets: list[ProbeTarget] = field(default_factory=list)
    liveness_probe_timeout_sec: float = 2.0
    liveness_grace_sec: int = 20            # don't flag a freshly-started container as down
    # Fast liveness pass cadence: cheap HTTP probes (+ light inspect) run this often so a
    # killed layer turns red ≤ ~probe interval (AC-1 ≤3s), decoupled from the expensive
    # deep scan (docker stats + size=true inspect) which runs every ``interval_sec``.
    liveness_interval_sec: int = 2

    # -- D7 dead-man (哨兵直采 keeper) --
    keeper_health_url: str = ""
    deadman_hours: int = 25

    # -- D13 disk (statvfs of read-only host mounts) --
    disk_mounts: list[tuple[str, str]] = field(default_factory=list)
    disk_warn_pct: int = 85
    disk_crit_pct: int = 95

    # -- D14 CPU saturation (docker stats cpu%) --
    cpu_sat_pct: int = 90
    cpu_sat_for_sec: int = 120

    # -- D15 core-dump (容器可写层 SizeRw 突增) --
    core_dump_rw_gb: float = 2.0

    # -- local JSONL fallback (本地优先, 哨兵告警永不丢) --
    jsonl_dir: str = "/logs"
    host: str = "obs-sentinel"

    # -- read-only HTTP/SSE status service (§5.2) --
    http_port: int = 8400
    status_push_sec: int = 10               # heartbeat frame interval
    jwt_secret: str = ""                    # shared with backend; HS256 local verify
    jwt_algorithm: str = "HS256"

    @classmethod
    def from_env(cls) -> "Config":
        token = os.environ.get("MONITOR_SIGNALS_TOKEN") or os.environ.get("LOG_INGEST_TOKEN", "")
        targets = _parse_targets(os.environ.get("OBS_LIVENESS_TARGETS", _DEFAULT_TARGETS))
        keeper_health = os.environ.get("KEEPER_HEALTH_URL", "")
        if not keeper_health:
            keeper_health = next((t.url for t in targets if t.layer == "data"), "")
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
            unhealthy_for_sec=_int("UNHEALTHY_FOR_SEC", 90),
            mem_pressure_pct=_int("MEM_PRESSURE_PCT", 90),
            mem_pressure_for_sec=_int("MEM_PRESSURE_FOR_SEC", 120),
            liveness_targets=targets,
            liveness_probe_timeout_sec=_float("LIVENESS_PROBE_TIMEOUT_SEC", 2.0),
            liveness_grace_sec=_int("LIVENESS_GRACE_SEC", 20),
            liveness_interval_sec=_int("OBS_LIVENESS_INTERVAL_SEC", 2),
            keeper_health_url=keeper_health,
            deadman_hours=_int("DEADMAN_HOURS", 25),
            disk_mounts=_parse_mounts(os.environ.get("OBS_DISK_MOUNTS", _DEFAULT_MOUNTS)),
            disk_warn_pct=_int("DISK_WARN_PCT", 85),
            disk_crit_pct=_int("DISK_CRIT_PCT", 95),
            cpu_sat_pct=_int("CPU_SAT_PCT", 90),
            cpu_sat_for_sec=_int("CPU_SAT_FOR_SEC", 120),
            core_dump_rw_gb=_float("CORE_DUMP_RW_GB", 2.0),
            jsonl_dir=os.environ.get("OBS_JSONL_DIR", os.environ.get("LOG_FILE_DIR", "/logs")),
            host=os.environ.get("OBS_HOST", "obs-sentinel"),
            http_port=_int("OBS_HTTP_PORT", 8400),
            status_push_sec=_int("OBS_STATUS_PUSH_SEC", 10),
            jwt_secret=os.environ.get("JWT_SECRET", ""),
            jwt_algorithm=os.environ.get("JWT_ALGORITHM", "HS256"),
        )
