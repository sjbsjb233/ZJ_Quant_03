"""FactorEvaluator — the engine that turns a Factor + EvalConfig into:

  - ic_series       : [date, ic, rank_ic, coverage]
  - ic_by_horizon   : {h: stats}  for IC decay
  - layers          : [date, quantile, ret]  for layered backtest
  - turnover        : {mean, series, mean_after_cost}
  - summary         : key scalars persisted in eval_summary
  - clean_values    : the post-preprocessing cross-section values (used by
                      correlation when method="factor_value")

Order matters:
  ① compute raw factor values (vectorized OR per-day for PIT)
  ② per rebalance date: winsorize -> zscore -> neutralize -> zscore
  ③ for each horizon, compute IC; main horizon also drives layered + turnover
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Callable, Iterable

import numpy as np
import polars as pl

from .base import Factor, FactorContext, RunCancelled, _ensure_columns
from .config import EvalConfig
from .neutralize import (
    industry_at_level,
    ln_size,
    neutralize_cross_section,
)
from .preprocess import apply_pipeline, zscore
from .returns import forward_returns
from .universe import UniverseDef, rebalance_dates, resolve_universe


# ── annualization factors (rough trading-period counts) ─────────────────
_ANN_PERIODS = {"D": 244, "W": 48, "M": 12}


def _ann_periods(rebalance: str) -> int:
    rb = (rebalance or "W").upper()
    if rb in _ANN_PERIODS:
        return _ANN_PERIODS[rb]
    if rb.endswith("D") and rb[:-1].isdigit():
        n = max(1, int(rb[:-1]))
        return max(1, 244 // n)
    return 48


# ─────────────────────────────────────────────────────────────────────────────
class FactorEvaluator:
    def __init__(
        self,
        reader,
        progress_cb: Callable[[int, int], None] | None = None,
        log_cb: Callable[[str], None] | None = None,
        cancel_cb: Callable[[], bool] | None = None,
    ):
        self.reader = reader
        self._progress = progress_cb or (lambda c, t: None)
        self._log = log_cb or (lambda m: None)
        self._cancelled = cancel_cb or (lambda: False)

    # ── public ───────────────────────────────────────────────────────────
    def evaluate(
        self,
        factor: Factor,
        cfg: EvalConfig,
        uni_def: dict | UniverseDef,
        custom_symbols: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        self._log(
            f"starting evaluation factor={factor.name} universe={uni_def.get('universe_id') if isinstance(uni_def, dict) else uni_def.universe_id}"
        )
        uni = resolve_universe(
            self.reader, uni_def, custom_symbols, cfg.start, cfg.end
        )
        if uni.height == 0:
            raise ValueError("Universe resolved to zero rows — check pool definition")

        symbols = sorted(uni["symbol"].unique().to_list())
        rebal = rebalance_dates(uni, cfg.rebalance)
        if not rebal:
            raise ValueError("No rebalance dates — check region & frequency")
        self._log(
            f"universe {len(symbols)} symbols, {len(rebal)} rebalance dates "
            f"({rebal[0]} → {rebal[-1]})"
        )

        ctx = FactorContext(self.reader, symbols, cfg.start, cfg.end)

        # ── step 1: compute raw factor values ─────────────────────────────
        fvals = self._compute_factor(factor, ctx, rebal)
        if fvals.height == 0:
            raise ValueError("Factor returned no values — check compute() output")
        _ensure_columns(fvals, ["date", "symbol", "value"], "factor output")

        # ── step 2: per-day preprocess + neutralize ───────────────────────
        clean = self._prep_all(factor, ctx, fvals, rebal, cfg)

        # ── step 3: IC for each horizon ───────────────────────────────────
        ic_by_h: dict[int, dict[str, float]] = {}
        ic_series: pl.DataFrame | None = None
        total = len(cfg.horizons)
        for i, h in enumerate(cfg.horizons):
            if self._cancelled():
                raise RunCancelled()
            self._progress(i, total + 2)
            fr = forward_returns(
                self.reader, symbols, cfg.start, cfg.end, h, cfg.return_price
            )
            ser = self._ic_series(clean, fr, rebal, cfg.ic_type, factor.direction)
            ic_by_h[int(h)] = self._ic_stats(ser, factor.direction, cfg.rebalance)
            if int(h) == int(cfg.primary_horizon):
                ic_series = ser

        if ic_series is None and cfg.horizons:
            # fall back to the first horizon if primary wasn't in the list
            h0 = int(cfg.horizons[0])
            fr0 = forward_returns(
                self.reader, symbols, cfg.start, cfg.end, h0, cfg.return_price
            )
            ic_series = self._ic_series(clean, fr0, rebal, cfg.ic_type, factor.direction)

        # ── step 4: layered + turnover on primary horizon ────────────────
        if self._cancelled():
            raise RunCancelled()
        self._progress(total, total + 2)
        fr_p = forward_returns(
            self.reader,
            symbols,
            cfg.start,
            cfg.end,
            cfg.primary_horizon,
            cfg.return_price,
        )
        layers = self._layered(
            clean, fr_p, rebal, cfg.quantiles, factor.direction, cfg.layer_weight
        )
        turnover = (
            self._turnover(clean, rebal, cfg.quantiles, factor.direction, cfg)
            if cfg.compute_turnover
            else {"mean": 0.0, "series": [], "mean_after_cost_ann": 0.0}
        )
        self._progress(total + 2, total + 2)

        # ── summary ──────────────────────────────────────────────────────
        primary_stats = dict(ic_by_h.get(int(cfg.primary_horizon), {}))
        summary = {
            "rank_ic_mean": primary_stats.get("rank_ic_mean", 0.0),
            "ic_mean": primary_stats.get("ic_mean", 0.0),
            "icir": primary_stats.get("icir", 0.0),
            "icir_annual": primary_stats.get("icir_annual", 0.0),
            "ic_t_stat": primary_stats.get("ic_t_stat", 0.0),
            "ic_win_rate": primary_stats.get("ic_win_rate", 0.0),
            "ls_annual_return": layers.get("long_short_annual", 0.0),
            "ls_sharpe": layers.get("long_short_sharpe", 0.0),
            "ls_max_drawdown": layers.get("long_short_max_drawdown", 0.0),
            "long_annual_return": layers.get("long_annual_return", 0.0),
            "short_annual_return": layers.get("short_annual_return", 0.0),
            "monotonic": layers.get("monotonic", False),
            "turnover": float(turnover.get("mean", 0.0)),
            "turnover_after_cost_ann": float(
                turnover.get("mean_after_cost_ann", 0.0)
            ),
            "coverage": primary_stats.get("coverage", 0.0),
        }

        return {
            "ic_series": ic_series if ic_series is not None else pl.DataFrame(),
            "ic_by_horizon": ic_by_h,
            "layers": layers,
            "turnover": turnover,
            "summary": summary,
            "clean_values": clean,
            "rebalance_dates": rebal,
            "symbol_count": len(symbols),
        }

    # ── step 1: dispatch to vectorized vs PIT compute ────────────────────
    def _compute_factor(
        self,
        factor: Factor,
        ctx: FactorContext,
        rebal: list[dt.date],
    ) -> pl.DataFrame:
        if factor.requires_pit:
            chunks: list[pl.DataFrame] = []
            for d in rebal:
                if self._cancelled():
                    raise RunCancelled()
                xs = factor.compute_cross_section(ctx, d)
                if xs is None or xs.height == 0:
                    continue
                if "date" not in xs.columns:
                    xs = xs.with_columns(pl.lit(d).cast(pl.Date).alias("date"))
                _ensure_columns(xs, ["symbol", "value"], "compute_cross_section()")
                chunks.append(xs.select(["date", "symbol", "value"]))
            return (
                pl.concat(chunks, how="vertical")
                if chunks
                else pl.DataFrame(
                    schema={"date": pl.Date, "symbol": pl.Utf8, "value": pl.Float64}
                )
            )
        else:
            out = factor.compute(ctx)
            return out.select(["date", "symbol", "value"])

    # ── step 2: per-day preprocess + neutralize ──────────────────────────
    def _prep_all(
        self,
        factor: Factor,
        ctx: FactorContext,
        fvals: pl.DataFrame,
        rebal: list[dt.date],
        cfg: EvalConfig,
    ) -> pl.DataFrame:
        # Pre-build industry map for the whole symbol set (cheap; one lookup).
        ind_map: pl.DataFrame | None = None
        if cfg.neutralize_industry:
            ind_map = industry_at_level(
                self.reader,
                ctx.symbols,
                cfg.classification_system,
                cfg.industry_level,
            )

        chunks: list[pl.DataFrame] = []
        date_set = set(rebal)
        for d, sub in fvals.group_by("date", maintain_order=True):
            day = d[0] if isinstance(d, tuple) else d
            if day not in date_set:
                continue
            if sub.height < 10:
                continue
            if self._cancelled():
                raise RunCancelled()

            cs = apply_pipeline(
                sub,
                col="value",
                winsorize=cfg.winsorize,
                winsorize_n=cfg.winsorize_n,
                standardize=cfg.standardize,
            )

            if cfg.neutralize_size or cfg.neutralize_industry:
                if cfg.neutralize_size:
                    ln = ln_size(
                        self.reader,
                        cs["symbol"].to_list(),
                        day,
                        field=cfg.size_field,
                    )
                    cs = cs.join(ln, on="symbol", how="left")
                if cfg.neutralize_industry and ind_map is not None:
                    cs = cs.join(ind_map, on="symbol", how="left")
                cs = neutralize_cross_section(
                    cs,
                    use_size=cfg.neutralize_size,
                    use_industry=cfg.neutralize_industry,
                ).drop_nulls("value")
                if cfg.standardize == "zscore":
                    cs = cs.with_columns(zscore(cs["value"]).alias("value"))

            chunks.append(cs.select(["date", "symbol", "value"]))

        return (
            pl.concat(chunks, how="vertical")
            if chunks
            else pl.DataFrame(
                schema={"date": pl.Date, "symbol": pl.Utf8, "value": pl.Float64}
            )
        )

    # ── step 3: IC series ────────────────────────────────────────────────
    def _ic_series(
        self,
        clean: pl.DataFrame,
        fwd: pl.DataFrame,
        rebal: list[dt.date],
        ic_type: str,
        direction: int,
    ) -> pl.DataFrame:
        if clean.height == 0 or fwd.height == 0:
            return pl.DataFrame(
                {"date": [], "ic": [], "rank_ic": [], "coverage": []},
                schema={
                    "date": pl.Date,
                    "ic": pl.Float64,
                    "rank_ic": pl.Float64,
                    "coverage": pl.Int64,
                },
            )

        joined = clean.join(fwd, on=["date", "symbol"], how="inner").drop_nulls(
            ["value", "fwd_ret"]
        )

        rows = []
        date_set = set(rebal)
        for d, sub in joined.group_by("date", maintain_order=True):
            day = d[0] if isinstance(d, tuple) else d
            if day not in date_set:
                continue
            if sub.height < 5:
                continue
            x = sub["value"].to_numpy().astype(float)
            y = sub["fwd_ret"].to_numpy().astype(float)
            ic_val = float(np.corrcoef(x, y)[0, 1]) if x.size > 1 else float("nan")
            # Rank IC = Pearson of ranks (≡ Spearman)
            xr = np.argsort(np.argsort(x)).astype(float)
            yr = np.argsort(np.argsort(y)).astype(float)
            rank_ic_val = (
                float(np.corrcoef(xr, yr)[0, 1]) if x.size > 1 else float("nan")
            )
            # Apply direction sign — design says +1/-1 means "natural"
            # interpretation; we let the user see the raw IC and signal the
            # direction separately.
            rows.append(
                {
                    "date": day,
                    "ic": ic_val,
                    "rank_ic": rank_ic_val,
                    "coverage": int(sub.height),
                }
            )

        if not rows:
            return pl.DataFrame(
                {"date": [], "ic": [], "rank_ic": [], "coverage": []},
                schema={
                    "date": pl.Date,
                    "ic": pl.Float64,
                    "rank_ic": pl.Float64,
                    "coverage": pl.Int64,
                },
            )
        return pl.DataFrame(rows).sort("date")

    def _ic_stats(
        self, ser: pl.DataFrame, direction: int, rebalance: str
    ) -> dict[str, float]:
        if ser.height == 0:
            return {
                "rank_ic_mean": 0.0,
                "ic_mean": 0.0,
                "ic_std": 0.0,
                "icir": 0.0,
                "icir_annual": 0.0,
                "ic_t_stat": 0.0,
                "ic_win_rate": 0.0,
                "coverage": 0.0,
            }
        rank_ic = ser["rank_ic"].to_numpy().astype(float)
        ic = ser["ic"].to_numpy().astype(float)
        rank_ic = rank_ic[np.isfinite(rank_ic)]
        ic = ic[np.isfinite(ic)]

        def _stats(arr: np.ndarray) -> tuple[float, float, float, float]:
            if arr.size < 2:
                return 0.0, 0.0, 0.0, 0.0
            mean = float(np.mean(arr))
            std = float(np.std(arr, ddof=1))
            ratio = mean / std if std > 0 else 0.0
            t_stat = ratio * math.sqrt(arr.size)
            return mean, std, ratio, t_stat

        rmean, rstd, rir, rt = _stats(rank_ic)
        imean, istd, _, _ = _stats(ic)
        ann = rir * math.sqrt(_ann_periods(rebalance))
        return {
            "rank_ic_mean": rmean,
            "ic_mean": imean,
            "ic_std": rstd,
            "icir": rir,
            "icir_annual": ann,
            "ic_t_stat": rt,
            "ic_win_rate": float((rank_ic > 0).mean()) if rank_ic.size else 0.0,
            "coverage": float(ser["coverage"].mean()),
        }

    # ── step 4a: layered backtest ────────────────────────────────────────
    def _layered(
        self,
        clean: pl.DataFrame,
        fwd: pl.DataFrame,
        rebal: list[dt.date],
        quantiles: int,
        direction: int,
        weight: str,
    ) -> dict[str, Any]:
        if clean.height == 0 or fwd.height == 0:
            return {"layers": [], "monotonic": False}

        joined = clean.join(fwd, on=["date", "symbol"], how="inner").drop_nulls(
            ["value", "fwd_ret"]
        )

        date_set = set(rebal)
        # Per-day quantile assignment + group mean return.
        layer_rows: list[dict[str, Any]] = []
        for d, sub in joined.group_by("date", maintain_order=True):
            day = d[0] if isinstance(d, tuple) else d
            if day not in date_set or sub.height < max(quantiles * 2, 10):
                continue
            v = sub["value"].to_numpy().astype(float)
            r = sub["fwd_ret"].to_numpy().astype(float)
            ranks = np.argsort(np.argsort(v)).astype(float)
            # bin into [0, quantiles)
            q = np.minimum(
                (ranks / len(ranks) * quantiles).astype(int), quantiles - 1
            )
            for k in range(quantiles):
                m = q == k
                if not m.any():
                    continue
                ret = float(np.mean(r[m]))
                layer_rows.append(
                    {"date": day, "quantile": int(k + 1), "ret": ret}
                )

        if not layer_rows:
            return {"layers": [], "monotonic": False}

        layers_df = pl.DataFrame(layer_rows).sort(["date", "quantile"])

        # Stats
        agg = (
            layers_df.group_by("quantile")
            .agg(pl.col("ret").mean().alias("ret_mean"))
            .sort("quantile")
        )
        means = agg["ret_mean"].to_numpy().astype(float)
        # Monotonic if mean of top > mean of bottom and pairwise consistent up to one inversion
        top, bot = means[-1], means[0]
        diffs = np.diff(means)
        monotone_pos = (diffs >= 0).sum()
        monotone_neg = (diffs <= 0).sum()
        monotonic = (
            (top > bot and monotone_pos >= len(diffs) - 1)
            or (bot > top and monotone_neg >= len(diffs) - 1)
        )

        # Long-short: top minus bottom of the direction.
        top_ret = (
            layers_df.filter(pl.col("quantile") == quantiles)
            .group_by("date")
            .agg(pl.col("ret").mean())
            .sort("date")
        )
        bot_ret = (
            layers_df.filter(pl.col("quantile") == 1)
            .group_by("date")
            .agg(pl.col("ret").mean())
            .sort("date")
        )
        merged = top_ret.join(bot_ret, on="date", suffix="_bot")
        ls = (merged["ret"] - merged["ret_bot"]).to_numpy().astype(float)
        if direction < 0:
            ls = -ls
            long_mean = float(np.mean(bot_ret["ret"].to_numpy()))
            short_mean = float(np.mean(top_ret["ret"].to_numpy()))
        else:
            long_mean = float(np.mean(top_ret["ret"].to_numpy()))
            short_mean = float(np.mean(bot_ret["ret"].to_numpy()))

        ann_periods = _ann_periods("D")  # ls already per-rebalance-day; coarse scale
        # Use len(merged) periods.
        ls_mean = float(np.mean(ls)) if ls.size else 0.0
        ls_std = float(np.std(ls, ddof=1)) if ls.size > 1 else 0.0
        # Roughly annualize using rebalance counts/year inferred from spacing.
        if merged.height > 1:
            dates = merged["date"].to_list()
            span_days = max(1, (dates[-1] - dates[0]).days)
            periods_per_year = (merged.height - 1) * 365.0 / span_days
        else:
            periods_per_year = 48.0
        ls_ann = ls_mean * periods_per_year
        ls_sharpe = (
            (ls_mean / ls_std) * math.sqrt(periods_per_year) if ls_std > 0 else 0.0
        )
        cum = np.cumsum(ls)
        peak = np.maximum.accumulate(cum)
        ls_mdd = float((cum - peak).min()) if cum.size else 0.0

        return {
            "layers": layers_df.to_dicts(),  # raw [date, quantile, ret]
            "layer_means": [float(x) for x in means],
            "monotonic": bool(monotonic),
            "long_short_series": ls.tolist(),
            "long_short_annual": float(ls_ann),
            "long_short_sharpe": float(ls_sharpe),
            "long_short_max_drawdown": float(ls_mdd),
            "long_annual_return": float(long_mean * periods_per_year),
            "short_annual_return": float(short_mean * periods_per_year),
        }

    # ── step 4b: turnover ────────────────────────────────────────────────
    def _turnover(
        self,
        clean: pl.DataFrame,
        rebal: list[dt.date],
        quantiles: int,
        direction: int,
        cfg: EvalConfig,
    ) -> dict[str, Any]:
        # Top group = direction * factor highest.
        if clean.height == 0:
            return {"mean": 0.0, "series": [], "mean_after_cost_ann": 0.0}

        prev: set[str] = set()
        series: list[dict[str, Any]] = []
        date_set = set(rebal)
        for d, sub in clean.group_by("date", maintain_order=True):
            day = d[0] if isinstance(d, tuple) else d
            if day not in date_set or sub.height < quantiles * 2:
                continue
            v = sub["value"].to_numpy().astype(float)
            order = np.argsort(v)
            # top group = highest factor values if direction +1, else lowest.
            if direction < 0:
                idx = order[: max(1, sub.height // quantiles)]
            else:
                idx = order[-max(1, sub.height // quantiles) :]
            top_syms = set(sub["symbol"].to_numpy()[idx].tolist())
            if prev:
                changed = len(prev.symmetric_difference(top_syms))
                tot = max(1, len(prev) + len(top_syms))
                t = changed / tot
                series.append({"date": day, "turnover": float(t)})
            prev = top_syms

        if not series:
            return {"mean": 0.0, "series": [], "mean_after_cost_ann": 0.0}
        mean_to = float(np.mean([r["turnover"] for r in series]))

        # Annualized turnover cost subtracted from a notional ann-return.
        # Per-period one-sided cost ~ cost_bps / 10000 * turnover.
        # Annual = per_period_cost * periods_per_year.
        if len(series) > 1:
            dates = [r["date"] for r in series]
            span_days = max(1, (dates[-1] - dates[0]).days)
            per_year = (len(series) - 1) * 365.0 / span_days
        else:
            per_year = 48.0
        mean_after_cost_ann = mean_to * (cfg.cost_bps / 10000.0) * per_year

        return {
            "mean": mean_to,
            "series": series,
            "mean_after_cost_ann": mean_after_cost_ann,
        }
