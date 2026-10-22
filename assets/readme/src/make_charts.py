"""Render the README result charts (light theme, SVG).

Every number below is transcribed from a table of the FSQ strategy report
(table numbers in comments); nothing is simulated or interpolated.

    uv run --no-project --with matplotlib python assets/readme/src/make_charts.py
"""
from __future__ import annotations

import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyBboxPatch  # noqa: E402

OUT = Path(__file__).resolve().parents[1]

INK = "#1B2A41"
MUTED = "#6B7A90"
GRID = "#E6EAF0"
FSQ = "#3B5BDB"        # indigo — the FSQ model
FSQ_LIGHT = "#91A7FF"
TEAL = "#12B886"
AMBER = "#F59F00"
RED = "#E03131"
BASE = "#ADB5BD"       # baselines / benchmark

plt.rcParams.update({
    "font.family": "DejaVu Sans",
    "font.size": 10.5,
    "axes.edgecolor": GRID,
    "axes.labelcolor": INK,
    "axes.titleweight": "bold",
    "axes.titlesize": 12.5,
    "axes.titlecolor": INK,
    "axes.titlelocation": "left",
    "axes.titlepad": 14,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "axes.grid": True,
    "grid.color": GRID,
    "grid.linewidth": 0.8,
    "axes.axisbelow": True,
    "legend.frameon": False,
    "svg.fonttype": "path",
    "figure.facecolor": "white",
    "axes.facecolor": "white",
})


def _clean(ax, grid_axis="y"):
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.spines["bottom"].set_color("#C9D1DC")
    ax.grid(axis="x" if grid_axis == "y" else "y", visible=False)
    ax.tick_params(length=0)


def _save(fig, name):
    fig.savefig(OUT / name, format="svg", bbox_inches="tight", pad_inches=0.25)
    if os.environ.get("PREVIEW_DIR"):  # PNG copies for quick review
        fig.savefig(Path(os.environ["PREVIEW_DIR"]) / name.replace(".svg", ".png"),
                    dpi=110, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)


def _note(fig, text):
    fig.text(0.01, -0.02, text, color=MUTED, fontsize=8.5, ha="left", va="top")


# ── Table 7.6: same protocol, only the forecasting model differs ───────────
def baselines():
    models = ["FSQ base (41M)", "FSQ compact (16M)", "Moment-large", "Moirai-large",
              "PatchTST", "TimeXer", "TimesFM"]
    hs300 = [13.5, 12.1, 10.9, 9.6, 8.4, 7.0, 5.2]
    csi800 = [17.8, 15.9, 14.8, 14.2, 13.3, 12.1, 10.6]
    fig, axes = plt.subplots(1, 2, figsize=(10.4, 3.9), sharey=True)
    for ax, vals, title in ((axes[0], hs300, "CSI 300 pool"), (axes[1], csi800, "CSI 800 pool")):
        y = range(len(models))[::-1]
        colors = [FSQ, FSQ_LIGHT] + [BASE] * 5
        ax.barh(list(y), vals, color=colors, height=0.62)
        for yi, v in zip(y, vals):
            ax.text(v + 0.3, yi, f"{v:.1f}%", va="center", color=INK, fontsize=9.5,
                    fontweight="bold" if yi >= len(models) - 2 else "normal")
        ax.set_yticks(list(y), models)
        ax.set_xlim(0, max(vals) * 1.22)
        ax.set_title(title, fontsize=11, loc="left", pad=8)
        ax.xaxis.set_major_formatter(lambda v, _: f"{v:.0f}%")
        _clean(ax, "x")
        ax.grid(axis="x", visible=True)
        ax.grid(axis="y", visible=False)
    fig.suptitle("Annualised excess return vs. time-series foundation models",
                 x=0.01, ha="left", fontsize=12.5, fontweight="bold", color=INK, y=1.03)
    _note(fig, "Out of sample 2025-07-01 → 2026-06-18 · identical universe, top-k/drop-n rules, "
               "5-day rebalance and 0.15% one-way cost; only the signal source changes.")
    fig.tight_layout()
    _save(fig, "chart-baselines.svg")


