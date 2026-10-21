#!/usr/bin/env bash
# 容器内训练流水线入口：按阶段执行 scripts/01 -> 06
# 用法：entrypoint.sh [all|corpus|tokenizer|pretrain|calibrate|factor|eval]
set -euo pipefail
cd /workspace/fsq-kbar-system

stage="${1:-all}"

run_corpus()    { python3 scripts/01_build_corpus.py    --config configs/corpus.yaml; }
run_tokenizer() { python3 scripts/02_train_tokenizer.py --config configs/tokenizer.yaml; }
run_pretrain()  { python3 scripts/03_pretrain.py        --config configs/pretrain_base.yaml; }
run_calibrate() { python3 scripts/05_calibrate_vol.py   --config configs/factor.yaml; }
run_factor()    { python3 scripts/04_generate_factor.py --config configs/factor.yaml; }
run_eval()      { python3 scripts/06_eval_factor.py     --config configs/factor.yaml; }

case "$stage" in
  corpus)    run_corpus ;;
  tokenizer) run_tokenizer ;;
  pretrain)  run_pretrain ;;
  calibrate) run_calibrate ;;
  factor)    run_factor ;;
  eval)      run_eval ;;
  all)       run_corpus; run_tokenizer; run_pretrain
             run_calibrate; run_factor; run_eval ;;
  *) echo "未知阶段: $stage (可选 all|corpus|tokenizer|pretrain|calibrate|factor|eval)"
     exit 1 ;;
esac
