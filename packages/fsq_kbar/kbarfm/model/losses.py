"""训练目标（报告4.1.2 / 4.2.1 / 4.2.3）.

标记化器：结构约束重构损失（四项加权）
  L = λ1·数值重构(Huber) + λ2·收益保持 + λ3·量额对数域重构
      + λ4·K线结构一致性铰链项
  初始权重 λ1=1.0、λ2=0.40、λ3=0.25、λ4=0.15。

基座模型：四组子标记分类的负对数似然之和，叠加分位数预测头的
分位数回归（pinball）损失（联合权重0.5）。
"""

from __future__ import annotations

import torch
import torch.nn.functional as F

from ..data.fields import AMOUNT, CLOSE, HIGH, LOW, OPEN, VOLUME
from ..data.normalize import EPS


def _denorm(x: torch.Tensor, mean: torch.Tensor, std: torch.Tensor) -> torch.Tensor:
    """逆z-score（mean/std: (B, 6)），恢复原始量纲."""
    return x * (std.unsqueeze(1) + EPS) + mean.unsqueeze(1)


def structure_recon_loss(
    x_hat: torch.Tensor,
    x: torch.Tensor,
    mean: torch.Tensor,
    std: torch.Tensor,
    lambda_huber: float = 1.0,
    lambda_ret: float = 0.40,
    lambda_volamt: float = 0.25,
    lambda_struct: float = 0.15,
    huber_delta: float = 1.0,
) -> tuple[torch.Tensor, dict]:
    """结构约束重构损失（报告4.1.2）.

    x_hat / x: (B, T, 6) 标准化空间的重构与真值；
    mean / std: (B, 6) 各窗口逐维统计量，用于恢复原始量纲计算
    收益、量额与形态约束（这些约束在标准化空间中不保持）。
    """
    # (1) 数值重构：标准化空间逐维Huber，对极端值更稳健
    l_huber = F.huber_loss(x_hat, x, delta=huber_delta)

    raw_hat = _denorm(x_hat, mean, std)
    raw = _denorm(x, mean, std)

    # (2) 收益保持：重构收盘价的对数收益与真实值一致，
    #     保证离散化不扭曲下游最关心的收益信息
    close_hat = raw_hat[:, :, CLOSE].clamp(min=1e-6)
    close = raw[:, :, CLOSE].clamp(min=1e-6)
    ret_hat = torch.log(close_hat[:, 1:] / close_hat[:, :-1])
    ret = torch.log(close[:, 1:] / close[:, :-1])
    l_ret = F.huber_loss(ret_hat, ret, delta=huber_delta)

    # (3) 量额对数域重构：适配量纲跨度大、右偏显著的分布特性
    va_hat = raw_hat[:, :, [VOLUME, AMOUNT]].clamp(min=0.0)
    va = raw[:, :, [VOLUME, AMOUNT]].clamp(min=0.0)
    l_volamt = F.huber_loss(torch.log1p(va_hat), torch.log1p(va), delta=huber_delta)

    # (4) K线结构一致性：铰链项惩罚"最高价低于开收盘、最低价高于开收盘"
    o, h = raw_hat[:, :, OPEN], raw_hat[:, :, HIGH]
    l_, c = raw_hat[:, :, LOW], raw_hat[:, :, CLOSE]
    body_hi = torch.maximum(o, c)
    body_lo = torch.minimum(o, c)
    scale = raw[:, :, CLOSE].abs().clamp(min=1e-6)      # 以真实收盘价为尺度归一，避免高价股主导
    l_struct = (F.relu(body_hi - h) / scale).mean() + (F.relu(l_ - body_lo) / scale).mean()

    total = (
        lambda_huber * l_huber
        + lambda_ret * l_ret
        + lambda_volamt * l_volamt
        + lambda_struct * l_struct
    )
    parts = {
        "recon_huber": float(l_huber.detach()),
        "recon_ret": float(l_ret.detach()),
        "recon_volamt": float(l_volamt.detach()),
        "recon_struct": float(l_struct.detach()),
    }
    return total, parts


def grouped_ce_loss(
    group_logits: list[torch.Tensor],
    codes: torch.Tensor,
    mask: torch.Tensor | None = None,
) -> torch.Tensor:
    """四组子标记分类的负对数似然之和（报告4.2.1）.

    group_logits: G个 (B, T, L_g)；codes: (B, T, G)；mask: (B, T) 或 None。
    """
    total = 0.0
    for g, logits in enumerate(group_logits):
        ce = F.cross_entropy(
            logits.reshape(-1, logits.shape[-1]),
            codes[..., g].reshape(-1),
            reduction="none",
        )
        if mask is not None:
            m = mask.reshape(-1)
            ce = (ce * m).sum() / m.sum().clamp(min=1.0)
        else:
            ce = ce.mean()
        total = total + ce
    return total


def pinball_loss(
    pred_q: torch.Tensor,
    target: torch.Tensor,
    quantiles: tuple[float, ...],
    mask: torch.Tensor | None = None,
) -> torch.Tensor:
    """分位数回归损失（报告4.2.3）.

    pred_q: (B, T, Q)；target: (B, T)；quantiles 与最后一维对应。
    """
    target = target.unsqueeze(-1)
    q = torch.tensor(quantiles, device=pred_q.device, dtype=pred_q.dtype).view(1, 1, -1)
    diff = target - pred_q
    loss = torch.maximum(q * diff, (q - 1.0) * diff)     # (B, T, Q)
    loss = loss.mean(dim=-1)                              # 各分位点平均
    if mask is not None:
        return (loss * mask).sum() / mask.sum().clamp(min=1.0)
    return loss.mean()
