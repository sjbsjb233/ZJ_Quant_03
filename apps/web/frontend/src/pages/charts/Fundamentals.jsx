// Financial time series workstation: PIT-constrained multi-symbol/metric ECharts
import React, { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { TerminalShell, TPanel, FSelect } from '../../components/shell';
import { EChart, SymbolSearch, Loading, Empty, ErrorState } from '../../components/ui';
import { endpoints } from '../../lib/api';

const RECENT_KEY = 'zjq_recent_charts';

function pushRecent(label) {
  try {
    const arr = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
    const next = [{ label, type: '财务' }, ...arr.filter((r) => r.label !== label)].slice(0, 20);
    localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {}
}

// Known business metrics
const KNOWN_METRICS = [
  { value: 'revenue',            label: '营业收入' },
  { value: 'net_income',         label: '净利润' },
  { value: 'total_assets',       label: '总资产' },
  { value: 'total_liabilities',  label: '总负债' },
  { value: 'total_equity',       label: '股东权益' },
  { value: 'eps_basic',          label: 'EPS (基本)' },
  { value: 'operating_cash_flow',label: '经营性现金流' },
  { value: 'roe',                label: 'ROE' },
  { value: 'gross_margin',       label: '毛利率' },
  { value: 'pe_ttm',             label: 'PE (TTM)' },
];

const PERIOD_OPTIONS = [
  { value: 'annual',    label: '年报 (A)' },
  { value: 'quarterly', label: '季报 (Q)' },
  { value: 'ttm',       label: 'TTM' },
];

// Symbol colors (same symbol gets same color; metric changes line style)
const SYMBOL_COLORS = [
  'oklch(0.58 0.18 27)',
  'oklch(0.55 0.13 220)',
  'oklch(0.6 0.13 120)',
  'oklch(0.6 0.13 300)',
  'oklch(0.6 0.13 60)',
];

const METRIC_STYLES = ['solid', 'dashed', 'dotted'];
const METRIC_DASH  = ['', '8,4', '2,4'];

function today() {
  return new Date().toISOString().slice(0, 10);
}

function startOfYear(yearsAgo) {
  const d = new Date();
  d.setFullYear(d.getFullYear() - yearsAgo, 0, 1);
  return d.toISOString().slice(0, 10);
}

const RANGE_OPTIONS = [
  { label: '1Y',  start: () => startOfYear(1) },
  { label: '3Y',  start: () => startOfYear(3) },
  { label: '5Y',  start: () => startOfYear(5) },
  { label: '10Y', start: () => startOfYear(10) },
];

export default function FundamentalsPage() {
  const [symbols, setSymbols] = useState(['600519.SH', '000858.SZ']);
  const [metrics, setMetrics] = useState(['roe', 'gross_margin']);
  const [periodType, setPeriodType] = useState('quarterly');
  const [asOf, setAsOf] = useState(today());
  const [rangeIdx, setRangeIdx] = useState(1); // default 3Y
  const [derivYoY, setDerivYoY] = useState(false);
  const [derivQoQ, setDerivQoQ] = useState(false);
  const [addMetric, setAddMetric] = useState('');

  const start = RANGE_OPTIONS[rangeIdx].start();

  const derivatives = [derivYoY && 'yoy', derivQoQ && 'qoq'].filter(Boolean).join(',') || undefined;

  const fundamentalsQ = useQuery({
    queryKey: ['fundamentals', symbols, metrics, periodType, asOf, start, derivatives],
    queryFn: () =>
      endpoints.fundamentals({
        symbols: symbols.join(','),
        metrics: metrics.join(','),
        as_of_date: asOf,
        period_type: periodType,
        start,
        ...(derivatives ? { derivatives } : {}),
      }),
    enabled: symbols.length > 0 && metrics.length > 0 && !!asOf,
  });

  // Push recent on successful query
  React.useEffect(() => {
    if (fundamentalsQ.data && symbols.length > 0 && metrics.length > 0) {
      pushRecent(`${metrics.slice(0, 2).join('+')} · ${symbols.slice(0, 2).join(' vs ')}`);
    }
  }, [fundamentalsQ.data]); // eslint-disable-line react-hooks/exhaustive-deps

  // Build EChart series from API response
  const chartOption = useMemo(() => {
    const series = fundamentalsQ.data?.series || [];
    if (!series.length) return null;

    // Collect all periods
    const allPeriods = [...new Set(series.flatMap((s) => s.points.map((p) => p.period)))].sort();

    // Assign color by symbol, dash by metric index
    const symbolIdx = {};
    const metricIdx = {};
    let si = 0, mi = 0;
    const echartsSeries = series.map((s) => {
      if (!(s.symbol in symbolIdx)) symbolIdx[s.symbol] = si++;
      if (!(s.metric in metricIdx)) metricIdx[s.metric] = mi++;
      const color = SYMBOL_COLORS[symbolIdx[s.symbol] % SYMBOL_COLORS.length];
      const dashIdx = metricIdx[s.metric] % METRIC_DASH.length;

      const data = allPeriods.map((p) => {
        const pt = s.points.find((x) => x.period === p);
        return pt ? pt.value : null;
      });

      return {
        name: `${s.symbol} · ${s.metric}`,
        type: 'line',
        data,
        itemStyle: { color },
        lineStyle: {
          color,
          type: dashIdx === 0 ? 'solid' : dashIdx === 1 ? 'dashed' : 'dotted',
          width: 1.8,
        },
        symbol: 'circle',
        symbolSize: 4,
        connectNulls: true,
      };
    });

    return {
      xAxis: { type: 'category', data: allPeriods, boundaryGap: false },
      yAxis: [
        { type: 'value', scale: true },
      ],
      series: echartsSeries,
      legend: { show: true, type: 'scroll', bottom: 0, itemWidth: 20, itemHeight: 8 },
      tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
      grid: { left: 52, right: 20, top: 24, bottom: 60 },
    };
  }, [fundamentalsQ.data]);

  // Build data matrix from series
  const matrix = useMemo(() => {
    const series = fundamentalsQ.data?.series || [];
    if (!series.length) return { periods: [], rows: [] };
    const allPeriods = [...new Set(series.flatMap((s) => s.points.map((p) => p.period)))].sort();
    const rows = series.map((s) => ({
      key: `${s.symbol} · ${s.metric}`,
      symbol: s.symbol,
      metric: s.metric,
      values: allPeriods.map((p) => {
        const pt = s.points.find((x) => x.period === p);
        return pt ? pt.value : null;
      }),
    }));
    return { periods: allPeriods, rows };
  }, [fundamentalsQ.data]);

  function removeSymbol(sym) {
    setSymbols((prev) => prev.filter((s) => s !== sym));
  }

  function removeMetric(m) {
    setMetrics((prev) => prev.filter((x) => x !== m));
  }

  function handleAddMetric() {
    if (addMetric && !metrics.includes(addMetric)) {
      setMetrics((prev) => [...prev, addMetric]);
    }
    setAddMetric('');
  }

  function handleAddSymbol(sym) {
    if (sym && !symbols.includes(sym) && symbols.length < 5) {
      setSymbols((prev) => [...prev, sym]);
    }
  }

  return (
    <TerminalShell
      module="viz"
      current="fundamentals"
      path={['viz/', 'fundamentals']}
      fkeys={[
        { k: 'F1', l: 'PIT 日期' },
        { k: 'F2', l: '行业均值' },
        { k: 'F3', l: '导出' },
      ]}
      title="财务时间序列工作台"
      subtitle="fundamentals · multi-symbol time series · as_of_date enforced"
    >
      {/* Control bar */}
      <div style={{
        padding: '8px 14px',
        background: 'var(--surface)', borderBottom: '1px solid var(--border)',
        display: 'flex', alignItems: 'center', gap: 0, flexWrap: 'wrap', rowGap: 6,
        fontFamily: 'var(--font-mono)', fontSize: 11, flexShrink: 0,
      }}>
        {/* Symbols */}
        <span style={{ color: 'var(--fg-3)', fontSize: 10, marginRight: 6 }}>symbols:</span>
        {symbols.map((s) => (
          <span key={s} style={{
            padding: '2px 8px', background: 'var(--surface-2)',
            border: '1px solid var(--border)', borderRadius: 3, marginRight: 4,
            display: 'inline-flex', alignItems: 'center', gap: 6,
          }}>
            <span style={{ color: 'var(--fg)' }}>{s}</span>
            <span style={{ color: 'var(--fg-3)', cursor: 'pointer' }} onClick={() => removeSymbol(s)}>✕</span>
          </span>
        ))}
        {symbols.length < 5 && (
          <div style={{ marginRight: 8 }}>
            <SymbolSearch value="" onChange={handleAddSymbol} width={160} />
          </div>
        )}

        <span style={{ color: 'var(--fg-mute)', margin: '0 10px' }}>│</span>

        {/* Metrics */}
        <span style={{ color: 'var(--fg-3)', fontSize: 10, marginRight: 6 }}>metrics:</span>
        {metrics.map((m) => {
          const opt = KNOWN_METRICS.find((x) => x.value === m);
          return (
            <span key={m} style={{
              padding: '2px 8px', background: 'var(--info-bg)', color: 'var(--info)',
              border: '1px solid var(--info)', borderRadius: 3, marginRight: 4,
              display: 'inline-flex', alignItems: 'center', gap: 6,
            }}>
              {opt ? opt.label : m}
              <span style={{ cursor: 'pointer' }} onClick={() => removeMetric(m)}>✕</span>
            </span>
          );
        })}
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, marginRight: 4 }}>
          <select
            value={addMetric}
            onChange={(e) => setAddMetric(e.target.value)}
            style={{
              height: 22, padding: '0 4px', background: 'var(--surface-2)',
              border: '1px solid var(--border)', borderRadius: 3, fontSize: 10.5,
              fontFamily: 'var(--font-mono)', color: 'var(--fg)', cursor: 'pointer',
            }}
          >
            <option value="">+ 指标</option>
            {KNOWN_METRICS.filter((m) => !metrics.includes(m.value)).map((m) => (
              <option key={m.value} value={m.value}>{m.label}</option>
            ))}
          </select>
          {addMetric && (
            <button className="btn ghost sm" style={{ height: 22 }} onClick={handleAddMetric}>添加</button>
          )}
        </span>

        <span style={{ color: 'var(--fg-mute)', margin: '0 10px' }}>│</span>

        {/* Period type */}
        <div style={{ display: 'inline-flex', border: '1px solid var(--border-strong)', borderRadius: 3, overflow: 'hidden', marginRight: 8 }}>
          {PERIOD_OPTIONS.map((p, i) => (
            <button
              key={p.value}
              onClick={() => setPeriodType(p.value)}
              style={{
                height: 22, padding: '0 8px',
                background: periodType === p.value ? 'var(--fg)' : 'transparent',
                color: periodType === p.value ? 'var(--bg)' : 'var(--fg-2)',
                border: 'none',
                borderRight: i < PERIOD_OPTIONS.length - 1 ? '1px solid var(--border-strong)' : 'none',
                fontFamily: 'var(--font-mono)', fontSize: 10, cursor: 'pointer',
              }}
            >{p.label}</button>
          ))}
        </div>

        {/* Range */}
        <div style={{ display: 'inline-flex', border: '1px solid var(--border-strong)', borderRadius: 3, overflow: 'hidden', marginRight: 8 }}>
          {RANGE_OPTIONS.map((r, i) => (
            <button
              key={r.label}
              onClick={() => setRangeIdx(i)}
              style={{
                height: 22, padding: '0 8px',
                background: rangeIdx === i ? 'var(--fg)' : 'transparent',
                color: rangeIdx === i ? 'var(--bg)' : 'var(--fg-2)',
                border: 'none',
                borderRight: i < RANGE_OPTIONS.length - 1 ? '1px solid var(--border-strong)' : 'none',
                fontFamily: 'var(--font-mono)', fontSize: 10, cursor: 'pointer',
              }}
            >{r.label}</button>
          ))}
        </div>

        {/* YoY/QoQ toggles */}
        <span style={{ display: 'inline-flex', gap: 4, marginRight: 8 }}>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4, cursor: 'pointer', fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-2)' }}>
            <input type="checkbox" checked={derivYoY} onChange={(e) => setDerivYoY(e.target.checked)} />
            YoY
          </label>
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 4, cursor: 'pointer', fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-2)' }}>
            <input type="checkbox" checked={derivQoQ} onChange={(e) => setDerivQoQ(e.target.checked)} />
            QoQ
          </label>
        </span>

        <div style={{ flex: 1 }} />

        {/* AS_OF control - prominent */}
        <div style={{
          height: 26, padding: '0 10px',
          background: 'var(--warn-bg)', border: '1px solid var(--warn)', borderRadius: 3,
          display: 'inline-flex', alignItems: 'center', gap: 8,
          color: 'var(--warn)',
        }}>
          <span style={{ fontSize: 10 }}>⏱ AS_OF</span>
          <input
            type="date"
            value={asOf}
            onChange={(e) => setAsOf(e.target.value)}
            style={{
              background: 'transparent', border: 'none', color: 'var(--fg)',
              fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 500, cursor: 'pointer',
            }}
          />
        </div>
      </div>

      {/* AS_OF Banner */}
      <div style={{
        padding: '5px 14px', background: 'var(--warn-bg)', borderBottom: '1px solid var(--border)',
        fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--warn)', flexShrink: 0,
      }}>
        ⓘ AS_OF · 财务数据基于披露日修订；所有取数为截至 {asOf} 真实可见的数据视角（避免未来函数）
      </div>

      {/* Main: chart + table */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        {/* Chart */}
        <div style={{ flex: 1.4, minHeight: 0, padding: '14px 14px 14px 14px', background: 'var(--surface)', position: 'relative' }}>
          {fundamentalsQ.isLoading ? (
            <Loading />
          ) : fundamentalsQ.error ? (
            <ErrorState error={fundamentalsQ.error} onRetry={fundamentalsQ.refetch} />
          ) : !chartOption ? (
            <Empty label="请选择 symbol 和指标，设置 AS_OF 日期后查询" />
          ) : (
            <EChart option={chartOption} height="100%" />
          )}
        </div>

        {/* Data matrix table */}
        <div style={{ flex: 1, minHeight: 0, borderTop: '1px solid var(--border-strong)', overflow: 'auto' }}>
          <TPanel title="DATA MATRIX" meta={`${matrix.rows.length} series · ${matrix.periods.length} periods`} last noBorderBottom>
            {matrix.rows.length === 0 ? (
              <div style={{ padding: 20, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-3)', textAlign: 'center' }}>
                —
              </div>
            ) : (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
                <thead style={{ position: 'sticky', top: 0, background: 'var(--surface)' }}>
                  <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
                    <th style={{
                      textAlign: 'left', padding: '6px 10px', fontSize: 9.5, color: 'var(--fg-3)',
                      fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.05em',
                      minWidth: 160, position: 'sticky', left: 0, background: 'var(--surface)',
                    }}>symbol · metric</th>
                    {matrix.periods.slice(-16).map((p) => (
                      <th key={p} style={{
                        textAlign: 'right', padding: '6px 10px', fontSize: 9.5,
                        color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', whiteSpace: 'nowrap',
                      }}>{p}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {matrix.rows.map((r, ri) => {
                    const siIdx = [...new Set(matrix.rows.map((x) => x.symbol))].indexOf(r.symbol);
                    const dotColor = SYMBOL_COLORS[siIdx % SYMBOL_COLORS.length];
                    const vals = r.values.slice(-16);
                    const maxVal = Math.max(...vals.filter((v) => v !== null));
                    return (
                      <tr key={r.key} style={{ borderBottom: '1px solid var(--border-faint)' }}>
                        <td style={{
                          padding: '5px 10px', textAlign: 'left', color: 'var(--fg)',
                          position: 'sticky', left: 0, background: 'var(--surface)',
                          display: 'flex', alignItems: 'center', gap: 6,
                        }}>
                          <span style={{ width: 8, height: 8, borderRadius: '50%', background: dotColor, flexShrink: 0 }} />
                          {r.key}
                        </td>
                        {vals.map((v, vi) => (
                          <td key={vi} style={{
                            padding: '5px 10px', textAlign: 'right',
                            color: v !== null ? 'var(--fg-2)' : 'var(--fg-4)',
                            fontVariantNumeric: 'tabular-nums',
                            background: v !== null && maxVal > 0
                              ? `oklch(0.96 0.03 152 / ${Math.min(0.5, v / maxVal * 0.5)})`
                              : 'transparent',
                          }}>
                            {v !== null ? v.toFixed(2) : '—'}
                          </td>
                        ))}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </TPanel>
        </div>
      </div>
    </TerminalShell>
  );
}
