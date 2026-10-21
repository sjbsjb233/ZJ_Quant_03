#!/usr/bin/env python3
"""第2步：训练K线标记化器（4.1）.

用法：python3 scripts/02_train_tokenizer.py --config configs/tokenizer.yaml
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kbarfm.train.train_tokenizer import train_tokenizer
from kbarfm.utils.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/tokenizer.yaml")
    args = parser.parse_args()
    train_tokenizer(load_config(args.config))


if __name__ == "__main__":
    main()
