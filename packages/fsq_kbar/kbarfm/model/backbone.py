"""并行分组自回归骨干（报告4.2.1，表4.4）.

纯解码器Transformer：因果注意力（下三角掩码）保证预测未来时只
依赖历史信息；RoPE表征相对位置关系；Pre-LN配合RMSNorm提升
深层训练稳定性。

多组标记化在建模端的呼应：在给定历史条件下，单根K线四组子
标记的联合分布被近似分解为四个组各自条件分布的乘积——四组
共享同一主干输出的历史隐状态，无需组间顺序依赖与交叉注意力
融合（区别于层级条件解码类方案），单
时间步仅需一次主干前向。
"""

from __future__ import annotations

import torch
import torch.nn as nn
from torch.utils.checkpoint import checkpoint

from .layers import RMSNorm, TemporalEmbedding, TransformerBlock


class GroupEmbedding(nn.Module):
    """四组子标记的并行嵌入：各组独立查表后求和融合."""

    def __init__(self, levels: tuple[int, ...], d_model: int) -> None:
        super().__init__()
        self.levels = tuple(levels)
        self.d_model = d_model
        self.embeds = nn.ModuleList(nn.Embedding(L, d_model) for L in self.levels)
        for emb in self.embeds:
            nn.init.normal_(emb.weight, mean=0.0, std=d_model**-0.5)

    def forward(self, codes: torch.Tensor) -> torch.Tensor:
        """codes: (B, T, G) -> (B, T, d_model)."""
        out = 0
        for g, emb in enumerate(self.embeds):
            out = out + emb(codes[..., g])
        return out * (self.d_model**0.5 / len(self.embeds))


class KBarBackbone(nn.Module):
    def __init__(
        self,
        levels: tuple[int, ...] = (8, 8, 8, 5),
        n_layers: int = 10,
        d_model: int = 640,
        n_heads: int = 10,
        ff_dim: int = 1920,
        ffn_dropout_p: float = 0.10,
        attn_dropout_p: float = 0.05,
        resid_dropout_p: float = 0.10,
        token_dropout_p: float = 0.0,
        max_context: int = 384,
        learn_temporal: bool = False,
        grad_checkpointing: bool = False,
    ) -> None:
        super().__init__()
        self.levels = tuple(levels)
        self.d_model = d_model
        self.max_context = max_context
        self.grad_checkpointing = grad_checkpointing

        self.embedding = GroupEmbedding(self.levels, d_model)
        self.time_emb = TemporalEmbedding(d_model, learnable=learn_temporal)
        self.token_drop = nn.Dropout(token_dropout_p)
        self.blocks = nn.ModuleList(
            TransformerBlock(
                d_model, n_heads, ff_dim, ffn_dropout_p, attn_dropout_p, resid_dropout_p
            )
            for _ in range(n_layers)
        )
        self.norm = RMSNorm(d_model)
        self.apply(self._init_weights)

    def _init_weights(self, module: nn.Module) -> None:
        if isinstance(module, nn.Linear):
            nn.init.xavier_normal_(module.weight)
            if module.bias is not None:
                nn.init.zeros_(module.bias)

    def forward(self, codes: torch.Tensor, stamp: torch.Tensor | None = None) -> torch.Tensor:
        """codes: (B, T, G)；stamp: (B, T, 5) -> 历史隐状态 (B, T, d_model)."""
        if codes.shape[1] > self.max_context:
            raise ValueError(
                f"序列长度 {codes.shape[1]} 超过最大上下文 {self.max_context}"
            )
        x = self.embedding(codes)
        if stamp is not None:
            x = x + self.time_emb(stamp)
        x = self.token_drop(x)
        for block in self.blocks:
            if self.grad_checkpointing and self.training:
                x = checkpoint(block, x, use_reentrant=False)   # 显存优化（附录A）
            else:
                x = block(x)
        return self.norm(x)
