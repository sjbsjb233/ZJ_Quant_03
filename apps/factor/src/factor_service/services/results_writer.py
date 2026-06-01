"""Persist evaluation + correlation results to disk."""
from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any

import polars as pl

from ..config import get_settings


def _ensure_factor_dir(factor_id: str) -> str:
    s = get_settings()
    d = os.path.join(s.evaluations_dir, factor_id)
    os.makedirs(d, exist_ok=True)
    return d


def write_run_results(run_id: str, factor_id: str, result: dict[str, Any]) -> None:
    """Write the IC series + layered series as parquet files."""
    d = _ensure_factor_dir(factor_id)
    ic: pl.DataFrame | None = result.get("ic_series")
    if isinstance(ic, pl.DataFrame) and ic.height > 0:
        # Polars's Date columns are stored natively in Parquet -> safe.
        ic.write_parquet(os.path.join(d, f"ic_{run_id}.parquet"))
    layers = result.get("layers") or {}
    # layers is a dict with 'layers' (list of rows). Serialize the rows.
    rows = layers.get("layers") if isinstance(layers, dict) else None
    if rows:
        try:
            df = pl.DataFrame(rows)
            df.write_parquet(os.path.join(d, f"layer_{run_id}.parquet"))
        except Exception:  # noqa: BLE001
            with open(os.path.join(d, f"layer_{run_id}.json"), "w", encoding="utf-8") as f:
                json.dump(rows, f, default=str)

    # Decay summary (per-horizon IC stats) — small, keep as json
    ic_by_h = result.get("ic_by_horizon") or {}
    if ic_by_h:
        with open(os.path.join(d, f"decay_{run_id}.json"), "w", encoding="utf-8") as f:
            json.dump(ic_by_h, f, default=_json_default)
    # Turnover series
    turn = result.get("turnover") or {}
    if turn.get("series"):
        with open(os.path.join(d, f"turnover_{run_id}.json"), "w", encoding="utf-8") as f:
            json.dump(turn, f, default=_json_default)


def _json_default(x):
    if isinstance(x, (dt.date, dt.datetime)):
        return x.isoformat()
    raise TypeError(f"can't serialize {type(x).__name__}")


def write_degradations(
    run_id: str,
    *,
    status: str,
    level: str,
    blocked: list[dict] | None = None,
    events: list[dict] | None = None,
) -> str:
    """Persist the degradation台账 artifact (设计 §8.3): {run_id}.degradations.json.

    Lives next to the run log under runs_dir so the detail view can render the
    full structured report (and the failed-by-preflight 阻断详情).
    """
    s = get_settings()
    os.makedirs(s.runs_dir, exist_ok=True)
    path = os.path.join(s.runs_dir, f"{run_id}.degradations.json")
    payload = {
        "run_id": run_id,
        "status": status,
        "level": level,
        "blocked": blocked or [],
        "events": events or [],
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, default=_json_default, ensure_ascii=False)
    return path


def read_degradations(run_id: str) -> dict[str, Any] | None:
    s = get_settings()
    path = os.path.join(s.runs_dir, f"{run_id}.degradations.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_run_trace(run_id: str, payload: dict) -> str:
    """Persist the 运行透视 artifact (设计 §2.4): ``{run_id}.trace.json``.

    Lives next to ``{run_id}.degradations.json`` / ``{run_id}.log`` under
    runs_dir. Carries the阶段流水线 + 前置检查全量 + 股票池漏斗 + 逐调仓日过程.
    """
    s = get_settings()
    os.makedirs(s.runs_dir, exist_ok=True)
    path = os.path.join(s.runs_dir, f"{run_id}.trace.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, default=_json_default, ensure_ascii=False)
    return path


def read_run_trace(run_id: str) -> dict[str, Any] | None:
    s = get_settings()
    path = os.path.join(s.runs_dir, f"{run_id}.trace.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_correlation_cache(task_id: str, result: dict[str, Any]) -> None:
    s = get_settings()
    os.makedirs(s.correlations_dir, exist_ok=True)
    path = os.path.join(s.correlations_dir, f"{task_id}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, default=_json_default)


def read_ic_series(factor_id: str, run_id: str) -> list[dict[str, Any]]:
    p = os.path.join(get_settings().evaluations_dir, factor_id, f"ic_{run_id}.parquet")
    if not os.path.exists(p):
        return []
    df = pl.read_parquet(p)
    return df.to_dicts()


def read_layers(factor_id: str, run_id: str) -> list[dict[str, Any]]:
    p_pq = os.path.join(
        get_settings().evaluations_dir, factor_id, f"layer_{run_id}.parquet"
    )
    if os.path.exists(p_pq):
        return pl.read_parquet(p_pq).to_dicts()
    p_json = os.path.join(
        get_settings().evaluations_dir, factor_id, f"layer_{run_id}.json"
    )
    if os.path.exists(p_json):
        with open(p_json, encoding="utf-8") as f:
            return json.load(f)
    return []


def read_decay(factor_id: str, run_id: str) -> dict[str, Any]:
    p = os.path.join(
        get_settings().evaluations_dir, factor_id, f"decay_{run_id}.json"
    )
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def read_turnover(factor_id: str, run_id: str) -> dict[str, Any]:
    p = os.path.join(
        get_settings().evaluations_dir, factor_id, f"turnover_{run_id}.json"
    )
    if not os.path.exists(p):
        return {}
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def read_correlation(task_id: str) -> dict[str, Any] | None:
    p = os.path.join(get_settings().correlations_dir, f"{task_id}.json")
    if not os.path.exists(p):
        return None
    with open(p, encoding="utf-8") as f:
        return json.load(f)
