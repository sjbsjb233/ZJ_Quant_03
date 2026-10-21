"""优化器与学习率调度（4.2.2，表4.5）.

AdamW（Loshchilov & Hutter, 2017）+ 线性warmup（前1500步由峰值10%
线性升起）+ 余弦衰减。权重衰减仅作用于二维以上权重矩阵，
偏置、归一化参数与嵌入不衰减。
"""

from __future__ import annotations

import math

import torch
from torch.optim.lr_scheduler import LambdaLR


def configure_adamw(
    model: torch.nn.Module,
    lr: float,
    weight_decay: float,
    betas: tuple[float, float] = (0.9, 0.95),
) -> torch.optim.AdamW:
    decay, no_decay = [], []
    for name, p in model.named_parameters():
        if not p.requires_grad:
            continue
        if p.ndim < 2 or "emb" in name.lower() or name.endswith(".bias"):
            no_decay.append(p)
        else:
            decay.append(p)
    groups = [
        {"params": decay, "weight_decay": weight_decay},
        {"params": no_decay, "weight_decay": 0.0},
    ]
    return torch.optim.AdamW(groups, lr=lr, betas=betas)


def make_warmup_cosine(
    optimizer: torch.optim.Optimizer,
    warmup_steps: int,
    total_steps: int,
    start_ratio: float = 0.10,
    min_ratio: float = 0.10,
) -> LambdaLR:
    def lr_lambda(step: int) -> float:
        if step < warmup_steps:
            return start_ratio + (1.0 - start_ratio) * step / max(1, warmup_steps)
        prog = (step - warmup_steps) / max(1, total_steps - warmup_steps)
        prog = min(prog, 1.0)
        return min_ratio + (1.0 - min_ratio) * 0.5 * (1.0 + math.cos(math.pi * prog))

    return LambdaLR(optimizer, lr_lambda)
