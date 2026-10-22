"""有限标量量化 FSQ（4.1.1）.

将潜向量经线性投影压缩到G个标量维度，每维经tanh压缩至(-1, 1)后
均匀量化到该维预设的L_g个固定水平；离散状态由各维取值的笛卡尔积
隐式构成（本策略 G=4、水平[8,8,8,5]，隐式状态数2560），无可学习
码本、无最近邻查找，从机制上规避码本坍塌；取整操作的梯度以
直通估计器（straight-through estimator）近似传递。
"""

from __future__ import annotations

import math

import torch
import torch.nn as nn


class FSQ(nn.Module):
    def __init__(self, levels: tuple[int, ...] = (8, 8, 8, 5)) -> None:
        super().__init__()
        self.levels = tuple(int(v) for v in levels)
        self.num_groups = len(self.levels)          # G
        self.num_states = math.prod(self.levels)    # 隐式状态数
        self.register_buffer(
            "_levels", torch.tensor(self.levels, dtype=torch.float32), persistent=False
        )

    def forward(self, z: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """z: (..., G) 连续标量 -> (zq 直通量化值 in [-1,1], codes 离散索引).

        每维L个水平均匀铺设在[-1, 1]：g_k = 2k/(L-1) - 1, k∈{0..L-1}。
        """
        h = torch.tanh(z)                            # 有界激活，极端样本被映射到边界水平附近
        L = self._levels.to(h.dtype)
        k = torch.round((h + 1.0) * (L - 1.0) / 2.0)
        k = torch.clamp(k, min=torch.zeros_like(L), max=L - 1.0)
        zq = 2.0 * k / (L - 1.0) - 1.0
        zq = h + (zq - h).detach()                   # 直通估计器
        return zq, k.long()

    def codes_to_values(self, codes: torch.Tensor) -> torch.Tensor:
        """离散索引 -> 对应的量化标量值（解码路径）."""
        L = self._levels.to(torch.float32)
        return 2.0 * codes.float() / (L - 1.0) - 1.0

    def joint_index(self, codes: torch.Tensor) -> torch.Tensor:
        """四组子标记 -> 联合状态编号 ∈ [0, 2560)（用于状态利用统计，表4.3）."""
        idx = torch.zeros_like(codes[..., 0])
        for g, L in enumerate(self.levels):
            idx = idx * L + codes[..., g]
        return idx


@torch.no_grad()
def fsq_usage_stats(codes: torch.Tensor, levels: tuple[int, ...]) -> dict:
    """离散状态利用统计（4.1.3，表4.3口径）.

    codes: (N, G) 语料上收集的子标记。
    返回：单维水平覆盖率、归一化信息熵（4维平均）、联合状态覆盖率、
         前10%高频状态频率占比。
    """
    codes = codes.reshape(-1, len(levels))
    per_dim_cov, per_dim_ent = [], []
    for g, L in enumerate(levels):
        counts = torch.bincount(codes[:, g], minlength=L).float()
        p = counts / counts.sum()
        per_dim_cov.append((counts > 0).float().mean().item())
        ent = -(p[p > 0] * p[p > 0].log()).sum().item() / math.log(L)
        per_dim_ent.append(ent)

    joint = torch.zeros_like(codes[:, 0])
    for g, L in enumerate(levels):
        joint = joint * L + codes[:, g]
    num_states = math.prod(levels)
    jc = torch.bincount(joint, minlength=num_states).float()
    p = jc / jc.sum()
    top_k = max(1, int(0.10 * num_states))
    top_share = torch.sort(p, descending=True).values[:top_k].sum().item()

    return {
        "per_dim_coverage": float(sum(per_dim_cov) / len(per_dim_cov)),
        "normalized_entropy": float(sum(per_dim_ent) / len(per_dim_ent)),
        "joint_coverage": float((jc > 0).float().mean().item()),
        "top10pct_share": float(top_share),
    }
