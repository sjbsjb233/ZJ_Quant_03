"""Out-of-band probes the sentinel owns directly (可观测层重构方案 §5.1).

* L0 liveness — a light HTTP GET against each container's own health URL. This is
  the *single source of truth* for "可达 / 不可达"; it never reads a web-backend
  business endpoint, so the heartbeat plane never depends on the rich-data plane.
* D13 disk — ``os.statvfs`` on the read-only host partitions bind-mounted into the
  sentinel. Pure stdlib, no docker dependency, so it works even if the daemon is
  unhappy.

Every probe is defensive: any failure returns a clear "unreachable / unknown"
result instead of raising, so one bad target never stalls the scan loop.
"""
from __future__ import annotations

import os
import urllib.error
import urllib.request


def probe_liveness(url: str, timeout: float) -> bool:
    """True iff ``url`` answers with an HTTP status < 500 within ``timeout``.

    A 200 is "alive"; 401/403/404 still prove the socket + process are up (we are
    not authorised, but it is *reachable*). Only a connection error / timeout / 5xx
    counts as down — that is the death-switch we want for L0.
    """
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status < 500
    except urllib.error.HTTPError as exc:           # got a response → reachable
        return exc.code < 500
    except Exception:  # noqa: BLE001 - any transport error == unreachable
        return False


def disk_usage(path: str) -> dict | None:
    """``statvfs`` a path → {used_pct, used_bytes, total_bytes}. None if missing."""
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    total = st.f_blocks * st.f_frsize
    if total <= 0:
        return None
    # use available-to-unprivileged (f_bavail) like df, so the % matches operators'
    free = st.f_bavail * st.f_frsize
    used = total - free
    return {
        "used_bytes": int(used),
        "total_bytes": int(total),
        "used_pct": round(100 * used / total),
    }
