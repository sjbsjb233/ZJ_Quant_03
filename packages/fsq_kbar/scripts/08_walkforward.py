#!/usr/bin/env python3
"""Walk-forward滚动历史回测驱动（参赛报告7.6.4节、附录E）.

按年度设置模型节点（Vintage 2017—2025）：每一节点仅使用其语料截止日
（测试年度前一自然年末）之前的数据重建预训练语料，并依次重训K线标记化器、
重训基座模型与分位数预测头、重拟合波动率校准器，随后仅在截止日之后的
测试年度内生成因子；全部节点共用 configs/walkforward.yaml 固化的同一份
冻结配置（架构、训练超参、因子构成与组合口径均沿用正文设定），逐年零调参。

各节点语料与产物按年度目录隔离，已完成节点自动跳过、支持断点续跑；
全部节点完成后将各测试段因子面板首尾拼接（互不重叠）为连续历史面板，
供Qlib按附录B统一协议逐年回测并拼接净值，与严格样本外区间无缝衔接。

用法：
    python scripts/08_walkforward.py --config configs/walkforward.yaml
    python scripts/08_walkforward.py --config configs/walkforward.yaml --vintages 2021:2025
"""

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kbarfm.data.corpus import CorpusBuilder, corpus_summary
from kbarfm.data.panel import load_panel
from kbarfm.factor.inference import FactorEngine
from kbarfm.factor.signal import build_factor_frame
from kbarfm.factor.volatility import VolatilityCalibrator, realized_volatility
from kbarfm.model.io import load_foundation, load_tokenizer
from kbarfm.train.pretrain import pretrain
from kbarfm.train.train_tokenizer import train_tokenizer
from kbarfm.utils.config import load_config
from kbarfm.utils.logging import get_logger

log = get_logger("scripts.walkforward")


def vintage_spec(year, cfg):
    """节点时间边界：语料截止=测试年度前一自然年末，测试段=该自然年度."""
    cutoff = f"{year - 1}-12-31"
    test_end = f"{year}-12-31"
    if year == cfg.get_path("vintages.last", 2025):
        test_end = cfg.get_path("vintages.final_test_end", test_end)
    return cutoff, f"{year}-01-01", test_end


def build_vintage_corpus(year, cutoff, cfg):
    """步骤①：仅用截止日前数据重建语料（治理口径同scripts/01，含退市标的）."""
    corpus_dir = Path(cfg.corpus_root) / str(year)
    if (corpus_dir / "segments_meta.parquet").exists():
        log.info("① 语料已存在，跳过重建: %s", corpus_dir)
        return corpus_dir
    ccfg = load_config(cfg.base_configs.corpus,
                       overrides={"cutoff": cutoff, "out_dir": str(corpus_dir)})
    meta = CorpusBuilder(raw_dir=ccfg.raw_dir, out_dir=ccfg.out_dir,
                         cutoff=ccfg.cutoff).build(freqs=ccfg.get_path("freqs"))
    log.info("① 语料重建完成（截止%s）:\n%s", cutoff, corpus_summary(meta))
    return corpus_dir


def train_vintage_models(year, corpus_dir, cfg):
    """步骤②③：重训标记化器与基座模型；仅覆盖语料与输出目录，超参冻结."""
    out_dir = Path(cfg.out_root) / str(year)
    tok_ckpt = out_dir / "tokenizer" / "tokenizer_final.pt"
    if not tok_ckpt.exists():
        tcfg = load_config(cfg.base_configs.tokenizer, overrides={
            "data": {"corpus_dir": str(corpus_dir)},
            "train": {"out_dir": str(out_dir / "tokenizer")}})
        tok_ckpt = Path(train_tokenizer(tcfg))
        log.info("② 标记化器重训完成: %s", tok_ckpt)
    fm_ckpt = out_dir / "foundation" / "base_final.pt"
    if not fm_ckpt.exists():
        pcfg = load_config(cfg.base_configs.pretrain, overrides={
            "tokenizer_ckpt": str(tok_ckpt),
            "data": {"corpus_dir": str(corpus_dir)},
            "train": {"out_dir": str(out_dir / "foundation")}})
        fm_ckpt = Path(pretrain(pcfg))
        log.info("③ 基座模型重训完成: %s", fm_ckpt)
    return tok_ckpt, fm_ckpt


