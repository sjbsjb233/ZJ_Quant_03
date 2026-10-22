#!/usr/bin/env bash
# =====================================================================
# 端到端流水线（图2.1五阶段）：
#   数据准备 -> K线标记化 -> 自回归预训练与因子化 -> 风控与仓位 -> 回测评估
# 在Docker容器内执行：docker compose -f docker/docker-compose.yml run kbarfm bash scripts/run_all.sh
# =====================================================================
set -e
cd "$(dirname "$0")/.."

echo "==== 阶段1 训练数据准备（第3章）===="
bash scripts/00_build_qlib_data.sh
python3 scripts/01_prepare_corpus.py --config configs/corpus.yaml

echo "==== 阶段2 K线标记化（4.1）===="
python3 scripts/02_train_tokenizer.py --config configs/tokenizer.yaml
python3 scripts/07_tokenizer_stats.py --config configs/tokenizer.yaml

echo "==== 阶段3 自回归预训练与因子化（4.2）===="
python3 scripts/03_pretrain.py --config configs/pretrain_base.yaml
python3 scripts/04_generate_factor.py --config configs/factor.yaml --pool csi300
python3 scripts/04_generate_factor.py --config configs/factor.yaml --pool csi800

echo "==== 阶段4+5 风控、仓位管理与回测评估（第5-7章）===="
python3 scripts/05_run_backtest.py --config configs/backtest_hs300.yaml
python3 scripts/05_run_backtest.py --config configs/backtest_csi800.yaml
python3 scripts/06_analysis.py --config configs/backtest_hs300.yaml --sensitivity --decompose
python3 scripts/06_analysis.py --config configs/backtest_csi800.yaml --sensitivity --decompose

echo "==== 全流程完成，结果见 outputs/ ===="
