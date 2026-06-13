"""函数级火焰图剖析器 (资源画像方案 §3.5 · 内置模式).

剖析线程以 100Hz 调 ``sys._current_frames()``,经线程注册表 (task_id → thread_ident)
**仅提取目标任务线程**的栈,折叠去重成火焰树 + speedscope 兼容采样剖析。要点:

  - 并发下归因天然精确:按线程取栈,与 CPU 的 ``RUSAGE_THREAD`` 同一逻辑,不受
    shared-N 模糊性影响 (方案 §3.5);
  - 原生代码 (polars/DuckDB) 执行期间采到的是 Python 边界帧 —— 对"贵在哪个因子
    函数/哪次取数"已是可行动粒度;
  - 运行 >20 分钟自动降至 50Hz;开销预算 ≤3% (验收红线 ≤5%);
  - 失败安全:剖析线程任何异常仅写日志,任务不受影响。

输出 speedscope 兼容 JSON (``shared.frames`` + sampled profile,样本保留时间戳) +
预构火焰树 (设计交付火焰图面板直接消费) + 实测平均采样率。
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from dataclasses import dataclass, field

from ..config import Settings, get_settings
from .resource_sampler import REGISTRY

_log = logging.getLogger("factor.resource_profiler")

_MAX_DEPTH = 64           # 设计按 40 层设计, 留余量; 超出截断 (极深递归防护)
_MAX_SAMPLES = 240_000    # 硬采样上限 (~40min@100Hz), 防内存膨胀


@dataclass
class _Node:
    name: str
    file: str
    samples: int = 0
    stage: str | None = None
    children: dict[str, "_Node"] = field(default_factory=dict)


class RunProfiler:
    """Per-run sampling profiler thread (方案 §3.5). Fail-safe by design."""

    def __init__(self, task_id: str, run_id: str, settings: Settings | None = None):
        self._s = settings or get_settings()
        self.task_id = task_id
        self.run_id = run_id
        self._hz = max(10, int(self._s.resource_profile_hz))
        self._downshift_after = float(self._s.resource_profile_downshift_after_s)
        self._downshift_hz = max(10, int(self._s.resource_profile_downshift_hz))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._root = _Node("<run>", "", 0)
        self._frame_idx: dict[tuple, int] = {}
        self._frames: list[dict] = []
        self._stacks: list[list[int]] = []
        self._times: list[float] = []
        self._sample_stages: list[int] = []
        self._stage_names: list[str] = []
        self._stage_idx: dict[str, int] = {}
        self._t0 = time.time()
        self._n = 0

    # ── lifecycle ────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread is not None:
            return
        self._t0 = time.time()
        self._thread = threading.Thread(
            target=self._loop, name=f"profiler-{self.run_id}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    # ── the loop ─────────────────────────────────────────────────────────
    def _loop(self) -> None:
        while not self._stop.is_set():
            t0 = time.time()
            try:
                self._sample_once()
            except Exception:  # noqa: BLE001 - 失败安全: 任务不受剖析异常影响
                _log.debug("profiler sample failed (non-fatal)", exc_info=True)
            if self._n >= _MAX_SAMPLES:
                break
            hz = (self._downshift_hz
                  if (time.time() - self._t0) > self._downshift_after else self._hz)
            self._stop.wait(max(0.0, 1.0 / hz - (time.time() - t0)))

    def _sample_once(self) -> None:
        ident = REGISTRY.thread_ident_of(self.task_id)
        if ident is None:
            return
        frame = sys._current_frames().get(ident)
        if frame is None:
            return
        stage = REGISTRY.stage_of(self.task_id)
        # walk leaf→root, then reverse to root→leaf
        stack: list[tuple] = []
        f, depth = frame, 0
        while f is not None and depth < _MAX_DEPTH:
            code = f.f_code
            stack.append((code.co_name, f"{_basename(code.co_filename)}:{f.f_lineno}"))
            f = f.f_back
            depth += 1
        stack.reverse()
        if not stack:
            return
        now_ms = (time.time() - self._t0) * 1000.0
        # fold into the tree, grouped by the active阶段 at the top level so the
        # 阶段 chips map 1:1 to a top-level child (设计交付 火焰图 + §7.1 三角联动);
        # every node under a stage carries that stage tag (drives color + frac).
        self._root.samples += 1
        skey = stage or "?"
        snode = self._root.children.get(skey)
        if snode is None:
            snode = _Node(skey, "", 0, stage=stage)
            self._root.children[skey] = snode
        snode.samples += 1
        cur = snode
        idxs: list[int] = []
        for name, file in stack:
            child = cur.children.get(name)
            if child is None:
                child = _Node(name, file, 0, stage=stage)
                cur.children[name] = child
            child.samples += 1
            child.stage = stage  # keep the whole subtree tagged with its stage
            cur = child
            idxs.append(self._frame_id(name, file))
        self._stacks.append(idxs)
        self._times.append(round(now_ms, 1))
        self._sample_stages.append(self._stage_id(stage))
        self._n += 1

    def _frame_id(self, name: str, file: str) -> int:
        key = (name, file)
        idx = self._frame_idx.get(key)
        if idx is None:
            idx = len(self._frames)
            self._frame_idx[key] = idx
            self._frames.append({"name": name, "file": file})
        return idx

    def _stage_id(self, stage: str | None) -> int:
        s = stage or "?"
        idx = self._stage_idx.get(s)
        if idx is None:
            idx = len(self._stage_names)
            self._stage_idx[s] = idx
            self._stage_names.append(s)
        return idx

    # ── serialization (speedscope + pre-built tree) ──────────────────────
    def measured_hz(self) -> float:
        elapsed = max(1e-6, time.time() - self._t0)
        return round(self._n / elapsed, 1)

    def has_samples(self) -> bool:
        return self._n > 0

    def to_artifact(self) -> dict:
        """speedscope-compatible JSON + UI tree + 实测采样率 (方案 §3.5)。"""
        tree = _tree_to_dict(self._root)
        artifact = {
            "$schema": "https://www.speedscope.app/file-format-schema.json",
            "name": self.run_id,
            "activeProfileIndex": 0,
            "shared": {"frames": self._frames},
            "profiles": [{
                "type": "sampled",
                "name": self.run_id,
                "unit": "none",
                "startValue": 0,
                "endValue": self._n,
                "samples": self._stacks,
                "weights": [1] * len(self._stacks),
            }],
            # custom (speedscope ignores unknown keys): per-sample time/stage so
            # the UI can do真实 time-selection filtering + 阶段 chips (§7.1).
            "sampleTimes": self._times,
            "sampleStages": self._sample_stages,
            "stageNames": self._stage_names,
            "tree": tree,
            "profiling": {
                "hz_nominal": self._hz,
                "hz_actual": self.measured_hz(),
                "samples": self._n,
            },
        }
        return _cap_size(artifact, self._s.resource_profile_max_mb)


def _basename(path: str) -> str:
    """Trim site-packages noise to a readable file ref (engine/rolling.py:131)."""
    if not path:
        return "?"
    parts = path.replace("\\", "/").split("/")
    if "site-packages" in parts:
        i = parts.index("site-packages")
        return "/".join(parts[i + 1:])[-48:] or parts[-1]
    return "/".join(parts[-2:])[-48:]


def _tree_to_dict(node: _Node) -> dict:
    return {
        "name": node.name, "file": node.file, "samples": node.samples,
        "stage": node.stage,
        "children": [_tree_to_dict(c) for c in node.children.values()],
    }


def _cap_size(artifact: dict, max_mb: int) -> dict:
    """硬上限 (方案 §3.5): 超限先丢逐样本时间轴, 再按步长抽稀 speedscope 样本。"""
    import json
    limit = max_mb * 1048576

    def _size() -> int:
        return len(json.dumps(artifact, separators=(",", ":")).encode())

    if _size() <= limit:
        return artifact
    # ① drop the per-sample time/stage axes (the UI tree still works)
    artifact["sampleTimes"] = []
    artifact["sampleStages"] = []
    if _size() <= limit:
        artifact["profiling"]["downsampled"] = "times_dropped"
        return artifact
    # ② stride-thin the speedscope samples until it fits
    prof = artifact["profiles"][0]
    stride = 2
    while _size() > limit and stride < 64:
        prof["samples"] = prof["samples"][::stride]
        prof["weights"] = [stride] * len(prof["samples"])
        stride *= 2
    artifact["profiling"]["downsampled"] = f"stride>{stride}"
    return artifact
