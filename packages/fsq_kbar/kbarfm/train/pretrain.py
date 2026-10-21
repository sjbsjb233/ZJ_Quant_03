"""自回归预训练主循环（报告4.2.2，附录A）.

- 冻结已训练的标记化器，在线将连续K线窗口编码为四组子标记；
- 训练目标：四组分类NLL之和 + 0.5 × 分位数回归损失；
- 自采样概率随训练进程线性升至上限，缓解曝光偏差（4.2.1）；
- 单卡显存优化：BF16精度、梯度检查点、SDPA高效注意力、
  梯度累积（等效放大批量）。
"""

from __future__ import annotations

from pathlib import Path

import torch
from torch.utils.data import DataLoader

from ..data.corpus import load_meta
from ..data.dataset import PretrainWindowDataset
from ..model.foundation import MODEL_FAMILY, build_model
from ..model.io import load_tokenizer, save_checkpoint
from ..utils.config import Cfg
from ..utils.logging import get_logger
from .optim import CosineWarmupScheduler, build_optimizer

log = get_logger("kbarfm.train.pretrain")


def _device() -> torch.device:
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def pretrain(cfg: Cfg) -> Path:
    device = _device()
    torch.manual_seed(cfg.get_path("train.seed", 42))

    size = cfg.get_path("model_size", "base")
    family = MODEL_FAMILY[size]
    peak_lr = cfg.get_path("train.lr", family["lr"])
    weight_decay = cfg.get_path("train.weight_decay", family["weight_decay"])

    # ---------------------------------------------------------------- 数据
    meta = load_meta(cfg.data.corpus_dir)
    dataset = PretrainWindowDataset(
        meta,
        seq_len=cfg.get_path("data.seq_len", 384),
        horizon=cfg.get_path("data.horizon", 10),
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

    # ---------------------------------------------------------------- 模型
    tokenizer = load_tokenizer(cfg.tokenizer_ckpt).to(device)
    for p in tokenizer.parameters():
        p.requires_grad_(False)

    model = build_model(
        size=size,
        grad_checkpointing=bool(cfg.get_path("train.grad_checkpointing", True)),
        quantile_loss_weight=cfg.get_path("train.quantile_loss_weight", 0.5),
    ).to(device)
    log.info("基座模型规格=%s，参数量=%.1fM", size, model.num_parameters / 1e6)

    optimizer = build_optimizer(model, lr=peak_lr, weight_decay=weight_decay)
    scheduler = CosineWarmupScheduler(
        optimizer,
        peak_lr=peak_lr,
        total_steps=cfg.train.steps,
        warmup_steps=cfg.get_path("train.warmup_steps", 1500),
    )
    use_bf16 = bool(cfg.get_path("train.bf16", True)) and device.type == "cuda"
    accum = int(cfg.get_path("train.grad_accum", 4))
    ss_max = float(cfg.get_path("train.self_sampling_max_p", 0.15))
    ss_ramp = int(cfg.get_path("train.self_sampling_ramp_steps", cfg.train.steps // 2))

    out_dir = Path(cfg.train.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    model.train()
    data_iter = iter(loader)
    optimizer.zero_grad(set_to_none=True)
    running = {"loss": 0.0, "ce": 0.0, "pinball": 0.0, "n": 0}

    for step in range(1, int(cfg.train.steps) + 1):
        ss_p = ss_max * min(step / max(ss_ramp, 1), 1.0)   # 自采样概率线性升温

        micro_loss = 0.0
        for _ in range(accum):
            x, stamp, y_ret, y_mask = next(data_iter)
            x, stamp = x.to(device), stamp.to(device)
            y_ret, y_mask = y_ret.to(device), y_mask.to(device)

            with torch.no_grad():
                codes = tokenizer.encode(x)                # 冻结标记化器在线编码

            with torch.autocast("cuda", dtype=torch.bfloat16, enabled=use_bf16):
                loss, parts = model.compute_loss(
                    codes, stamp, y_ret, y_mask, self_sampling_p=ss_p
                )
            (loss / accum).backward()
            micro_loss += float(loss.detach()) / accum
            running["ce"] += parts["ce"] / accum
            running["pinball"] += parts["pinball"] / accum

        torch.nn.utils.clip_grad_norm_(model.parameters(), cfg.get_path("train.grad_clip", 1.0))
        optimizer.step()
        optimizer.zero_grad(set_to_none=True)
        lr = scheduler.step()

        running["loss"] += micro_loss
        running["n"] += 1
        if step % cfg.get_path("train.log_every", 50) == 0:
            n = max(running["n"], 1)
            log.info(
                "step %d | loss %.4f (ce %.4f, pinball %.5f) | lr %.2e | 自采样p %.3f",
                step, running["loss"] / n, running["ce"] / n, running["pinball"] / n, lr, ss_p,
            )
            running = {"loss": 0.0, "ce": 0.0, "pinball": 0.0, "n": 0}

        if step % cfg.get_path("train.ckpt_every", 5000) == 0:
            save_checkpoint(
                out_dir / f"{size}_step{step}.pt",
                model,
                step,
                config={"model_size": size, **dict(cfg)},
                optimizer=optimizer,
            )

    final_path = out_dir / f"{size}_final.pt"
    save_checkpoint(
        final_path, model, int(cfg.train.steps), config={"model_size": size, **dict(cfg)}
    )
    return final_path
