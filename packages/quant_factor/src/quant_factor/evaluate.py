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

from .base import Factor, FactorContext, RunCancelled, _engine_log, _ensure_columns
from .config import EvalConfig
from .degradation import DegradationLedger, make_event
from .neutralize import (
    industry_at_level,
    ln_size,
    neutralize_cross_section,
)
from .preprocess import apply_pipeline, zscore
from .returns import forward_returns, price_columns
from .trace import MAX_SAMPLE_NAMES, describe as _trace_describe
from .universe import UniverseDef, rebalance_dates, resolve_universe


# ── annualization factors (rough trading-period counts) ─────────────────
_ANN_PERIODS = {"D": 244, "W": 48, "M": 12}


def _jf(x) -> float | None:
    """JSON-safe float for trace records (NaN/Inf/None → None)."""
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


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
        *,
        ledger: DegradationLedger | None = None,
        trace=None,
        progress_cb: Callable[[int, int], None] | None = None,
        log_cb: Callable[[str], None] | None = None,
        cancel_cb: Callable[[], bool] | None = None,
    ):
        self.reader = reader
        # Degradation台账 (设计 §6). Defaults to a fresh empty ledger so legacy
        # callers that don't pass one still get every runtime degradation
        # recorded (equivalent to "全降级放行" during the migration window §12.2).
        self.ledger = ledger if ledger is not None else DegradationLedger()
        # 运行透视 旁路采集器 (RunTrace | None). When None every埋点 is a no-op
        # guarded by `if self.trace is not None:` — zero cost, byte-identical result.
        self.trace = trace
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
            self.reader, uni_def, custom_symbols, cfg.start, cfg.end,
            ledger=self.ledger, trace=self.trace,
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

        # 运行透视 ①: 全景元信息 (设计 §2.2a). Pure side-record.
        if self.trace is not None:
            uid = (
                uni_def.get("universe_id")
                if isinstance(uni_def, dict)
                else uni_def.universe_id
            )
            self.trace.meta.update({
                "factor": factor.name,
                "direction": factor.direction,
                "requires_pit": factor.requires_pit,
                "universe_id": uid,
                "n_symbols": len(symbols),
                "n_rebalance": len(rebal),
                "rebalance_first": str(rebal[0]) if rebal else None,
                "rebalance_last": str(rebal[-1]) if rebal else None,
                "rebalance_freq": cfg.rebalance,
                "horizons": list(cfg.horizons),
                "primary_horizon": int(cfg.primary_horizon),
                "quantiles": int(cfg.quantiles),
            })

        ctx = FactorContext(self.reader, symbols, cfg.start, cfg.end, ledger=self.ledger)

        # ── 内存治理 WP3: one governed bars read per evaluation ────────────
        # required_columns 治理 (WP3.1): project the evaluation panel to what
        # the factor + the收益口径 actually use. Undeclared (存量自定义因子)
        # ⇒ legacy full-column read; 结果不变,不进台账 — log drives the
        # declaration backfill (方案 §2.4 / 风险§9.2).
        req_cols = tuple(getattr(factor, "required_columns", ()) or ())
        if req_cols:
            ctx.set_default_columns(sorted(set(req_cols) | price_columns(cfg.return_price)))
        else:
            _engine_log("factor.columns.fallback", factor=factor.name,
                        reason="factor did not declare required_columns")
            self._log(f"factor {factor.name} 未声明 required_columns,回退全列读取")
        # THE one full-window bars read (WP3.2): cached in ctx — step 1's
        # ctx.bars("post"), every horizon's forward returns and step 4 all
        # derive from this panel. 每评估 bars 全量读: H+3 → 1。
        panel = ctx.bars(adjust="post")

        # ── step 1: compute raw factor values ─────────────────────────────
        fvals = self._compute_factor(factor, ctx, rebal)
        if fvals.height == 0:
            raise ValueError("Factor returned no values — check compute() output")
        _ensure_columns(fvals, ["date", "symbol", "value"], "factor output")

        # 运行透视 ①(b): 因子原始值产出 (设计 §2.2b).
        if self.trace is not None:
            self.trace.meta["factor_mode"] = (
                "pit" if factor.requires_pit else "vectorized"
            )
            self.trace.meta["raw_value_rows"] = int(fvals.height)

        # ── step 2: per-day preprocess + neutralize ───────────────────────
        clean = self._prep_all(factor, ctx, fvals, rebal, cfg)

        # ── step 3: IC for each horizon ───────────────────────────────────
        # WP3.2: every horizon derives from the SAME panel (zero extra reads);
        # the primary horizon's frame is kept for step 4 (消除冗余重算).
        ic_by_h: dict[int, dict[str, float]] = {}
        ic_series: pl.DataFrame | None = None
        fr_primary: pl.DataFrame | None = None
        total = len(cfg.horizons)
        for i, h in enumerate(cfg.horizons):
            if self._cancelled():
                raise RunCancelled()
            self._progress(i, total + 2)
            fr = forward_returns(panel, h, cfg.return_price, ledger=self.ledger)
            ser = self._ic_series(clean, fr, rebal, cfg.ic_type, factor.direction)
            ic_by_h[int(h)] = self._ic_stats(ser, factor.direction, cfg.rebalance)
            if int(h) == int(cfg.primary_horizon):
                ic_series = ser
                fr_primary = fr

        if ic_series is None and cfg.horizons:
            # fall back to the first horizon if primary wasn't in the list
            h0 = int(cfg.horizons[0])
            fr0 = forward_returns(panel, h0, cfg.return_price, ledger=self.ledger)
            ic_series = self._ic_series(clean, fr0, rebal, cfg.ic_type, factor.direction)

        # 运行透视 ④(d): per-rebalance-day IC merge (primary horizon only).
        if self.trace is not None and ic_series is not None and ic_series.height:
            for row in ic_series.iter_rows(named=True):
                rec = self.trace.day(row["date"])
                rec["ic"] = _jf(row.get("ic"))
                rec["rank_ic"] = _jf(row.get("rank_ic"))
                rec["fwd_ret_coverage"] = int(row.get("coverage") or 0)

        # ── step 4: layered + turnover on primary horizon ────────────────
        if self._cancelled():
            raise RunCancelled()
        self._progress(total, total + 2)
        # WP3.2: reuse step 3's primary-horizon frame (identical inputs ⇒
        # identical floats); only the primary-not-in-horizons edge re-derives —
        # from the panel, still zero reads.
        fr_p = (
            fr_primary
            if fr_primary is not None
            else forward_returns(panel, cfg.primary_horizon, cfg.return_price,
                                 ledger=self.ledger)
        )
        layers = self._layered(
            clean, fr_p, rebal, cfg.quantiles, factor.direction, cfg.layer_weight,
            trace=self.trace,
        )
        turnover = (
            self._turnover(
                clean, rebal, cfg.quantiles, factor.direction, cfg, trace=self.trace
            )
            if cfg.compute_turnover
            else {"mean": 0.0, "series": [], "mean_after_cost_ann": 0.0}
        )
        self._progress(total + 2, total + 2)

        # 运行透视: per-day degradation attribution (设计 §2.2g). Cross-run
        # de-duped events (e.g. DG_SIZE_DROPPED with first_date) are stamped on
        # their first-hit day only — the UI notes this波及后续调仓日.
        if self.trace is not None:
            for ev in self.ledger.events:
                evid = ev.evidence or {}
                d = evid.get("date") or evid.get("first_date")
                if d:
                    self.trace.note_day_degradation(d, ev.code)

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
            "trace": self.trace,   # 运行透视 §2.2h; None when采集 disabled
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
            # Runtime backstop (设计 §6): even though preflight PF_NEU_IND should
            # have blocked a zero-coverage industry map under strict policy, in
            # permissive/override modes we re-confirm here and record critical.
            if ind_map is not None:
                cov = (
                    ind_map.filter(pl.col("industry").is_not_null()).height
                    / max(1, ind_map.height)
                )
                if cov == 0.0:
                    self.ledger.record(
                        make_event(
                            "DG_IND_NEUTRAL_SKIPPED",
                            stage="prep",
                            reason=(
                                f"{cfg.classification_system} 行业分类可解析覆盖率为 0"
                            ),
                            impact="残差等价于仅去均值,因子未做任何行业中性化",
                            suggested_action="导入对应行业分类数据(reference.sectors)后重跑",
                            evidence={
                                "coverage": 0.0,
                                "threshold": 0.95,
                                "affected_symbols": ind_map.height,
                            },
                        )
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

            # 运行透视 ③(c): per-rebalance-day distribution snapshots. `cap` lets
            # apply_pipeline回填 ②③ snapshots in its single code path; describe is
            # only invoked on rebalance days当采集开启, so cost stays bounded.
            tracing = self.trace is not None
            hist = self.trace.histogram_enabled if tracing else True
            cap: dict | None = {} if tracing else None
            raw_stats = (
                _trace_describe(sub["value"].to_numpy(), histogram=hist)
                if tracing
                else None
            )

            cs = apply_pipeline(
                sub,
                col="value",
                winsorize=cfg.winsorize,
                winsorize_n=cfg.winsorize_n,
                standardize=cfg.standardize,
                ledger=self.ledger,
                day=day,
                capture=cap,
                capture_histogram=hist,
            )

            n_before_neu = cs.height
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
                    ledger=self.ledger,
                    day=day,
                ).drop_nulls("value")
                if cfg.standardize == "zscore":
                    cs = cs.with_columns(
                        zscore(cs["value"], ledger=self.ledger, day=day).alias("value")
                    )

            clean_chunk = cs.select(["date", "symbol", "value"])
            chunks.append(clean_chunk)

            # ④ 中性化后(残差)分布 + 单日记录落入 trace。
            if tracing:
                rec = self.trace.day(day)
                rec.update({
                    "n_in_universe": int(sub.height),
                    "n_raw_values": int(raw_stats["n"]) if raw_stats else None,
                    "dist_raw": raw_stats,
                    "dist_after_winsorize": cap.get("after_winsorize"),
                    "dist_after_standardize": cap.get("after_standardize"),
                    "winsorize_clipped": cap.get("winsorize_clipped"),
                    "n_before_neutralize": int(n_before_neu),
                    "dist_after_neutralize": _trace_describe(
                        clean_chunk["value"].to_numpy(), histogram=hist
                    ),
                    "n_clean": int(clean_chunk.drop_nulls("value").height),
                })

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
        trace=None,
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

            # 运行透视 ⑤(e): per-day分层收益 + 多/空头抽样名单 (Q1..QQ).
            if trace is not None:
                rec = trace.day(day)
                per_q = []
                for k in range(quantiles):
                    m = q == k
                    per_q.append(_jf(np.mean(r[m])) if m.any() else None)
                rec["layer_returns"] = per_q
                syms = sub["symbol"].to_numpy()
                top_mask = q == (quantiles - 1)
                bot_mask = q == 0
                rec["top_names"] = syms[top_mask][:MAX_SAMPLE_NAMES].tolist()
                rec["bottom_names"] = syms[bot_mask][:MAX_SAMPLE_NAMES].tolist()
                rec["n_top"] = int(top_mask.sum())
                rec["n_bottom"] = int(bot_mask.sum())

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
        trace=None,
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
                # 运行透视 ④(f): per-day换手.
                if trace is not None:
                    trace.day(day)["turnover"] = _jf(t)
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