# ── Table 7.8: yearly walk-forward (full-chain retraining per vintage) ─────
def walkforward():
    years = ["2017", "2018", "2019", "2020", "2021", "2022", "2023", "2024", "2025H1", "OOS*"]
    hs300 = [8.6, 10.3, 9.5, 11.0, 12.2, 11.4, 12.5, 10.6, 12.8, 13.5]
    csi800 = [10.4, 12.9, 13.2, 14.8, 16.1, 14.6, 16.3, 13.4, 16.9, 17.8]
    ir800 = [0.94, 1.08, 1.12, 1.21, 1.27, 1.19, 1.30, 1.10, 1.34, 1.42]
    x = range(len(years))
    w = 0.38
    fig, ax = plt.subplots(figsize=(10.4, 3.9))
    ax.axvspan(len(years) - 1.5, len(years) - 0.5, color="#EDF2FF", zorder=0)
    ax.bar([i - w / 2 for i in x], hs300, w, color=FSQ_LIGHT, label="CSI 300 · AER")
    ax.bar([i + w / 2 for i in x], csi800, w, color=FSQ, label="CSI 800 · AER")
    for i, v in zip(x, csi800):
        ax.text(i + w / 2, v - 0.5, f"{v:.1f}", ha="center", va="top", fontsize=8, color="white")
    ax.set_xticks(list(x), years)
    ax.set_ylim(0, 22)
    ax.set_yticks([0, 5, 10, 15, 20])
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0f}%")
    ax2 = ax.twinx()
    ax2.plot(list(x), ir800, color=AMBER, marker="o", lw=2, ms=5, label="CSI 800 · IR")
    ax2.set_ylim(0, 1.6)
    ax2.set_yticks([0, 0.5, 1.0, 1.5])
    for s in ax2.spines.values():
        s.set_visible(False)
    ax2.tick_params(length=0, colors=AMBER)
    ax2.grid(False)
    _clean(ax)
    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left", ncol=3, fontsize=9)
    ax.set_title("Positive excess return in every test year, 2017 → 2026")
    _note(fig, "Each vintage re-builds the corpus and retrains tokenizer, model and calibrator using only "
               "data before its cut-off; configuration frozen.\n*OOS = strict out-of-sample year "
               "(2025-07 → 2026-06). Walk-forward shares design-level hindsight, so it is "
               "supporting evidence; OOS is the headline.")
    _save(fig, "chart-walkforward.svg")


# ── Table 7.7: four market regimes inside the OOS year (CSI 800) ───────────
def regimes():
    names = ["Trend up\n2025-07 → 10", "High-level range\n2025-11 → 01",
             "Drawdown\n2026-02 → 03", "Recovery\n2026-04 → 06"]
    strat = [21.9, 9.1, -2.3, 11.5]
    bench = [18.9, 3.7, -5.2, 10.1]
    aer = [8.3, 21.0, 21.3, 6.4]
    x = range(len(names))
    w = 0.36
    fig, ax = plt.subplots(figsize=(10.4, 3.7))
    ax.bar([i - w / 2 for i in x], strat, w, color=FSQ, label="FSQ strategy")
    ax.bar([i + w / 2 for i in x], bench, w, color=BASE, label="CSI 800 index")
    for i, (s, b, a) in enumerate(zip(strat, bench, aer)):
        ax.text(i - w / 2, s + (0.6 if s >= 0 else -1.8), f"{s:+.1f}%", ha="center", fontsize=9, color=INK)
        ax.text(i + w / 2, b + (0.6 if b >= 0 else -1.8), f"{b:+.1f}%", ha="center", fontsize=9, color=MUTED)
        ax.text(i, 31.5, f"AER {a:.1f}%", ha="center", fontsize=9.5, color=TEAL, fontweight="bold")
    ax.axhline(0, color="#C9D1DC", lw=1)
    ax.set_xticks(list(x), names)
    ax.set_ylim(-8, 34)
    ax.set_yticks([-5, 0, 5, 10, 15, 20, 25])
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0f}%")
    _clean(ax)
    ax.legend(loc="upper right", fontsize=9, bbox_to_anchor=(1, 0.86))
    ax.set_title("Holds up when the market falls: period return by market regime")
    _note(fig, "CSI 800 pool, out of sample. AER = annualised excess return over the index within each regime.")
    _save(fig, "chart-regimes.svg")


