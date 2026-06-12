"""chunked_panel — 时间切块 + 双向重叠的分批读取原语 (《内存治理总体方案》WP4).

服务对象:``raw.bars_minute``(行数 ×240,裁列后单读仍 ~40GB 量级)与未来数据
增长。列裁剪后的日线场景不需要分批(单读 ~170MB)。**触发条件是静态配置**
(``CHUNK_TABLES`` 环境变量,默认 raw.bars_minute),不是运行时负载探测 —— 双
路径 = 双倍测试面 + 不可复现的执行历史(方案 §2.1)。

正确性依据(方案 §6.2/§6.3):
  * current_view 去重 ``PARTITION BY (date|datetime, symbol)`` —— 分区键含时间
    列,按日期切块不改变任何行的去重对手 ⇒ 每块独立去重赢家与整读一致。
  * 复权口径:块内统一 ``px = close × adj_factor``(乘法复权,无基准依赖)。
    **禁止**在分批路径使用 reader 的 ``adjust="post"`` —— 其 ``first_value()``
    归一化基准随结果集首行漂移,不分块稳定(与 /rows 内存分页注释记录的是
    同一陷阱)。本原语始终以 ``adjust="none"`` 读取,由调用方派生 px。
  * 重叠宽度 = max(lookback, lookahead),按 **交易日**(读 calendar)而非自然日
    推移;调用方对扩展面板计算后,必须 filter 回属主窗口再聚合 ⇒ 块间峰值互不
    叠加,且块边界两侧的因子值与整读逐字节一致。

块大小由预算反推(v1.2):``chunk_days = f(budget_mb, n_symbols, n_columns)``。
理论下限 = 单调仓日截面 + 支撑窗口(几十 MB 级)⇒ 任意数据规模都能在固定内存
内完成 —— 任务峰值内存是算法的选择,不是数据规模的函数。

远期扩展(登记,不实现;方案 WP4 v1.2):块边界协作式让位 —— 巨型任务在块间
检查是否有更高优先级等待者,有则落地已完成块、释放面板、以"从块 k 续跑"重新
入队。实现触发条件:系统出现小时级任务;当前 WP5 排干模式已覆盖。

新调用点登记(reader.py 调用守则 §4 三件套):本模块即登记示例 ——
① reader.py 重读调用点清单已列;② 准入预算类 ``factor.eval.minute``
(ADMISSION_BUDGETS_JSON);③ 内存断言测试 T-WP4-003。
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Iterator

import polars as pl

from . import config
from .logging import get_logger

_log = get_logger("quant_data.chunking")

__all__ = ["Window", "chunked_panel", "chunk_days_for_budget", "must_chunk"]

# 行字节系数(按实测标定;方案 WP4):Arrow 物化 + polars 派生瞬时拷贝的
# 每单元格折算字节,safety 把 join/group_by 的 2–3× 瞬时拷贝计入预算口径。
_BYTES_PER_CELL = 16.0
_BUDGET_SAFETY = 3.0

# chunk 字符串单位 → 交易日数("1Y"=244 交易日,"1M"=21,"5D"=5)
_CHUNK_RE = re.compile(r"^\s*(\d+)\s*([YMD])\s*$", re.IGNORECASE)
_UNIT_TRADING_DAYS = {"Y": 244, "M": 21, "D": 1}

# 分钟线一交易日 240 根 1m bar —— 预算反推时的行数放大系数。
_ROWS_PER_DAY = {"raw.bars_minute": 240.0}


@dataclass(frozen=True)
class Window:
    """属主窗口(调用方聚合归属)+ 扩展窗口(实际读取,含双向重叠)。"""

    owner_start: dt.date
    owner_end: dt.date
    ext_start: dt.date
    ext_end: dt.date


def must_chunk(table: str) -> bool:
    """该表是否被静态配置强制走分批原语 (CHUNK_TABLES, 方案 §5)。"""
    return table in config.chunk_tables()


def chunk_days_for_budget(
    budget_mb: float,
    n_symbols: int,
    n_columns: int,
    *,
    table: str = "raw.bars_daily",
    bytes_per_cell: float = _BYTES_PER_CELL,
    safety: float = _BUDGET_SAFETY,
) -> int:
    """块大小由预算反推 (方案 WP4 v1.2)。

    budget ≥ chunk_days × rows_per_day(table) × n_symbols × n_columns ×
    bytes_per_cell × safety。同一原语适配任意预算;下限 1 个交易日。
    """
    rows_per_day = _ROWS_PER_DAY.get(table, 1.0)
    per_day = max(1.0, rows_per_day * max(1, n_symbols) * max(1, n_columns)
                  * bytes_per_cell * safety)
    return max(1, int((float(budget_mb) * 1024 * 1024) // per_day))


def _chunk_to_trading_days(chunk: str | int) -> int:
    if isinstance(chunk, int):
        return max(1, chunk)
    m = _CHUNK_RE.match(str(chunk))
    if not m:
        raise ValueError(f"chunk 必须是交易日数或 '<n>Y/M/D' 形式,got {chunk!r}")
    return max(1, int(m.group(1)) * _UNIT_TRADING_DAYS[m.group(2).upper()])


def _trading_days(reader, start: dt.date, end: dt.date, market: str | None,
                  pad_days: int) -> list[dt.date]:
    """交易日轴(含两侧 pad,供重叠外推)。calendar 缺失时退化为自然日轴并告警
    (重叠按自然日计数 ≥ 交易日计数,只多读不少读 —— 保守安全)。"""
    lo = start - dt.timedelta(days=pad_days)
    hi = end + dt.timedelta(days=pad_days)
    cal = pl.from_arrow(reader.read_calendar(market=market, start=lo, end=hi))
    if cal.height and "is_trading_day" in cal.columns:
        days = (
            cal.filter(pl.col("is_trading_day") == True)  # noqa: E712
            .select("date").unique().sort("date")["date"].to_list()
        )
        if days:
            return [d if isinstance(d, dt.date) else d.date() for d in days]
    _log.warn("chunking.calendar.missing", start=str(start), end=str(end),
              fallback="natural-days")
    return [lo + dt.timedelta(days=i) for i in range((hi - lo).days + 1)]


def _read_chunk(reader, table: str, symbols, w: Window, columns: list[str]) -> pl.DataFrame:
    """One projected, adjust-free chunk read via the governed reader."""
    if table == "raw.bars_daily":
        tbl = reader.read_bars_daily(
            symbols=symbols, start=w.ext_start, end=w.ext_end,
            adjust="none", columns=columns,
        )
    elif table == "raw.bars_minute":
        tbl = reader.read_bars_minute(
            symbols=symbols,
            start=dt.datetime.combine(w.ext_start, dt.time.min),
            end=dt.datetime.combine(w.ext_end, dt.time.max),
            adjust="none", columns=columns,
        )
    else:
        # generic current-view table with a `date` column (rare; bars 为主)
        filters = [f"t.date >= DATE '{w.ext_start.isoformat()}'",
                   f"t.date <= DATE '{w.ext_end.isoformat()}'"]
        if symbols is not None:
            quoted = ",".join("'" + s.replace("'", "''") + "'" for s in symbols)
            filters.append(f"t.symbol IN ({quoted})")
        tbl = reader.current_view(table, filters=filters, projection=columns)
    return pl.from_arrow(tbl)


def chunked_panel(
    reader,
    *,
    table: str,
    symbols,
    start: dt.date,
    end: dt.date,
    columns: list[str],
    lookback_days: int = 0,
    lookahead_days: int = 0,
    chunk: str | int = "1Y",
    budget_mb: float | None = None,
    market: str | None = None,
) -> Iterator[tuple[Window, pl.DataFrame]]:
    """逐块产出 (属主窗口, 扩展面板)。

    调用方对扩展面板计算(rolling/shift 等窗口算子在重叠区内有完整支撑)后,
    **必须 filter 回属主窗口再聚合**;块间峰值互不叠加 (T-WP4-003)。

    - ``columns`` 必填(守则 §2:大表必须投影);需要价格时带上
      ``close``+``adj_factor``,块内自行派生 ``px = close × adj_factor``。
    - ``lookback_days``/``lookahead_days``:算子支撑窗口,按 **交易日** 计。
    - ``chunk``:属主块宽("1Y"/"3M"/"90D" 或交易日数);传 ``budget_mb``
      时按预算反推覆盖之(v1.2)。
    - 本原语永远 ``adjust="none"``;禁止对产出面板再做 post 复权(见模块
      docstring 的陷阱档案)。
    """
    if not columns:
        raise ValueError("chunked_panel 要求显式 columns(调用守则 §2:大表必须投影)")
    if start > end:
        raise ValueError(f"start {start} is after end {end}")
    lookback_days = max(0, int(lookback_days))
    lookahead_days = max(0, int(lookahead_days))
    overlap = max(lookback_days, lookahead_days)

    if budget_mb is not None:
        n_sym = len(symbols) if symbols is not None else 6000  # 全市场保守口径
        chunk_days = chunk_days_for_budget(
            budget_mb, n_sym, len(columns), table=table)
    else:
        chunk_days = _chunk_to_trading_days(chunk)

    # 交易日轴:pad 按自然日折算交易日 (×7/5) 再加安全余量,保证重叠外推不出轴。
    pad = int(overlap * 7 / 5) + 15
    axis = _trading_days(reader, start, end, market, pad)
    in_window = [d for d in axis if start <= d <= end]
    if not in_window:
        return
    idx_of = {d: i for i, d in enumerate(axis)}

    n_chunks = (len(in_window) + chunk_days - 1) // chunk_days
    _log.info("chunking.plan", table=table, chunks=n_chunks,
              chunk_days=chunk_days, overlap_days=overlap,
              window_days=len(in_window),
              budget_mb=budget_mb, columns=len(columns))

    for k in range(n_chunks):
        owner = in_window[k * chunk_days:(k + 1) * chunk_days]
        o_start, o_end = owner[0], owner[-1]
        # 双向重叠:沿交易日轴外推 overlap 天 (轴端夹紧 —— 数据之外本就无行)。
        i0, i1 = idx_of[o_start], idx_of[o_end]
        ext_start = axis[max(0, i0 - overlap)]
        ext_end = axis[min(len(axis) - 1, i1 + overlap)]
        w = Window(o_start, o_end, ext_start, ext_end)
        df = _read_chunk(reader, table, symbols, w, columns)
        _log.debug("chunking.chunk.read", table=table, k=k,
                   owner=f"{o_start}..{o_end}", ext=f"{ext_start}..{ext_end}",
                   rows=df.height)
        yield w, df
