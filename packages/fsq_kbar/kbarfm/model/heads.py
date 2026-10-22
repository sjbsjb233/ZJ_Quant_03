"""预测头（报告4.2.1 / 4.2.3）.

GroupHeads：四组子标记的并行线性分类头。四组共享同一主干输出
  的历史隐状态，各自独立并行输出预测分布——"在全状态空间上做
  一次大词表预测"被等价拆解为"在G个小类别空间上并行做G次
  分类"，输出类别总数仅为各维水平数之和（8+8+8+5=29），单时间
  步生成深度不随组数增加。

QuantileHead：直接分位数收益预测头。以历史隐状态为输入，一次
  确定性前向同时输出未来H日累计收益的10%/50%/90%分位数；以
  "最低分位 + softplus非负增量累加"的参数化保证
  q0.1 <= q0.5 <= q0.9 的单调性，无需蒙特卡洛采样。
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F


class GroupHeads(nn.Module):
    def __init__(self, d_model: int, levels: tuple[int, ...]) -> None:
        super().__init__()
        self.levels = tuple(levels)
        self.heads = nn.ModuleList(nn.Linear(d_model, L) for L in self.levels)

    def forward(self, h: torch.Tensor) -> list[torch.Tensor]:
        """h: (B, T, d) -> G个logits，第g个形状 (B, T, L_g)."""
        return [head(h) for head in self.heads]


class QuantileHead(nn.Module):
    def __init__(
        self, d_model: int, quantiles: tuple[float, ...] = (0.1, 0.5, 0.9)
    ) -> None:
        super().__init__()
        qs = tuple(sorted(float(q) for q in quantiles))
        assert len(qs) == len(set(qs)) and all(0.0 < q < 1.0 for q in qs)
        self.quantiles = qs
        self.base = nn.Linear(d_model, 1)                # 最低分位
        self.deltas = nn.Linear(d_model, len(qs) - 1)    # 相邻分位的非负增量

    def forward(self, h: torch.Tensor) -> torch.Tensor:
        """h: (B, T, d) -> (B, T, Q) 单调递增的分位数预测（对数收益口径）."""
        lo = self.base(h)
        inc = F.softplus(self.deltas(h))
        return torch.cat([lo, lo + torch.cumsum(inc, dim=-1)], dim=-1)
