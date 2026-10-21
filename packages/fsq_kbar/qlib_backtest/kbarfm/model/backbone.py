"""纯解码器自回归骨干（4.2.1 / 4.2.2，表4.4）.

因果注意力（下三角掩码，SDPA/FlashAttention后端）保证预测仅依赖
历史上下文；RoPE表征相对位置；Pre-LN + RMSNorm提升深层训练稳定性；
支持梯度检查点以适配单张24GB消费级显卡（附录A）。

输入为四组子标记（各组独立嵌入后求和）叠加五类时间特征嵌入
（表3.5），输出隐状态交由GroupHeads并行预测下一根K线的四组
子标记、由QuantileHead输出未来H日收益分位数。
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.checkpoint import checkpoint

from ..data.normalize import TIME_FEATURE_SIZES
from .heads import GroupHeads, QuantileHead
from .rope import apply_rope, build_rope_cache


class RMSNorm(nn.Module):
    def __init__(self, dim: int, eps: float = 1e-6) -> None:
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.ones(dim))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        norm = x * torch.rsqrt(x.pow(2).mean(dim=-1, keepdim=True) + self.eps)
        return norm * self.weight


class CausalSelfAttention(nn.Module):
    def __init__(self, d_model: int, n_heads: int, dropout_attn: float) -> None:
        super().__init__()
        assert d_model % n_heads == 0
        self.n_heads = n_heads
        self.head_dim = d_model // n_heads
        self.qkv = nn.Linear(d_model, 3 * d_model, bias=False)
        self.proj = nn.Linear(d_model, d_model, bias=False)
        self.dropout_attn = dropout_attn

    def forward(
        self, x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor
    ) -> torch.Tensor:
        B, T, C = x.shape
        q, k, v = self.qkv(x).chunk(3, dim=-1)
        q = q.view(B, T, self.n_heads, self.head_dim).transpose(1, 2)
        k = k.view(B, T, self.n_heads, self.head_dim).transpose(1, 2)
        v = v.view(B, T, self.n_heads, self.head_dim).transpose(1, 2)
        q, k = apply_rope(q, k, cos, sin)
        # SDPA自动路由至FlashAttention等高效实现（附录A）
        y = F.scaled_dot_product_attention(
            q, k, v,
            dropout_p=self.dropout_attn if self.training else 0.0,
            is_causal=True,
        )
        y = y.transpose(1, 2).contiguous().view(B, T, C)
        return self.proj(y)


class FeedForward(nn.Module):
    def __init__(self, d_model: int, d_ff: int, dropout_ffn: float) -> None:
        super().__init__()
        self.fc1 = nn.Linear(d_model, d_ff)
        self.fc2 = nn.Linear(d_ff, d_model)
        self.drop = nn.Dropout(dropout_ffn)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.fc2(self.drop(F.gelu(self.fc1(x))))


class DecoderBlock(nn.Module):
    def __init__(
        self,
        d_model: int,
        n_heads: int,
        d_ff: int,
        dropout_attn: float,
        dropout_ffn: float,
        dropout_resid: float,
    ) -> None:
        super().__init__()
        self.norm1 = RMSNorm(d_model)
        self.attn = CausalSelfAttention(d_model, n_heads, dropout_attn)
        self.norm2 = RMSNorm(d_model)
        self.ffn = FeedForward(d_model, d_ff, dropout_ffn)
        self.drop = nn.Dropout(dropout_resid)

    def forward(
        self, x: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor
    ) -> torch.Tensor:
        x = x + self.drop(self.attn(self.norm1(x), cos, sin))   # Pre-LN
        x = x + self.drop(self.ffn(self.norm2(x)))
        return x


class KBarBackbone(nn.Module):
    def __init__(
        self,
        n_layers: int,
        d_model: int,
        d_ff: int,
        n_heads: int,
        fsq_levels: tuple[int, ...],
        max_context: int = 384,
        dropout_attn: float = 0.05,
        dropout_ffn: float = 0.10,
        dropout_resid: float = 0.10,
        dropout_token: float = 0.0,
        grad_checkpoint: bool = False,
    ) -> None:
        super().__init__()
        self.fsq_levels = tuple(fsq_levels)
        self.max_context = max_context
        self.dropout_token = dropout_token
        self.grad_checkpoint = grad_checkpoint

        # 四组子标记各自嵌入后求和（4.2.1 并行分组）
        self.token_emb = nn.ModuleList(
            [nn.Embedding(L, d_model) for L in self.fsq_levels]
        )
        # 五类时间特征嵌入（分钟、小时、星期、日、月，表3.5）
        self.time_emb = nn.ModuleList(
            [nn.Embedding(n, d_model) for n in TIME_FEATURE_SIZES]
        )

        self.blocks = nn.ModuleList(
            [
                DecoderBlock(
                    d_model, n_heads, d_ff, dropout_attn, dropout_ffn, dropout_resid
                )
                for _ in range(n_layers)
            ]
        )
        self.final_norm = RMSNorm(d_model)

        head_dim = d_model // n_heads
        cos, sin = build_rope_cache(max_context, head_dim)
        self.register_buffer("rope_cos", cos, persistent=False)
        self.register_buffer("rope_sin", sin, persistent=False)

    def forward(self, codes: torch.Tensor, time_feats: torch.Tensor) -> torch.Tensor:
        """codes: (B, T, G) long；time_feats: (B, T, 5) long -> 隐状态 (B, T, d)."""
        h = sum(emb(codes[..., g]) for g, emb in enumerate(self.token_emb))
        h = h + sum(emb(time_feats[..., i]) for i, emb in enumerate(self.time_emb))

        # Token Dropout（表4.5）：训练时随机置零整根K线的嵌入
        if self.training and self.dropout_token > 0:
            keep = (
                torch.rand(h.shape[:2], device=h.device) >= self.dropout_token
            ).unsqueeze(-1)
            h = h * keep

        for blk in self.blocks:
            if self.grad_checkpoint and self.training:
                h = checkpoint(blk, h, self.rope_cos, self.rope_sin, use_reentrant=False)
            else:
                h = blk(h, self.rope_cos, self.rope_sin)
        return self.final_norm(h)


class KBarFoundationModel(nn.Module):
    """骨干 + 四组并行分类头 + 分位数收益预测头 的整装模型."""

    def __init__(self, model_cfg: dict) -> None:
        super().__init__()
        self.cfg = dict(model_cfg)
        levels = tuple(model_cfg["fsq_levels"])
        self.backbone = KBarBackbone(
            n_layers=model_cfg["n_layers"],
            d_model=model_cfg["d_model"],
            d_ff=model_cfg["d_ff"],
            n_heads=model_cfg["n_heads"],
            fsq_levels=levels,
            max_context=model_cfg.get("max_context", 384),
            dropout_attn=model_cfg.get("dropout_attn", 0.05),
            dropout_ffn=model_cfg.get("dropout_ffn", 0.10),
            dropout_resid=model_cfg.get("dropout_resid", 0.10),
            dropout_token=model_cfg.get("dropout_token", 0.0),
            grad_checkpoint=model_cfg.get("grad_checkpoint", False),
        )
        self.group_heads = GroupHeads(model_cfg["d_model"], levels)
        self.quantile_head = QuantileHead(
            model_cfg["d_model"], tuple(model_cfg.get("quantiles", (0.1, 0.5, 0.9)))
        )
        self.quantiles = self.quantile_head.quantiles
        self.horizon = int(model_cfg.get("horizon", 10))

    def forward(
        self, codes: torch.Tensor, time_feats: torch.Tensor
    ) -> tuple[list[torch.Tensor], torch.Tensor, torch.Tensor]:
        """返回 (四组logits列表, 分位数预测 (B,T,Q), 隐状态)."""
        h = self.backbone(codes, time_feats)
        return self.group_heads(h), self.quantile_head(h), h

    def num_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters())
