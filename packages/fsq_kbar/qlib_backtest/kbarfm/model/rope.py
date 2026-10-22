"""旋转位置编码 RoPE（4.2.1，Su et al., 2024）.

以相对位置关系表征时间步间距，使模型能外推到不同长度的序列。
采用"前半/后半"旋转对约定，作用于注意力的q、k。
"""

from __future__ import annotations

import torch


def build_rope_cache(
    seq_len: int,
    head_dim: int,
    base: float = 10000.0,
    device: torch.device | None = None,
    dtype: torch.dtype = torch.float32,
) -> tuple[torch.Tensor, torch.Tensor]:
    """返回 (cos, sin)，形状均为 (seq_len, head_dim // 2)."""
    half = head_dim // 2
    inv_freq = 1.0 / (
        base ** (torch.arange(0, half, device=device, dtype=dtype) / half)
    )
    t = torch.arange(seq_len, device=device, dtype=dtype)
    freqs = torch.outer(t, inv_freq)  # (T, half)
    return freqs.cos(), freqs.sin()


def apply_rope(
    q: torch.Tensor, k: torch.Tensor, cos: torch.Tensor, sin: torch.Tensor
) -> tuple[torch.Tensor, torch.Tensor]:
    """q, k: (B, n_heads, T, head_dim)；cos/sin: (T, head_dim//2)."""
    T = q.size(-2)
    cos = cos[:T].to(q.dtype)[None, None, :, :]  # (1,1,T,half)
    sin = sin[:T].to(q.dtype)[None, None, :, :]

    def rot(x: torch.Tensor) -> torch.Tensor:
        x1, x2 = x.chunk(2, dim=-1)
        return torch.cat([x1 * cos - x2 * sin, x1 * sin + x2 * cos], dim=-1)

    return rot(q), rot(k)
