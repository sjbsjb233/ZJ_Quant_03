"""自回归预训练（4.2，表4.4 / 表4.5 / 附录A）.

- 标记化器冻结，训练中在线将连续K线窗口编码为四组子标记；
- 纯解码器骨干对四组子标记并行自回归建模，训练目标为四组分类
  负对数似然之和，叠加分位数预测头的pinball损失（联合权重0.5）；
- 自采样（scheduled sampling）缓解曝光偏差：以随训练递增的概率
  将部分输入子标记替换为模型自身的上一位置预测；
- 显存三件套（附录A）：BF16 autocast、梯度检查点（模型内部）、
  梯度累积等效放大批量；训练量由token预算控制（0.5B–1B）。
"""

from __future__ import annotations

from pathlib import Path

import torch
from torch.utils.data import DataLoader

from ..data.dataset import PretrainWindowDataset
from ..model.backbone import KBarFoundationModel
from ..model.io import load_tokenizer, save_checkpoint
from ..model.losses import grouped_cross_entropy, pinball_loss
from ..utils.logging import get_logger
from .optim import configure_adamw, make_warmup_cosine

logger = get_logger("kbarfm.pretrain")


def _scheduled_sampling_prob(step: int, max_p: float, warmup: int) -> float:
    return min(max_p, max_p * step / max(1, warmup))


@torch.no_grad()
def _mix_with_self_predictions(
    model: KBarFoundationModel,
    inp: torch.Tensor,
    time_feats: torch.Tensor,
    p: float,
) -> torch.Tensor:
    """自采样：位置t的输入子标记以概率p替换为模型基于历史对t的预测.

    第一遍前向得到各位置对"下一根K线"的贪心预测；输入序列第t位
    (t>=1)的预测来自隐状态t-1处的四组分类头argmax。
    """
    logits_list, _q, _h = model(inp, time_feats)
    preds = torch.stack([lg.argmax(dim=-1) for lg in logits_list], dim=-1)  # (B,T,G)
    mixed = inp.clone()
    replace = torch.rand(inp.shape[:2], device=inp.device) < p               # (B,T)
    replace[:, 0] = False  # 首位置无历史预测
    mixed[replace] = preds[:, :-1][replace[:, 1:]]
    return mixed


def pretrain(cfg) -> Path:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    torch.manual_seed(cfg.train.seed)

    tokenizer = load_tokenizer(cfg.train.tokenizer_ckpt, device)
    tokenizer.requires_grad_(False)

    mcfg = dict(cfg.model)
    model = KBarFoundationModel(mcfg).to(device)
    logger.info(
        "%s规格参数量: %.1fM（表4.4口径）", mcfg["name"], model.num_parameters() / 1e6
    )

    context = int(cfg.train.context)
    dataset = PretrainWindowDataset(
        cfg.train.corpus_manifest,
        window=context + 1,                 # next-token需目标位
        horizon=int(mcfg["horizon"]),       # 分位数标签前瞻H步
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
    # 以token预算折算优化步数：每个优化步消费 batch*accum*context 根K线
    tokens_per_step = cfg.train.batch_size * cfg.train.grad_accum * context
    total_steps = int(cfg.train.token_budget) // tokens_per_step
    scheduler = make_warmup_cosine(optimizer, cfg.train.warmup_steps, total_steps)
    logger.info("token预算 %.2fB -> %d 个优化步", cfg.train.token_budget / 1e9, total_steps)

    ckpt_dir = Path(cfg.train.ckpt_dir)
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    qw = float(cfg.train.quantile_loss_weight)

    model.train()
    opt_step, tokens_seen = 0, 0
    optimizer.zero_grad(set_to_none=True)

    for micro_step, batch in enumerate(iter(loader)):
        x = batch["x"].to(device, non_blocking=True)            # (B, T+1, 6)
        time_feats = batch["time"].to(device, non_blocking=True)
        ret = batch["ret"].to(device, non_blocking=True)        # (B, T+1)
        ret_mask = batch["ret_mask"].to(device, non_blocking=True)

        with torch.autocast("cuda", dtype=torch.bfloat16, enabled=cfg.train.bf16):
            with torch.no_grad():
                codes, _ = tokenizer.encode(x)                  # (B, T+1, G) 冻结标记化

            inp, tgt = codes[:, :-1], codes[:, 1:]
            t_in = time_feats[:, :-1]

            p_ss = _scheduled_sampling_prob(
                opt_step, cfg.train.scheduled_sampling_max,
                cfg.train.scheduled_sampling_warmup,
            )
            if p_ss > 0:
                inp = _mix_with_self_predictions(model, inp, t_in, p_ss)

            logits_list, q_pred, _h = model(inp, t_in)
            ce = grouped_cross_entropy(logits_list, tgt)
            ql = pinball_loss(
                q_pred.float(), ret[:, :-1], model.quantiles, ret_mask[:, :-1]
            )
            loss = ce + qw * ql

        (loss / cfg.train.grad_accum).backward()
        tokens_seen += x.size(0) * context

        if (micro_step + 1) % cfg.train.grad_accum == 0:
            torch.nn.utils.clip_grad_norm_(model.parameters(), cfg.train.grad_clip)
            optimizer.step()
            scheduler.step()
            optimizer.zero_grad(set_to_none=True)
            opt_step += 1

            if opt_step % cfg.train.log_every == 0:
                logger.info(
                    "opt_step %d/%d | ce %.4f | pinball %.4f | ss_p %.3f | "
                    "tokens %.2fB | lr %.2e",
                    opt_step, total_steps, ce.item(), ql.item(), p_ss,
                    tokens_seen / 1e9, scheduler.get_last_lr()[0],
                )
            if opt_step % cfg.train.save_every == 0:
                save_checkpoint(
                    ckpt_dir / f"backbone_step{opt_step}.pt", model, mcfg, opt_step
                )
            if opt_step >= total_steps or tokens_seen >= cfg.train.token_budget:
                break

    final = ckpt_dir / "backbone_final.pt"
    save_checkpoint(final, model, mcfg, opt_step)
    logger.info("预训练完成（%.2fB tokens）-> %s", tokens_seen / 1e9, final)
    return final
