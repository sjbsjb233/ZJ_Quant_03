"""Transformer基础组件（报告4.2.1）.

纯解码器骨干的构件：RMSNorm（Pre-LN）、SwiGLU前馈、旋转位置
编码（RoPE）因果自注意力、时间特征嵌入。结构设计借鉴解码器
Transformer的通用实践：因果注意力经下三角掩码保证预测未来时
只依赖历史信息，RoPE表征相对位置关系以支持序列长度外推。
"""

from __future__ import annotations

import math

import torch
import torch.nn as nn
import torch.nn.functional as F


class RMSNorm(nn.Module):
    def __init__(self, dim: int, eps: float = 1e-5) -> None:
        super().__init__()
        self.eps = eps
        self.weight = nn.Parameter(torch.ones(dim))

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        norm = x.float() * torch.rsqrt(x.float().pow(2).mean(-1, keepdim=True) + self.eps)
        return norm.type_as(x) * self.weight


class FeedForward(nn.Module):
    """两层GELU前馈网络（参数量与表4.4口径一致：compact≈16M、base≈41M）."""

    def __init__(self, d_model: int, ff_dim: int, dropout_p: float = 0.0) -> None:
        super().__init__()
        self.w_in = nn.Linear(d_model, ff_dim)
        self.w_out = nn.Linear(ff_dim, d_model)
        self.dropout = nn.Dropout(dropout_p)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.dropout(self.w_out(F.gelu(self.w_in(x))))


class RotaryPositionalEmbedding(nn.Module):
    def __init__(self, head_dim: int, base: float = 10000.0) -> None:
        super().__init__()
        inv_freq = 1.0 / (base ** (torch.arange(0, head_dim, 2).float() / head_dim))
        self.register_buffer("inv_freq", inv_freq, persistent=False)
        self._cached_len = 0
        self._cos: torch.Tensor | None = None
        self._sin: torch.Tensor | None = None

    def _update(self, x: torch.Tensor, seq_len: int) -> None:
        if seq_len != self._cached_len or self._cos is None or self._cos.device != x.device:
            t = torch.arange(seq_len, device=x.device).type_as(self.inv_freq)
            freqs = torch.einsum("i,j->ij", t, self.inv_freq)
            emb = torch.cat((freqs, freqs), dim=-1)
            self._cos = emb.cos()[None, None, :, :]
            self._sin = emb.sin()[None, None, :, :]
            self._cached_len = seq_len

    @staticmethod
    def _rotate_half(x: torch.Tensor) -> torch.Tensor:
        x1, x2 = x.chunk(2, dim=-1)
        return torch.cat((-x2, x1), dim=-1)

    def forward(self, q: torch.Tensor, k: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        self._update(q, q.shape[-2])
        cos, sin = self._cos.type_as(q), self._sin.type_as(q)
        return (
            q * cos + self._rotate_half(q) * sin,
            k * cos + self._rotate_half(k) * sin,
        )


class CausalSelfAttention(nn.Module):
    """带RoPE的多头因果自注意力（SDPA高效实现，附录A）."""

    def __init__(
        self,
        d_model: int,
        n_heads: int,
        attn_dropout_p: float = 0.0,
        resid_dropout_p: float = 0.0,
    ) -> None:
        super().__init__()
        assert d_model % n_heads == 0, "d_model 必须能被 n_heads 整除"
        self.n_heads = n_heads
        self.head_dim = d_model // n_heads
        self.q_proj = nn.Linear(d_model, d_model)
        self.k_proj = nn.Linear(d_model, d_model)
        self.v_proj = nn.Linear(d_model, d_model)
        self.out_proj = nn.Linear(d_model, d_model)
        self.rotary = RotaryPositionalEmbedding(self.head_dim)
        self.attn_dropout_p = attn_dropout_p
        self.resid_dropout = nn.Dropout(resid_dropout_p)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        b, t, d = x.shape
        q = self.q_proj(x).view(b, t, self.n_heads, self.head_dim).transpose(1, 2)
        k = self.k_proj(x).view(b, t, self.n_heads, self.head_dim).transpose(1, 2)
        v = self.v_proj(x).view(b, t, self.n_heads, self.head_dim).transpose(1, 2)
        q, k = self.rotary(q, k)
        out = F.scaled_dot_product_attention(
            q, k, v,
            dropout_p=self.attn_dropout_p if self.training else 0.0,
            is_causal=True,
        )
        out = out.transpose(1, 2).contiguous().view(b, t, d)
        return self.resid_dropout(self.out_proj(out))


class TransformerBlock(nn.Module):
    """Pre-LN 残差块：RMSNorm -> 因果注意力 -> RMSNorm -> SwiGLU."""

    def __init__(
        self,
        d_model: int,
        n_heads: int,
        ff_dim: int,
        ffn_dropout_p: float = 0.0,
        attn_dropout_p: float = 0.0,
        resid_dropout_p: float = 0.0,
    ) -> None:
        super().__init__()
        self.norm1 = RMSNorm(d_model)
        self.attn = CausalSelfAttention(d_model, n_heads, attn_dropout_p, resid_dropout_p)
        self.norm2 = RMSNorm(d_model)
        self.ffn = FeedForward(d_model, ff_dim, ffn_dropout_p)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = x + self.attn(self.norm1(x))
        x = x + self.ffn(self.norm2(x))
        return x


class FixedEmbedding(nn.Module):
    """正弦固定嵌入（时间特征的默认形式）."""

    def __init__(self, c_in: int, d_model: int) -> None:
        super().__init__()
        w = torch.zeros(c_in, d_model)
        position = torch.arange(0, c_in).float().unsqueeze(1)
        div_term = (torch.arange(0, d_model, 2).float() * -(math.log(10000.0) / d_model)).exp()
        w[:, 0::2] = torch.sin(position * div_term)
        w[:, 1::2] = torch.cos(position * div_term[: w[:, 1::2].shape[1]])
        self.emb = nn.Embedding(c_in, d_model)
        self.emb.weight = nn.Parameter(w, requires_grad=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.emb(x).detach()


class TemporalEmbedding(nn.Module):
    """五类周期特征嵌入：分钟、小时、星期、日、月（表3.5）."""

    SIZES = (60, 24, 7, 32, 13)  # minute, hour, weekday, day, month

    def __init__(self, d_model: int, learnable: bool = False) -> None:
        super().__init__()
        embed_cls = nn.Embedding if learnable else FixedEmbedding
        self.embeds = nn.ModuleList([embed_cls(size, d_model) for size in self.SIZES])

    def forward(self, stamp: torch.Tensor) -> torch.Tensor:
        """stamp: (B, T, 5) 整型时间特征 -> (B, T, d_model)."""
        stamp = stamp.long()
        out = 0
        for i, emb in enumerate(self.embeds):
            out = out + emb(stamp[:, :, i])
        return out