# ── Fig. 7.10: excess-return attribution (CSI 800) ─────────────────────────
def attribution():
    parts = [("FSQ stock\nselection", 13.1), ("Volatility\nrisk budget", 2.6),
             ("Turnover &\ncost control", 1.2), ("Tiered\nhedging", 1.6), ("Trading\ncost", -0.7)]
    fig, ax = plt.subplots(figsize=(10.4, 3.6))
    cum = 0.0
    colors = [FSQ, TEAL, TEAL, TEAL, RED]
    for i, ((name, v), c) in enumerate(zip(parts, colors)):
        bottom = cum if v >= 0 else cum + v
        ax.bar(i, abs(v), bottom=bottom, color=c, width=0.58)
        ax.text(i, cum + max(v, 0) + 0.35, f"{v:+.1f} pp", ha="center", fontsize=9.5, color=INK)
        if i < len(parts) - 1:
            ax.plot([i + 0.29, i + 0.71], [cum + v, cum + v], color="#C9D1DC", lw=1, ls="--")
        cum += v
    ax.bar(len(parts), cum, color=INK, width=0.58)
    ax.text(len(parts), cum + 0.35, f"{cum:.1f}%", ha="center", fontsize=10, color=INK, fontweight="bold")
    ax.set_xticks(range(len(parts) + 1), [p[0] for p in parts] + ["Total\nexcess"])
    ax.set_ylim(0, 21)
    ax.set_yticks([0, 5, 10, 15, 20])
    ax.yaxis.set_major_formatter(lambda v, _: f"{v:.0f}")
    ax.set_ylabel("percentage points / yr", color=MUTED, fontsize=9)
    _clean(ax)
    ax.set_title("Where the 17.8% comes from — most of it is the model's stock picking")
    _note(fig, "CSI 800 pool, out of sample; decomposition of annualised excess return by system module.")
    _save(fig, "chart-attribution.svg")


# ── Table A.4: pre-training corpus governance funnel ───────────────────────
def corpus():
    steps = ["Raw ingest", "Missing-field policy", "Structural-break split",
             "Illiquid / stale filter", "Min-length filter (final)"]
    vals = [30.62, 30.36, 30.21, 28.67, 27.83]
    fig, ax = plt.subplots(figsize=(10.4, 2.9))
    y = range(len(steps))[::-1]
    for yi, v, s in zip(y, vals, steps):
        frac = v / vals[0]
        ax.barh(yi, v, color=FSQ if s.endswith("(final)") else FSQ_LIGHT, height=0.62)
        ax.text(v + 0.25, yi, f"{v:.2f} B  ·  {frac:.1%}", va="center", fontsize=9.5, color=INK)
    ax.set_yticks(list(y), steps)
    ax.set_xlim(0, 37)
    ax.xaxis.set_major_formatter(lambda v, _: f"{v:.0f} B")
    _clean(ax, "x")
    ax.grid(axis="x", visible=True)
    ax.grid(axis="y", visible=False)
    ax.set_title("3.06 billion raw K-lines → 2.78 billion clean training bars")
    _note(fig, "All Shanghai + Shenzhen A-shares incl. 5,872 delisted names · 7 frequencies (1-min → weekly) · "
               "Jan 2005 → Jun 2025 · 638k continuous training segments.")
    _save(fig, "chart-corpus.svg")


if __name__ == "__main__":
    for f in (baselines, walkforward, regimes, attribution, corpus):
        f()
    print("charts written to", OUT)