def fit_vintage_calibrator(engine, panel, all_dates, cutoff, cfg, fac_cfg, out_dir):
    """步骤④：截止日前窗口内重拟合波动率校准器，测试段冻结外推（同scripts/05）."""
    cut = pd.Timestamp(cutoff)
    c0 = cut - pd.DateOffset(months=cfg.get_path("calib_window_months", 18))
    cdates = [d for d in all_dates if c0 <= d <= cut]
    cdates = cdates[::cfg.get_path("calib_sample_every", 5)]
    horizon = fac_cfg.get_path("horizon", 10)
    quantile_panel = engine.quantile_panel(panel, cdates)
    rv_cache = {s: realized_volatility(df["close"], horizon) for s, df in panel.items()}
    widths, realized = [], []
    for (date, symbol), row in quantile_panel.iterrows():
        rv = rv_cache.get(symbol)
        if rv is not None and date in rv.index and pd.notna(rv.loc[date]):
            widths.append(row["q90"] - row["q10"])
            realized.append(float(rv.loc[date]))
    calib = VolatilityCalibrator().fit(pd.Series(widths), pd.Series(realized))
    calib.to_frame().to_parquet(out_dir / "vol_calibrator.parquet")
    log.info("④ 校准器已冻结（%s前%d个月，n=%d）", cutoff,
             cfg.get_path("calib_window_months", 18), len(widths))
    return calib


def run_vintage(year, cfg, fac_cfg, panel, all_dates):
    """单节点全链路：重建语料 -> 重训 -> 重拟合校准器 -> 测试年度因子."""
    cutoff, test_start, test_end = vintage_spec(year, cfg)
    out_dir = Path(cfg.out_root) / str(year)
    out_path = out_dir / "factor_panel.parquet"
    if out_path.exists():
        log.info("Vintage %d 已完成，跳过: %s", year, out_path)
        return out_path
    out_dir.mkdir(parents=True, exist_ok=True)
    log.info("========== Vintage %d | 语料截止 %s | 测试 %s ~ %s ==========",
             year, cutoff, test_start, test_end)

    corpus_dir = build_vintage_corpus(year, cutoff, cfg)
    tok_ckpt, fm_ckpt = train_vintage_models(year, corpus_dir, cfg)

    engine = FactorEngine(load_tokenizer(str(tok_ckpt)), load_foundation(str(fm_ckpt)),
                          lookback=fac_cfg.get_path("lookback", 90),
                          batch_size=fac_cfg.get_path("batch_size", 256))
    calib = fit_vintage_calibrator(engine, panel, all_dates, cutoff, cfg, fac_cfg, out_dir)

    # 步骤⑤：严格仅在截止日之后的测试年度内生成因子（时间隔离）
    tdates = [d for d in all_dates if pd.Timestamp(test_start) <= d <= pd.Timestamp(test_end)]
    factor_df = build_factor_frame(
        engine.quantile_panel(panel, tdates),
        eps=fac_cfg.get_path("eps", 1e-3),
        winsor=tuple(fac_cfg.get_path("winsor", (0.01, 0.99))))
    factor_df["vol_pred"] = calib.predict(factor_df["width"].to_numpy())
    factor_df.to_parquet(out_path)
    log.info("⑤ Vintage %d 完成：%d个交易日 -> %s", year, len(tdates), out_path)
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/walkforward.yaml")
    ap.add_argument("--vintages", default=None, help="如 2017:2025，或单节点 2024")
    args = ap.parse_args()
    cfg = load_config(args.config)
    first = cfg.get_path("vintages.first", 2017)
    last = cfg.get_path("vintages.last", 2025)
    if args.vintages:
        lo, _, hi = args.vintages.partition(":")
        first, last = int(lo), int(hi or lo)

    fac_cfg = load_config(cfg.base_configs.factor)
    # 成分股按历史时点复原(point-in-time)由Qlib回测端按动态成分处理（附录B表B.1）
    panel = load_panel(fac_cfg.panel_path)
    all_dates = sorted(set().union(*[set(df.index) for df in panel.values()]))
    log.info("行情面板载入完成：%d只标的 | 节点区间 Vintage %d—%d | 配置冻结、逐年零调参",
             len(panel), first, last)

    outputs = [run_vintage(y, cfg, fac_cfg, panel, all_dates) for y in range(first, last + 1)]

    # 各测试段互不重叠、首尾相接：拼接为连续历史因子面板，供Qlib逐年回测并拼接净值
    stitched = pd.concat([pd.read_parquet(p) for p in outputs]).sort_index()
    out = Path(cfg.out_root) / "factor_panel_walkforward.parquet"
    stitched.to_parquet(out)
    log.info("Walk-forward因子面板拼接完成: %s（%d个截面日）", out,
             stitched.index.get_level_values("date").nunique())


if __name__ == "__main__":
    main()
