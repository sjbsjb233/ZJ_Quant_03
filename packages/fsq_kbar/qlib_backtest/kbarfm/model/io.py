"""模型checkpoint读写：权重与构建配置一并保存，加载端自动重建结构."""

from __future__ import annotations

from pathlib import Path

import torch

from .backbone import KBarFoundationModel
from .tokenizer import KBarTokenizer


def save_checkpoint(
    path: str | Path, model: torch.nn.Module, model_cfg: dict, step: int
) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {"state_dict": model.state_dict(), "model_cfg": dict(model_cfg), "step": step},
        path,
    )


def load_tokenizer(path: str | Path, device: str = "cuda") -> KBarTokenizer:
    ckpt = torch.load(path, map_location="cpu")
    cfg = ckpt["model_cfg"]
    model = KBarTokenizer(
        in_dim=cfg.get("in_dim", 6),
        d_model=cfg["d_model"],
        n_heads=cfg["n_heads"],
        d_ff=cfg["d_ff"],
        n_layers=cfg["n_layers"],
        dropout=cfg.get("dropout", 0.1),
        fsq_levels=tuple(cfg["fsq_levels"]),
    )
    model.load_state_dict(ckpt["state_dict"])
    return model.to(device).eval()


def load_backbone(path: str | Path, device: str = "cuda") -> KBarFoundationModel:
    ckpt = torch.load(path, map_location="cpu")
    model = KBarFoundationModel(ckpt["model_cfg"])
    model.load_state_dict(ckpt["state_dict"])
    return model.to(device).eval()
