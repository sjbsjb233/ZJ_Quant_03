"""训练目标（4.1.2 / 4.2.1 / 4.2.3）."""

from __future__ import annotations

import torch
import torch.nn.functional as F


def structure_constrained_recon_loss(
    recon: torch.Tensor,
    target: torch.Tensor,
    mean: torch.Tensor,
    std: torch.Tensor,
    w_huber: float = 1.0,
    w_return: float = 0.40,
    w_volume: float = 0.25,
    w_structure: float = 0.15,
) -> tuple[torch.Tensor, dict[str, float]]:
    """结构约束重构损失（4.1.2），四项加权：

    ① 数值重构（Huber）：重构K线与真实K线的逐维偏差，对极端值稳健；
    ② 收益保持：重构收盘价的对数收益与真实值一致，保证离散化不扭曲
       下游最关心的收益信息；
    ③ 量额重构：对数域约束成交量/成交额精度，适配右偏厚尾分布；
    ④ K线结构一致性：铰链项惩罚"最高价低于开收盘、最低价高于开收盘"
       等违反K线物理结构的重构。
    ②③④在还原到原始量纲后计算（z-score逐维仿射不保序跨维大小关系）。

    recon/target: (B, T, 6) 标准化域；mean/std: (B, 6)。
    """
    huber = F.smooth_l1_loss(recon, target)

    mean = mean.unsqueeze(1)  # (B,1,6)
    std = std.unsqueeze(1)
    de = recon * std + mean   # 反标准化
    dt = target * std + mean

    # ② 收益保持（对数收益，收盘价第3维）
    c_hat = torch.clamp(de[..., 3], min=1e-6)
    c_true = torch.clamp(dt[..., 3], min=1e-6)
    r_hat = torch.log(c_hat[:, 1:]) - torch.log(c_hat[:, :-1])
    r_true = torch.log(c_true[:, 1:]) - torch.log(c_true[:, :-1])
    ret = F.smooth_l1_loss(r_hat, r_true)

    # ③ 量额重构（log1p域）
    va_hat = torch.log1p(torch.clamp(de[..., 4:6], min=0.0))
    va_true = torch.log1p(torch.clamp(dt[..., 4:6], min=0.0))
    volamt = F.smooth_l1_loss(va_hat, va_true)

    # ④ K线结构一致性（铰链）
    body_hi = torch.maximum(de[..., 0], de[..., 3])
    body_lo = torch.minimum(de[..., 0], de[..., 3])
    struct = (
        F.relu(body_hi - de[..., 1]).mean() + F.relu(de[..., 2] - body_lo).mean()
    )

    total = w_huber * huber + w_return * ret + w_volume * volamt + w_structure * struct
    parts = {
        "huber": huber.item(),
        "return": ret.item(),
        "volume": volamt.item(),
        "structure": struct.item(),
    }
    return total, parts


def grouped_cross_entropy(
    logits_list: list[torch.Tensor], targets: torch.Tensor
) -> torch.Tensor:
    """四组分类的负对数似然之和（4.2.1）.

    联合分布按组条件独立近似分解：sum_g CE(logits_g, target_g)。
    logits_list[g]: (B, T, L_g)；targets: (B, T, G)。
    """
    loss = 0.0
    for g, logits in enumerate(logits_list):
        loss = loss + F.cross_entropy(
            logits.reshape(-1, logits.size(-1)), targets[..., g].reshape(-1)
        )
    return loss


def pinball_loss(
    pred: torch.Tensor,
    target: torch.Tensor,
    quantiles: tuple[float, ...],
    mask: torch.Tensor | None = None,
) -> torch.Tensor:
    """分位数回归（pinball）损失（4.2.3）.

    pred: (B, T, Q)；target: (B, T)；mask: (B, T) 1=有效标签。
    """
    q = torch.tensor(quantiles, device=pred.device, dtype=pred.dtype)
    diff = target.unsqueeze(-1) - pred                      # (B, T, Q)
    loss = torch.maximum(q * diff, (q - 1.0) * diff)
    if mask is not None:
        loss = loss * mask.unsqueeze(-1)
        denom = mask.sum().clamp(min=1.0) * len(quantiles)
        return loss.sum() / denom
    return loss.mean()
