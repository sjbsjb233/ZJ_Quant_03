"""优化器与学习率调度（报告4.2.2，表4.5）.

AdamW（解耦权重衰减），配合余弦学习率调度与线性warmup：
前1500步学习率由峰值的10%线性升起，随后沿余弦曲线衰减。
norm、bias与嵌入层不施加权重衰减。
"""

from __future__ import annotations

import math

import torch
import torch.nn as nn


def build_optimizer(
    model: nn.Module,
    lr: float,
    weight_decay: float,
    betas: tuple[float, float] = (0.9, 0.95),
) -> torch.optim.AdamW:
    decay_params, no_decay_params = [], []
    for name, param in model.named_parameters():
        if not param.requires_grad:
            continue
        if param.ndim <= 1 or "embed" in name.lower() or "norm" in name.lower():
            no_decay_params.append(param)
        else:
            decay_params.append(param)
    groups = [
        {"params": decay_params, "weight_decay": weight_decay},
        {"params": no_decay_params, "weight_decay": 0.0},
    ]
    return torch.optim.AdamW(groups, lr=lr, betas=betas)


class CosineWarmupScheduler:
    """线性warmup（自峰值10%起）+ 余弦衰减（至峰值的min_lr_ratio）."""

    def __init__(
        self,
        optimizer: torch.optim.Optimizer,
        peak_lr: float,
        total_steps: int,
        warmup_steps: int = 1500,
        warmup_start_ratio: float = 0.10,
        min_lr_ratio: float = 0.05,
    ) -> None:
        self.optimizer = optimizer
        self.peak_lr = peak_lr
        self.total_steps = max(total_steps, 1)
        self.warmup_steps = warmup_steps
        self.warmup_start_ratio = warmup_start_ratio
        self.min_lr_ratio = min_lr_ratio
        self.step_num = 0

    def lr_at(self, step: int) -> float:
        if step < self.warmup_steps:
            frac = step / max(self.warmup_steps, 1)
            ratio = self.warmup_start_ratio + (1.0 - self.warmup_start_ratio) * frac
            return self.peak_lr * ratio
        progress = (step - self.warmup_steps) / max(self.total_steps - self.warmup_steps, 1)
        progress = min(progress, 1.0)
        cos = 0.5 * (1.0 + math.cos(math.pi * progress))
        return self.peak_lr * (self.min_lr_ratio + (1.0 - self.min_lr_ratio) * cos)

    def step(self) -> float:
        self.step_num += 1
        lr = self.lr_at(self.step_num)
        for group in self.optimizer.param_groups:
            group["lr"] = lr
        return lr

    def state_dict(self) -> dict:
        return {"step_num": self.step_num}

    def load_state_dict(self, state: dict) -> None:
        self.step_num = int(state.get("step_num", 0))
