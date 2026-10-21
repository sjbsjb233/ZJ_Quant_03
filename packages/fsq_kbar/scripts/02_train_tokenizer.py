#!/usr/bin/env python3
"""步骤2：训练K线标记化器（报告4.1，表4.2）.

用法：
    python scripts/02_train_tokenizer.py --config configs/tokenizer.yaml
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kbarfm.train.train_tokenizer import train_tokenizer
from kbarfm.utils.config import load_config


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/tokenizer.yaml")
    args = ap.parse_args()
    ckpt = train_tokenizer(load_config(args.config))
    print(f"标记化器训练完成: {ckpt}")


if __name__ == "__main__":
    main()
