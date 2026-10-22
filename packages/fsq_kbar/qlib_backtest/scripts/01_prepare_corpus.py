#!/usr/bin/env python3
"""第1步：构建预训练语料（第3章）.

用法：python3 scripts/01_prepare_corpus.py --config configs/corpus.yaml
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kbarfm.data.corpus import build_corpus
from kbarfm.utils.config import load_config


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="configs/corpus.yaml")
    args = parser.parse_args()
    build_corpus(load_config(args.config))


if __name__ == "__main__":
    main()
