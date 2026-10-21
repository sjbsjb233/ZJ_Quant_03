"""K线标记化器训练（4.1，表4.2）.

以结构约束重构损失（4.1.2）端到端优化Transformer自编码器+FSQ；
FSQ无需承诺损失、熵正则等辅助目标即可稳定训练（4.1.1）。
"""

from __future__ import annotations

import itertools
from pathlib import Path

import torch
from torch.utils.data import DataLoader

from ..data.dataset import PretrainWindowDataset
from ..model.io import save_checkpoint
from ..model.losses import structure_constrained_recon_loss
from ..model.tokenizer import KBarTokenizer
from ..utils.logging import get_logger
from .optim import configure_adamw, make_warmup_cosine

logger = get_logger("kbarfm.train_tokenizer")


def train_tokenizer(cfg) -> Path:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    torch.manual_seed(cfg.train.seed)

    mcfg = dict(cfg.model)
    model = KBarTokenizer(
        in_dim=mcfg["in_dim"],
        d_model=mcfg["d_model"],
        n_heads=mcfg["n_heads"],
        d_ff=mcfg["d_ff"],
        n_layers=mcfg["n_layers"],
        dropout=mcfg["dropout"],
        fsq_levels=tuple(mcfg["fsq_levels"]),
    ).to(device)
    logger.info("标记化器参数量: %.2fM", sum(p.numel() for p in model.parameters()) / 1e6)

    dataset = PretrainWindowDataset(
        cfg.train.corpus_manifest,
        window=cfg.train.window,
        horizon=0,
        train=True,
        seed=cfg.train.seed,
    )
    loader = DataLoader(
        dataset,
        batch_size=cfg.train.batch_size,
        num_workers=cfg.train.num_workers,
        pin_memory=True,
        drop_last=True,
    )

    optimizer = configure_adamw(model, cfg.train.lr, cfg.train.weight_decay)
    scheduler = make_warmup_cosine(
        optimizer, cfg.train.warmup_steps, cfg.train.max_steps
    )
    lw = dict(cfg.loss)
    ckpt_dir = Path(cfg.train.ckpt_dir)
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    model.train()
    for step, batch in enumerate(itertools.islice(iter(loader), cfg.train.max_steps)):
        x = batch["x"].to(device, non_blocking=True)
        mean = batch["mean"].to(device, non_blocking=True)
        std = batch["std"].to(device, non_blocking=True)

        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=cfg.train.bf16):
            recon, _codes, _zq = model(x)
            loss, parts = structure_constrained_recon_loss(
                recon.float(), x.float(), mean, std,
                w_huber=lw["w_huber"], w_return=lw["w_return"],
                w_volume=lw["w_volume"], w_structure=lw["w_structure"],
            )

        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), cfg.train.grad_clip)
        optimizer.step()
        scheduler.step()

        if step % cfg.train.log_every == 0:
            logger.info(
                "step %d | loss %.4f | huber %.4f ret %.4f vol %.4f struct %.4f | lr %.2e",
                step, loss.item(), parts["huber"], parts["return"],
                parts["volume"], parts["structure"], scheduler.get_last_lr()[0],
            )
        if step > 0 and step % cfg.train.save_every == 0:
            save_checkpoint(ckpt_dir / f"tokenizer_step{step}.pt", model, mcfg, step)

    final = ckpt_dir / "tokenizer_final.pt"
    save_checkpoint(final, model, mcfg, cfg.train.max_steps)
    logger.info("标记化器训练完成 -> %s", final)
    return final
