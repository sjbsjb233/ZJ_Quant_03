#!/usr/bin/env python3
"""步骤1：构建预训练语料（报告3.2节）.

原始K线目录 -> 缺失值字段化处理 -> 三步低质量过滤 -> 片段分片落盘。

用法：
    python scripts/01_build_corpus.py --config configs/corpus.yaml
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kbarfm.data.corpus import CorpusBuilder, corpus_summary
from kbarfm.utils.config import load_config
from kbarfm.utils.logging import get_logger

log = get_logger("scripts.build_corpus")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/corpus.yaml")
    ap.add_argument("--freqs", nargs="*", default=None, help="仅处理指定频率")
    args = ap.parse_args()

    cfg = load_config(args.config)
    builder = CorpusBuilder(
        raw_dir=cfg.raw_dir,
        out_dir=cfg.out_dir,
        cutoff=cfg.get_path("cutoff", "2025-06-30"),
    )
    meta = builder.build(freqs=args.freqs or cfg.get_path("freqs"))
    if meta.empty:
        log.warning("未产出任何片段，请检查原始数据目录结构 raw_dir/<freq>/<symbol>.parquet")
        return
    log.info("语料概览（表3.2口径）:\n%s", corpus_summary(meta))


if __name__ == "__main__":
    main()
