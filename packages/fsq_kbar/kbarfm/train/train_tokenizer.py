"""标记化器训练主循环（报告4.1，表4.2）.

以4.1.2的结构约束重构损失端到端优化Transformer自编码器；
周期性输出重构NRMSE与FSQ离散状态利用统计（表4.3口径）。
"""

from __future__ import annotations

from pathlib import Path

import torch
from torch.utils.data import DataLoader

from ..data.corpus import load_meta
from ..data.dataset import TokenizerWindowDataset
from ..model.fsq import fsq_usage_stats
from ..model.losses import structure_recon_loss
from ..model.io import save_checkpoint
from ..model.tokenizer import KBarTokenizer
from ..utils.config import Cfg
from ..utils.logging import get_logger
from .optim import CosineWarmupScheduler, build_optimizer

log = get_logger("kbarfm.train.tokenizer")


def _device() -> torch.device:
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


@torch.no_grad()
def _nrmse(x_hat: torch.Tensor, x: torch.Tensor) -> float:
    """标准化空间的归一化均方根误差（std≈1，直接以RMSE近似）."""
    return float(torch.sqrt(torch.mean((x_hat - x) ** 2)))


def train_tokenizer(cfg: Cfg) -> Path:
    device = _device()
    torch.manual_seed(cfg.get_path("train.seed", 42))

    meta = load_meta(cfg.data.corpus_dir)
    dataset = TokenizerWindowDataset(
        meta,
        seq_len=cfg.data.seq_len,
        rebalance_alpha=cfg.get_path("data.rebalance_alpha", 0.5),
        volamt_mask_p=cfg.get_path("data.volamt_mask_p", 0.05),
        seed=cfg.get_path("train.seed", 42),
    )
    loader = DataLoader(
        dataset,
        batch_size=cfg.data.batch_size,
        num_workers=cfg.get_path("data.num_workers", 4),
        pin_memory=True,
    )

    model = KBarTokenizer(
        d_in=cfg.get_path("model.d_in", 6),
        d_model=cfg.get_path("model.d_model", 192),
        ff_dim=cfg.get_path("model.ff_dim", 768),
        n_heads=cfg.get_path("model.n_heads", 6),
        n_enc_layers=cfg.get_path("model.n_enc_layers", 2),
        n_dec_layers=cfg.get_path("model.n_dec_layers", 2),
        fsq_levels=tuple(cfg.get_path("model.fsq_levels", (8, 8, 8, 5))),
    ).to(device)
    log.info("标记化器参数量: %.2fM", sum(p.numel() for p in model.parameters()) / 1e6)

    optimizer = build_optimizer(model, lr=cfg.train.lr, weight_decay=cfg.train.weight_decay)
    scheduler = CosineWarmupScheduler(
        optimizer,
        peak_lr=cfg.train.lr,
        total_steps=cfg.train.steps,
        warmup_steps=cfg.get_path("train.warmup_steps", 1500),
    )
    use_bf16 = bool(cfg.get_path("train.bf16", True)) and device.type == "cuda"
    loss_cfg = cfg.get("loss", {})

    out_dir = Path(cfg.train.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    codes_pool: list[torch.Tensor] = []

    model.train()
    data_iter = iter(loader)
    for step in range(1, int(cfg.train.steps) + 1):
        x, mean, std, _stamp = next(data_iter)
        x, mean, std = x.to(device), mean.to(device), std.to(device)

        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=use_bf16):
            x_hat, codes, _zq = model(x)
            loss, parts = structure_recon_loss(
                x_hat.float(),
                x.float(),
                mean,
                std,
                lambda_huber=loss_cfg.get("huber", 1.0),
                lambda_ret=loss_cfg.get("ret", 0.40),
                lambda_volamt=loss_cfg.get("volamt", 0.25),
                lambda_struct=loss_cfg.get("structure", 0.15),
                huber_delta=loss_cfg.get("huber_delta", 1.0),
            )

        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), cfg.get_path("train.grad_clip", 1.0))
        optimizer.step()
        lr = scheduler.step()

        if len(codes_pool) < 200:                      # 采样子标记做状态利用统计
            codes_pool.append(codes.detach().reshape(-1, codes.shape[-1]).cpu())

        if step % cfg.get_path("train.log_every", 100) == 0:
            log.info(
                "step %d | loss %.4f | nrmse %.4f | lr %.2e | %s",
                step, float(loss), _nrmse(x_hat.float(), x.float()), lr, parts,
            )
        if step % cfg.get_path("train.usage_every", 5000) == 0 and codes_pool:
            stats = fsq_usage_stats(torch.cat(codes_pool), model.levels)
            log.info("FSQ状态利用（表4.3口径）: %s", stats)
            codes_pool.clear()
        if step % cfg.get_path("train.ckpt_every", 10000) == 0:
            save_checkpoint(out_dir / f"tokenizer_step{step}.pt", model, step, config=dict(cfg))

    final_path = out_dir / "tokenizer_final.pt"
    save_checkpoint(final_path, model, int(cfg.train.steps), config=dict(cfg))
    return final_path
