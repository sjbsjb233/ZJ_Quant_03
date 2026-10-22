#!/usr/bin/env python3
"""步骤3：基座模型自回归预训练（报告4.2，附录A）.

用法：
    python scripts/03_pretrain.py --config configs/pretrain_base.yaml
    python scripts/03_pretrain.py --config configs/pretrain_compact.yaml
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kbarfm.train.pretrain import pretrain
from kbarfm.utils.config import load_config


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/pretrain_base.yaml")
    args = ap.parse_args()
    ckpt = pretrain(load_config(args.config))
    print(f"预训练完成: {ckpt}")


if __name__ == "__main__":
    main()
