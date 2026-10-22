# qlib_backtest — Qlib research & backtest harness

This is the first-generation FSQ codebase. Market data access and portfolio simulation both go through
[Qlib](https://github.com/microsoft/qlib) (Yang et al., 2020). The model is written in PyTorch, and everything runs
in Docker on a single NVIDIA RTX 4090 / 4090D (24 GB).

It is self-contained: it ships its own `kbarfm` package and must be run from this folder in its own environment.
The newer engine one level up, `../kbarfm`, is the maintained version of the model and factor code. This folder is kept
for the Qlib-native **two-pass risk backtest**, **attribution** and **sensitivity** analysis.

## Layout

```
qlib_backtest/
├── docker/                      # CUDA image + nvidia-container-toolkit compose
├── requirements.txt             # numpy 1.26.2 / pandas 2.2.2 / pyqlib ...
├── configs/
│   ├── corpus.yaml              # corpus scope, per-frequency filter thresholds, preprocessing
│   ├── tokenizer.yaml           # tokenizer + structure-constrained loss weights
│   ├── pretrain_base.yaml       # base: 10 layers / d=640 / 41M
│   ├── pretrain_compact.yaml    # compact: 8 layers / d=448 / 16M
│   ├── factor.yaml              # 90-day lookback, H=10, eps=1e-3
│   ├── backtest_hs300.yaml      # k=50, n=5, IF hedge
│   └── backtest_csi800.yaml     # k=200, n=10, IC hedge
├── kbarfm/
│   ├── data/                    # Qlib OHLCVA source, missing-field policy, filters, normalisation, rebalancing
│   ├── model/                   # FSQ, tokenizer, RoPE, decoder backbone, parallel heads, losses
│   ├── train/                   # AdamW + warmup/cosine, tokenizer training, pre-training
│   ├── factor/                  # deterministic inference, risk-adjusted signal + risk overlays
│   ├── risk/                    # volatility calibration (PAVA), crowding, drawdown state machine, factor monitor, hedging
│   ├── portfolio/strategy.py    # Qlib TopkDropoutStrategy extension: 5-day beat, cash ladder, defensive freeze
│   └── backtest/                # two-pass risk backtest, metrics, IC analysis, attribution, sensitivity
└── scripts/                     # 00 → 07 end-to-end pipeline
```

## Quick start

```bash
# 1. Build and enter the container (host needs the NVIDIA driver + nvidia-container-toolkit)
cd docker && docker compose build && docker compose run kbarfm bash

# 2. End to end (inside the container)
bash scripts/run_all.sh

# or step by step
bash    scripts/00_build_qlib_data.sh                                       # Qlib data + CSI 800 constituents
python3 scripts/01_prepare_corpus.py  --config configs/corpus.yaml          # corpus governance
python3 scripts/02_train_tokenizer.py --config configs/tokenizer.yaml       # FSQ tokenizer
python3 scripts/03_pretrain.py        --config configs/pretrain_base.yaml   # pre-training
python3 scripts/04_generate_factor.py --config configs/factor.yaml --pool csi300
python3 scripts/05_run_backtest.py    --config configs/backtest_hs300.yaml
python3 scripts/06_analysis.py        --config configs/backtest_hs300.yaml --sensitivity --decompose
```

## Design notes

- **Strict out-of-sample.**
  - The pre-training corpus ends on 2025-06-30 and the backtest runs from 2025-07-01 to 2026-06-18.
  - Inference only sees the 90 daily bars up to each date.
- **Deterministic inference.**
  - The quantile head emits q0.1 / q0.5 / q0.9 in one pass, with no Monte-Carlo sampling.
  - The factor and the forward-volatility estimate share that pass.
- **Two-pass risk backtest.**
  - Pass 1 produces a raw NAV, and the drawdown state machine and factor monitor are replayed offline on it.
  - Pass 2 reruns with those risk states frozen and overlays the hedge ladder.
  - This is equivalent day by day to an online state machine and keeps Qlib's fill conventions unchanged.
- **Risk budget before ranking.** These overlays act on the score (`factor/signal.py: apply_risk_overlays`), so excluded names never enter the top-k ranking:
  - top 20 % predicted volatility is down-weighted;
  - extreme names are excluded;
  - crowded names are excluded.
- **Quantile sensitivity.** Extend `quantiles` to `[0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95]` in the pre-train and factor configs, then regenerate the panel.
- **External input.** `data/industry_map.csv` has two columns, instrument and SW level-1 industry. It feeds rebalancing and industry attribution; if it is missing, both fall back to "UNK".
