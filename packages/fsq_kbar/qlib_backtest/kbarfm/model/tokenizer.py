"""K线标记化器：基于FSQ的Transformer自编码器（4.1，图4.1，表4.2）.

编码器将一根连续的6维K线（在序列语境中）压缩为低维潜向量，
线性投影到G=4个标量维度后经FSQ离散化为四组并行子标记，
解码器再由离散表示重构回连续K线。编码器与解码器均为
2层、d_model=192、d_ff=768、6头的轻量Transformer，
以4.1.2的结构约束重构损失端到端优化。
"""

from __future__ import annotations

import math

import torch
import torch.nn as nn

from .fsq import FSQ


class SinusoidalPE(nn.Module):
    """标准正弦位置编码（自编码器双向语境用）."""

    def __init__(self, d_model: int, max_len: int = 4096) -> None:
        super().__init__()
        pe = torch.zeros(max_len, d_model)
        pos = torch.arange(max_len).unsqueeze(1).float()
        div = torch.exp(
            torch.arange(0, d_model, 2).float() * (-math.log(10000.0) / d_model)
        )
        pe[:, 0::2] = torch.sin(pos * div)
        pe[:, 1::2] = torch.cos(pos * div)
        self.register_buffer("pe", pe, persistent=False)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x + self.pe[: x.size(1)].unsqueeze(0)


def _encoder_stack(d_model: int, n_heads: int, d_ff: int, n_layers: int, dropout: float):
    layer = nn.TransformerEncoderLayer(
        d_model=d_model,
        nhead=n_heads,
        dim_feedforward=d_ff,
        dropout=dropout,
        activation="gelu",
        batch_first=True,
        norm_first=True,  # Pre-LN
    )
    return nn.TransformerEncoder(layer, num_layers=n_layers)


class KBarTokenizer(nn.Module):
    def __init__(
        self,
        in_dim: int = 6,
        d_model: int = 192,
        n_heads: int = 6,
        d_ff: int = 768,
        n_layers: int = 2,
        dropout: float = 0.1,
        fsq_levels: tuple[int, ...] = (8, 8, 8, 5),
    ) -> None:
        super().__init__()
        self.fsq = FSQ(fsq_levels)
        G = self.fsq.num_groups

        self.in_proj = nn.Linear(in_dim, d_model)
        self.pe = SinusoidalPE(d_model)
        self.encoder = _encoder_stack(d_model, n_heads, d_ff, n_layers, dropout)
        self.to_scalars = nn.Linear(d_model, G)     # 潜向量 -> G个标量维

        self.from_scalars = nn.Linear(G, d_model)   # 量化值 -> 解码语境
        self.decoder = _encoder_stack(d_model, n_heads, d_ff, n_layers, dropout)
        self.out_proj = nn.Linear(d_model, in_dim)

    # ---- 编码：连续K线 -> 四组并行子标记 ----
    def encode(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """x: (B, T, 6) 标准化OHLCVA -> (codes (B,T,G) long, zq (B,T,G))."""
        h = self.encoder(self.pe(self.in_proj(x)))
        z = self.to_scalars(h)
        zq, codes = self.fsq(z)
        return codes, zq

    # ---- 解码：量化值 / 子标记 -> 重构K线 ----
    def decode_values(self, zq: torch.Tensor) -> torch.Tensor:
        h = self.decoder(self.pe(self.from_scalars(zq)))
        return self.out_proj(h)

    def decode_codes(self, codes: torch.Tensor) -> torch.Tensor:
        return self.decode_values(self.fsq.codes_to_values(codes))

    def forward(
        self, x: torch.Tensor
    ) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """返回 (重构K线, 子标记, 量化值)."""
        codes, zq = self.encode(x)
        recon = self.decode_values(zq)
        return recon, codes, zq
