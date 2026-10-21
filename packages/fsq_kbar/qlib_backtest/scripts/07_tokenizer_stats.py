#!/usr/bin/env python3
"""第7步（可选）：标记化效果与离散状态利用统计（4.1.3，表4.3 / 表4.1口径）.

在语料抽样上统计：单维水平覆盖率、归一化信息熵、联合状态覆盖率、
前10%高频状态频率占比，以及重构NRMSE。

用法：python3 scripts/07_tokenizer_stats.py --config configs/tokenizer.yaml --samples 2000
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import torch
from torch.utils.data import DataLoader

from kbarfm.data.dataset import PretrainWindowDataset
from kbarfm.model.fsq import fsq_usage_stats
from kbarfm.model.io import load_tokenizer
from kbarfm.utils.config import load_config
from kbarfm.utils.logging import get_logger

logger = get_logger("kbarfm.script.tokstats")


@torch.no_grad()
def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/tokenizer.yaml")
    parser.add_argument("--ckpt", default="checkpoints/tokenizer/tokenizer_final.pt")
    parser.add_argument("--samples", type=int, default=2000)
    args = parser.parse_args()

    cfg = load_config(args.config)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    tokenizer = load_tokenizer(args.ckpt, device)
    levels = tuple(cfg.model.fsq_levels)

    dataset = PretrainWindowDataset(
        cfg.train.corpus_manifest, window=cfg.train.window, horizon=0, train=False
    )
    loader = DataLoader(dataset, batch_size=64, num_workers=4)

    all_codes, sq_err, sq_scale, n_batches = [], 0.0, 0.0, 0
    for i, batch in enumerate(loader):
        if i * 64 >= args.samples:
            break
        x = batch["x"].to(device)
        recon, codes, _ = tokenizer(x)
        all_codes.append(codes.reshape(-1, len(levels)).cpu())
        sq_err += float(((recon - x) ** 2).mean())
        sq_scale += float((x ** 2).mean())
        n_batches += 1

    codes = torch.cat(all_codes)
    stats = fsq_usage_stats(codes, levels)
    stats["reconstruction_NRMSE"] = (sq_err / max(sq_scale, 1e-12)) ** 0.5

    logger.info("==== FSQ离散状态利用统计（表4.3口径）====")
    for k, v in stats.items():
        logger.info("%s: %.4f", k, v)


if __name__ == "__main__":
    main()
