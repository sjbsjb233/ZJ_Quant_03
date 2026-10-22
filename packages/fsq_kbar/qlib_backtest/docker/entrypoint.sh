#!/usr/bin/env bash
# 容器入口：默认进入 bash，也可直接透传训练/回测命令，例如
#   docker compose run kbarfm python3 scripts/03_pretrain.py --config configs/pretrain_base.yaml
set -e
exec "$@"
