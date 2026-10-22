# fsq_kbar — FSQ K-line Foundation Model

Reference implementation of **"FSQ Parallel Tokenization and Quantile Risk Adjustment:
a Price-Volume Forecasting Factor Strategy built on a Financial Foundation Model"**.

The package covers the full research loop:

- **Training side.** Data governance, K-line tokenization, autoregressive pre-training and factor generation.
- **Execution side.** A strategy for the SuperMind backtest engine.
- **Qlib harness.** The [`qlib_backtest/`](qlib_backtest/) folder runs the portfolio simulation in Qlib.

> Inside the ZJ Quant platform, the factor panel produced here is registered as two
> builtin factors (`FSQAlpha`, `FSQWidth` in
> [`quant_factor/builtin/fsq.py`](../quant_factor/src/quant_factor/builtin/fsq.py)).
> They then go through the same IC/IR, layering, neutralisation and correlation pipeline as every other factor.

## Design at a glance

| Dimension | Common hierarchical K-line tokenizers | This model |
| --- | --- | --- |
| Quantizer | Learnable codebook (VQ) or spherical quantization (BSQ); needs commitment / entropy losses | **Finite Scalar Quantization**: G = 4 groups, levels `[8, 8, 8, 5]`, 2,560 implicit states. No codebook and no auxiliary losses |
| Token structure | Coarse + fine residual tokens, decoded sequentially, cross-attention between levels | **Four symmetric parallel sub-tokens** share one hidden state. Four small classifiers output only 8+8+8+5 = **29** classes, with no ordering between groups |
| Reconstruction target | MSE + quantizer auxiliary loss | **Structure-constrained loss** with four terms. Huber value term (λ=1.0), log-return preservation (0.40), log-domain volume/amount (0.25), candlestick-geometry hinge (0.15) |
| Inference | Temperature / top-p Monte-Carlo paths, averaged | **Deterministic single forward pass** of a quantile head: q0.1 / q0.5 / q0.9 of the next H = 10 day return |
| Training objective | Multi-level cross-entropy | Σ group cross-entropy + **0.5 × pinball loss**, with self-sampling against exposure bias |
| Factor | Convert a sampled price path to a return | **α = q0.5 / (q0.9 − q0.1 + 1e-3)**, then 1%/99% winsorisation and a cross-sectional z-score |
| Risk measure | — | Band width → forward realised volatility through an isotonic map. The map is fitted on the training window and frozen out of sample |

## Layout

```
fsq_kbar/
├── kbarfm/                  # core package
│   ├── data/                # corpus governance: missing-field policy, 3-step filtering, rebalancing
│   ├── model/               # FSQ quantizer, tokenizer, parallel-group decoder backbone, heads, losses
│   ├── train/               # tokenizer training and pre-training (BF16, gradient checkpointing, accumulation)
│   ├── factor/              # inference engine, risk-adjusted signal, volatility calibration
│   ├── risk/                # crowding monitor, 3-level drawdown alerts, tiered index-futures hedge
│   ├── portfolio/           # top-k construction, drop-n rebalancing, 3-tier cash buffer
│   └── eval/                # IC / RankIC, decile layering, decay, performance metrics
├── scripts/                 # 01 corpus → 02 tokenizer → 03 pre-train → 05 calibrate → 04 factor → 06 eval → 08 walk-forward
├── configs/                 # every hyper-parameter, one file per stage
├── docker/                  # CUDA training image + compose service
├── supermind/               # execution-side strategy + deployment notes
└── qlib_backtest/           # Qlib data/backtest harness (first-generation codebase, self-contained)
```

## Training pipeline

Everything runs in one container. The reference environment is Python 3.12.4 and PyTorch 2.7.0 on a single 24 GB RTX 4090 / 4090D.

```bash
cd docker && docker compose run train all   # or one stage: corpus|tokenizer|pretrain|calibrate|factor|eval
```

Or on the host:

```bash
pip install -r requirements.txt

# 1. Corpus: all A-shares (incl. delisted), 7 frequencies, cut-off 2025-06-30
python scripts/01_build_corpus.py --config configs/corpus.yaml

# 2. Tokenizer: 2-layer d=192 Transformer auto-encoder + FSQ [8, 8, 8, 5]
python scripts/02_train_tokenizer.py --config configs/tokenizer.yaml

# 3. Foundation model pre-training (base: 10 layers / d=640 / 41M; compact: 8 layers / d=448 / 16M)
python scripts/03_pretrain.py --config configs/pretrain_base.yaml

# 4. Volatility calibrator: fitted inside the training window, frozen afterwards
python scripts/05_calibrate_vol.py --config configs/factor.yaml \
    --train-start 2024-01-01 --train-end 2025-06-30

# 5. Out-of-sample factor panel + SuperMind signal file
python scripts/04_generate_factor.py --config configs/factor.yaml

# 6. Out-of-sample factor tests (IC / RankIC / layering / decay)
python scripts/06_eval_factor.py --config configs/factor.yaml

# 7. Optional: yearly walk-forward retraining (vintages 2017–2025)
python scripts/08_walkforward.py --config configs/walkforward.yaml
```

Model sizes as built by `kbarfm.model.build_model`:

| Component | Layers | d_model | FFN | Heads | Parameters |
| --- | ---: | ---: | ---: | ---: | ---: |
| Tokenizer (encoder + decoder) | 2 + 2 | 192 | 768 | 6 | 1.78 M |
| Foundation model · compact | 8 | 448 | 1,344 | 7 | 16.18 M |
| Foundation model · base | 10 | 640 | 1,920 | 10 | 41.15 M |

## Plugging into the platform

```bash
# after step 5
mkdir -p ../../deploy/factor-store/fsq
cp outputs/factor/factor_panel.parquet ../../deploy/factor-store/fsq/
# or point the factor service at any location:
export FSQ_FACTOR_PANEL=/path/to/factor_panel.parquet
```

`FSQAlpha` (the risk-adjusted score) and `FSQWidth` (forecast-band width) then show up in the
**Factor Library**. From there they can be evaluated on any universe, neutralised, and checked for
correlation against the hand-crafted factors.

## Execution side (SuperMind)

See [`supermind/README.md`](supermind/README.md). In short:

1. Upload `outputs/factor/kbarfm_signal_data.py` to the root of "My Research".
2. Paste `supermind/strategy_kbarfm.py` in as a stock + index-futures strategy.
3. Backtest at daily frequency.

## Data conventions

- The pre-training corpus ends on **2025-06-30**. The out-of-sample backtest starts on **2025-07-01**, and no parameter is tuned on the test window.
- Factors are computed on **post-adjusted** prices. Fills use real, unadjusted prices.
- Trading cost is a flat 0.15 % per side. Suspended, ST and limit-locked stocks are filtered before ranking.
- Market data, checkpoints and outputs are not part of the repository. `data/`, `checkpoints/` and `outputs/` are git-ignored.
