"""模型与检查点读写."""

from __future__ import annotations

from pathlib import Path

import torch

from ..utils.logging import get_logger
from .foundation import KBarFoundationModel, build_model
from .tokenizer import KBarTokenizer

log = get_logger("kbarfm.io")


def save_checkpoint(
    path: str | Path,
    model: torch.nn.Module,
    step: int,
    config: dict | None = None,
    optimizer: torch.optim.Optimizer | None = None,
    extra: dict | None = None,
) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model_state": model.state_dict(),
        "step": step,
        "config": config or {},
        "extra": extra or {},
    }
    if optimizer is not None:
        payload["optimizer_state"] = optimizer.state_dict()
    torch.save(payload, path)
    log.info("检查点已保存: %s (step=%d)", path, step)


def load_tokenizer(path: str | Path, map_location: str = "cpu") -> KBarTokenizer:
    ckpt = torch.load(path, map_location=map_location, weights_only=False)
    cfg = ckpt.get("config", {}).get("model", {})
    tokenizer = KBarTokenizer(
        d_in=cfg.get("d_in", 6),
        d_model=cfg.get("d_model", 192),
        ff_dim=cfg.get("ff_dim", 768),
        n_heads=cfg.get("n_heads", 6),
        n_enc_layers=cfg.get("n_enc_layers", 2),
        n_dec_layers=cfg.get("n_dec_layers", 2),
        fsq_levels=tuple(cfg.get("fsq_levels", (8, 8, 8, 5))),
    )
    tokenizer.load_state_dict(ckpt["model_state"])
    tokenizer.eval()
    return tokenizer


def load_foundation(
    path: str | Path, map_location: str = "cpu"
) -> KBarFoundationModel:
    ckpt = torch.load(path, map_location=map_location, weights_only=False)
    size = ckpt.get("config", {}).get("model_size", "base")
    model = build_model(size=size)
    model.load_state_dict(ckpt["model_state"])
    model.eval()
    return model
