# SuperMind execution side

`strategy_kbarfm.py` is a self-contained, single-file strategy for the SuperMind backtest engine.
It uses SuperMind's `init` / `before_trading` / `handle_bar` / `after_trading` lifecycle on a mixed account that holds stocks and index futures.

## Deployment

1. **Generate the signal file (recommended).** After the training pipeline, run:

   ```bash
   python scripts/04_generate_factor.py --config configs/factor.yaml
   ```

   This writes `outputs/factor/kbarfm_signal_data.py`.

2. **Upload the signal file** to the **root** of SuperMind "My Research". The platform supports
   user Python modules, so the backtest can simply `import` it.

3. **Create the strategy.** Under "My Strategies", create a **stock (stock + futures)** strategy and paste
   in the full `strategy_kbarfm.py`.

4. **Choose the universe** with `POOL_NAME` at the top of the file:
   - `'hs300'`: CSI 300, k = 50 / n = 5, hedged with IF futures.
   - `'csi800'`: CSI 800, k = 200 / n = 10, hedged with IC futures.

5. **Backtest settings.**
   - Frequency: **daily**. For the intraday fast lane, choose **minute**. The strategy then runs its
     main beat at 09:31 and only monitors on each half-hour mark.
   - Window: 2025-07-01 → 2026-06-18 (out of sample).
   - Initial capital: must match `stock_cash + future_cash` in `CFG['common']`. The default is
     CNY 10 M for stocks plus CNY 3 M of futures margin.

## Signal source fallback

| Tier | Source | When |
| --- | --- | --- |
| 1 | Pre-computed `kbarfm_signal_data.py` | File uploaded (recommended) |
| 2 | Online inference | `torch` importable on the platform and weights uploaded |
| 3 | Risk-adjusted momentum proxy | Neither of the above. Keeps the pipeline runnable end to end |

At start-up the strategy logs the source actually in use (`[init] ... signal source=...`).

## Platform API notes

A few SuperMind calls differ slightly across versions. The strategy guards each one: on failure it
logs the error and falls back to the platform default.

- `set_commission(...)`: the target is 0.15 % per side. The platform default (2.5 bp commission
  + 0.1 % stamp duty) is close to that.
- `set_slippage(PriceSlippage(0.002))`: 0.2 % variable slippage.
- `get_security_info(sec)`: used to re-check ST status. If it is unavailable the check is skipped;
  suspension and limit-lock checks still apply.
- `history(..., 'amount', ...)`: used by the capital-concentration monitor. If the field is missing,
  that sub-signal is set to `False` and crowding falls back to agreement between the two remaining signals.

## Risk parameters

| Module | Parameters |
| --- | --- |
| Drawdown alerts | 5 % / 8 % / 12 %. Release needs DD < 8 % for 3 consecutive days |
| De-risking | L2 → 60 % exposure, L3 → 25 %. After release, refilled weekly (two weeks to full) |
| Hedge ladder | 0 / 20 / 60 / 80 / 100 % |
| Cash tiers | 3 % / 10 % / crisis (≈ 75 %, implied by the de-risked equity weight) |
| Volatility budget | Top 20 % predicted vol × 0.5 weight; top 3 % excluded |
| Crowding | Abnormal turnover / price-volume divergence / capital concentration; any two trigger |
| Factor decay | 20-day RankIC < 0.02 for 5 days → stop opening; > 0.03 for 5 days → resume |
| Turnover control | drop-n (5 / 10), 5-day minimum holding, rebalance every 5 days |
