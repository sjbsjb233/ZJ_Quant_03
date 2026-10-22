#!/usr/bin/env python3
"""第3步：自回归预训练（4.2）.

用法：
  python3 scripts/03_pretrain.py --config configs/pretrain_base.yaml     # base规格
  python3 scripts/03_pretrain.py --config configs/pretrain_compact.yaml  # compact规格
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kbarfm.train.pretrain import pretrain
from kbarfm.utils.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/pretrain_base.yaml")
    args = parser.parse_args()
    pretrain(load_config(args.config))


if __name__ == "__main__":
    main()
