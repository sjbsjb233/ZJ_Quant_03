"""K线标记化器（报告4.1，图4.1）.

Transformer自编码器三模块串联：
  编码器（2层轻量Transformer, d=192, ff=768, heads=6）将一根连续
  的6维K线压缩为低维潜向量；FSQ量化器把连续潜向量离散化为
  G=4组并行子标记（各维水平[8,8,8,5]，隐式状态2560）；解码器再由
  离散表示重构回连续K线。整个标记化器以4.1.2的结构约束重构
  损失端到端优化。

与"粗主干+细残差"式的层级分解不同：四个标量组之间不设
先后与主次之分，为下游并行分组自回归建模提供对称的分组结构。
"""

from __future__ import annotations

import torch
import torch.nn as nn

from .fsq import FSQ
from .layers import TransformerBlock


class KBarTokenizer(nn.Module):
    def __init__(
        self,
        d_in: int = 6,
        d_model: int = 192,
        ff_dim: int = 768,
        n_heads: int = 6,
        n_enc_layers: int = 2,
        n_dec_layers: int = 2,
        fsq_levels: tuple[int, ...] = (8, 8, 8, 5),
        dropout_p: float = 0.0,
    ) -> None:
        super().__init__()
        self.d_in = d_in
        self.fsq = FSQ(fsq_levels)
        g = self.fsq.num_groups

        self.embed = nn.Linear(d_in, d_model)
        self.encoder = nn.ModuleList(
            TransformerBlock(d_model, n_heads, ff_dim, dropout_p, dropout_p, dropout_p)
            for _ in range(n_enc_layers)
        )
        self.quant_proj = nn.Linear(d_model, g)          # 压缩到G个标量维度
        self.post_quant_proj = nn.Linear(g, d_model)
        self.decoder = nn.ModuleList(
            TransformerBlock(d_model, n_heads, ff_dim, dropout_p, dropout_p, dropout_p)
            for _ in range(n_dec_layers)
        )
        self.head = nn.Linear(d_model, d_in)

    # ------------------------------------------------------------------ forward
    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """x: (B, T, 6) -> (x_hat, codes, zq).

        codes: (B, T, G) 四组并行子标记；zq: (B, T, G) 直通量化值。
        """
        zq, codes = self._encode_quantize(x)
        x_hat = self._decode_values(zq)
        return x_hat, codes, zq

    def _encode_quantize(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        z = self.embed(x)
        for layer in self.encoder:
            z = layer(z)
        z = self.quant_proj(z)
        return self.fsq(z)

    def _decode_values(self, zq: torch.Tensor) -> torch.Tensor:
        y = self.post_quant_proj(zq)
        for layer in self.decoder:
            y = layer(y)
        return self.head(y)

    # ---------------------------------------------------------------- inference
    @torch.no_grad()
    def encode(self, x: torch.Tensor) -> torch.Tensor:
        """连续K线 -> 四组子标记. x: (B, T, 6) -> (B, T, G) long."""
        _, codes = self._encode_quantize(x)
        return codes

    @torch.no_grad()
    def decode(self, codes: torch.Tensor) -> torch.Tensor:
        """四组子标记 -> 重构K线（标准化空间）. codes: (B, T, G) -> (B, T, 6)."""
        zq = self.fsq.codes_to_values(codes)
        return self._decode_values(zq)

    @property
    def levels(self) -> tuple[int, ...]:
        return self.fsq.levels
