"""预测头（4.2.1 / 4.2.3）.

GroupHeads：四组子标记的并行线性分类头，共享同一历史隐状态，
  输出类别总数仅为各维水平数之和（8+8+8+5=29），单时间步生成
  深度不随组数增加。

QuantileHead：直接分位数收益预测头，一次确定性前向同时输出未来
  H日累计收益的多个分位数；以"最低分位 + softplus增量累加"的
  参数化保证 q0.1 <= q0.5 <= q0.9 的单调性（无需蒙特卡洛采样）。
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


class GroupHeads(nn.Module):
    def __init__(self, d_model: int, levels: tuple[int, ...]) -> None:
        super().__init__()
        self.levels = tuple(levels)
        self.heads = nn.ModuleList([nn.Linear(d_model, L) for L in levels])

    def forward(self, h: torch.Tensor) -> list[torch.Tensor]:
        """h: (B, T, d) -> G个logits，第g个形状 (B, T, L_g)."""
        return [head(h) for head in self.heads]


class QuantileHead(nn.Module):
    def __init__(self, d_model: int, quantiles: tuple[float, ...] = (0.1, 0.5, 0.9)) -> None:
        super().__init__()
        qs = sorted(float(q) for q in quantiles)
        assert qs == sorted(set(qs)) and all(0.0 < q < 1.0 for q in qs)
        self.quantiles = tuple(qs)
        self.base = nn.Linear(d_model, 1)                      # 最低分位
        self.deltas = nn.Linear(d_model, len(qs) - 1)          # 非负增量

    def forward(self, h: torch.Tensor) -> torch.Tensor:
        """h: (B, T, d) -> (B, T, Q) 单调递增的分位数预测（对数收益口径）."""
        lo = self.base(h)
        inc = F.softplus(self.deltas(h))
        return torch.cat([lo, lo + torch.cumsum(inc, dim=-1)], dim=-1)
