"""K线基座模型：骨干 + 并行分组分类头 + 分位数预测头（报告4.2）.

模型族配置（表4.4 / 表4.5）：
  compact：8层 / d=448 / ff=1344 / 7头，约16M参数；
           dropout(ffn/resid/attn/token)=0.15/0.15/0.10/0.05，lr=8e-4，wd=0.02
  base   ：10层 / d=640 / ff=1920 / 10头，约41M参数；
           dropout=0.10/0.10/0.05/0.00，lr=5e-4，wd=0.05
两档最大上下文长度统一为384个时间步。

训练目标 = 四组分类NLL之和 + 0.5 × 分位数回归损失（4.2.1/4.2.3）；
训练阶段以自采样策略缓解曝光偏差（部分位置以模型自身预测为条件）。
推理为确定性单次前向：收益预测与因子构建不依赖随机采样。
"""

from __future__ import annotations

import torch
import torch.nn as nn

from .backbone import KBarBackbone
from .heads import GroupHeads, QuantileHead
from .losses import grouped_ce_loss, pinball_loss

FSQ_LEVELS = (8, 8, 8, 5)
QUANTILES = (0.1, 0.5, 0.9)
MAX_CONTEXT = 384

# 表4.4 / 表4.5：模型族结构与训练超参数
MODEL_FAMILY: dict[str, dict] = {
    "compact": {
        "n_layers": 8,
        "d_model": 448,
        "ff_dim": 1344,
        "n_heads": 7,
        "ffn_dropout_p": 0.15,
        "resid_dropout_p": 0.15,
        "attn_dropout_p": 0.10,
        "token_dropout_p": 0.05,
        "lr": 8e-4,
        "weight_decay": 0.02,
    },
    "base": {
        "n_layers": 10,
        "d_model": 640,
        "ff_dim": 1920,
        "n_heads": 10,
        "ffn_dropout_p": 0.10,
        "resid_dropout_p": 0.10,
        "attn_dropout_p": 0.05,
        "token_dropout_p": 0.00,
        "lr": 5e-4,
        "weight_decay": 0.05,
    },
}


class KBarFoundationModel(nn.Module):
    def __init__(
        self,
        levels: tuple[int, ...] = FSQ_LEVELS,
        quantiles: tuple[float, ...] = QUANTILES,
        quantile_loss_weight: float = 0.5,
        max_context: int = MAX_CONTEXT,
        grad_checkpointing: bool = False,
        **backbone_kw,
    ) -> None:
        super().__init__()
        self.levels = tuple(levels)
        self.quantiles = tuple(quantiles)
        self.quantile_loss_weight = quantile_loss_weight
        self.backbone = KBarBackbone(
            levels=self.levels,
            max_context=max_context,
            grad_checkpointing=grad_checkpointing,
            **backbone_kw,
        )
        d_model = self.backbone.d_model
        self.group_heads = GroupHeads(d_model, self.levels)
        self.quantile_head = QuantileHead(d_model, self.quantiles)

    # ------------------------------------------------------------------ forward
    def forward(
        self, codes: torch.Tensor, stamp: torch.Tensor | None = None
    ) -> tuple[list[torch.Tensor], torch.Tensor]:
        """codes: (B, T, G) -> (G个下一步logits, (B, T, Q)分位数预测)."""
        h = self.backbone(codes, stamp)
        return self.group_heads(h), self.quantile_head(h)

    # --------------------------------------------------------------------- loss
    def compute_loss(
        self,
        codes: torch.Tensor,
        stamp: torch.Tensor | None,
        y_ret: torch.Tensor,
        y_mask: torch.Tensor,
        self_sampling_p: float = 0.0,
    ) -> tuple[torch.Tensor, dict]:
        """联合训练目标（报告4.2.1）.

        codes: (B, T, G)；y_ret / y_mask: (B, T) 未来H日累计对数收益及掩码。
        自采样（self_sampling_p>0）：先以教师输入前向一次得到模型自身
        预测，再以该预测按位置概率替换输入子标记（首位置除外），令训练
        与推理的分布更趋一致，缓解曝光偏差。
        """
        inputs = codes
        if self_sampling_p > 0.0:
            inputs = self._self_sampling_mix(codes, stamp, self_sampling_p)

        group_logits, pred_q = self.forward(inputs, stamp)

        # 下一步子标记预测：位置t的logits对齐位置t+1的真实子标记
        shifted_logits = [lg[:, :-1] for lg in group_logits]
        ce = grouped_ce_loss(shifted_logits, codes[:, 1:])

        # 分位数收益预测：位置t的隐状态预测自t起的未来H日收益
        pin = pinball_loss(pred_q, y_ret, self.quantiles, mask=y_mask)

        total = ce + self.quantile_loss_weight * pin
        return total, {"ce": float(ce.detach()), "pinball": float(pin.detach())}

    @torch.no_grad()
    def _self_sampling_mix(
        self, codes: torch.Tensor, stamp: torch.Tensor | None, p: float
    ) -> torch.Tensor:
        """自采样输入构造：位置t的输入以概率p替换为模型对位置t的自身预测."""
        group_logits, _ = self.forward(codes, stamp)
        mixed = codes.clone()
        b, t, _ = codes.shape
        replace = torch.rand(b, t, device=codes.device) < p
        replace[:, 0] = False                          # 首位置无历史预测可用
        for g, logits in enumerate(group_logits):
            pred = logits.argmax(dim=-1)               # (B, T)，位置t-1处为对位置t的预测
            pred_for_pos = torch.roll(pred, shifts=1, dims=1)
            mixed[..., g] = torch.where(replace, pred_for_pos, mixed[..., g])
        return mixed

    # ---------------------------------------------------------------- inference
    @torch.no_grad()
    def predict_quantiles(
        self, codes: torch.Tensor, stamp: torch.Tensor | None = None
    ) -> torch.Tensor:
        """确定性单次前向（报告4.2.3，表4.6）.

        codes: (B, T, G) 回看窗口的子标记 -> (B, Q) 窗口末端对未来H日
        累计对数收益的分位数预测 [q0.1, q0.5, q0.9]。
        """
        was_training = self.training
        self.eval()
        _, pred_q = self.forward(codes, stamp)
        if was_training:
            self.train()
        return pred_q[:, -1, :]

    @property
    def num_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters())


def build_model(
    size: str = "base",
    grad_checkpointing: bool = False,
    quantile_loss_weight: float = 0.5,
) -> KBarFoundationModel:
    """按模型族名称构建（'compact' / 'base'）."""
    if size not in MODEL_FAMILY:
        raise KeyError(f"未知模型规格: {size!r}，应为 {sorted(MODEL_FAMILY)}")
    cfg = {k: v for k, v in MODEL_FAMILY[size].items() if k not in ("lr", "weight_decay")}
    return KBarFoundationModel(
        levels=FSQ_LEVELS,
        quantiles=QUANTILES,
        quantile_loss_weight=quantile_loss_weight,
        max_context=MAX_CONTEXT,
        grad_checkpointing=grad_checkpointing,
        **cfg,
    )
