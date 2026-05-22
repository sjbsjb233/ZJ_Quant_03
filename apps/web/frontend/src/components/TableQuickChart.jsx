// Quick chart view for a single table (整改方案 v1.0 §6). Per-table dispatch:
// bars → embedded K-line, fundamentals → metric line, everything else → a
// distribution chart derived from the rows already loaded by TableBrowser.
import React, { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { EChart, Kline, Loading, Empty } from './ui';
import { endpoints } from '../lib/api';

const INTERNAL = new Set(['_batch_id', '_source', '_ingested_at', '_publish_date', '_status', '_invalidated_at', '_superseded_by', '_extras']);

function WorkstationLink({ to, label = '↗ 完整工作台', nav }) {
  return (
    <button className="btn ghost sm" style={{ position: 'absolute', top: 10, right: 14, zIndex: 5, fontSize: 10.5 }}
      onClick={() => nav(to)}>{label}</button>
  );
}

function EmbeddedKline({ symbol, frequency, nav }) {
  const q = useQuery({
    queryKey: ['quickKline', symbol, frequency],
    queryFn: () => endpoints.kline({ symbol, frequency, adjust: 'pre', count: 250 }),
    enabled: !!symbol,
  });
  if (q.isLoading) return <Loading />;
  const candles = q.data?.candles || [];
  if (!candles.length) return <Empty label={`${symbol} 暂无 K 线`} />;
  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <WorkstationLink nav={nav} to={`/data/charts/kline?symbol=${encodeURIComponent(symbol)}`} />
      <Kline candles={candles} mainIndicators={['MA']} subIndicators={['VOL']} />
    </div>
  );
}

function EmbeddedFundamentals({ symbol, asOf, nav }) {
  const q = useQuery({
    queryKey: ['quickFund', symbol, asOf],
    queryFn: () => endpoints.fundamentals({
      symbols: symbol, metrics: 'revenue,net_income', as_of_date: asOf || new Date().toISOString().slice(0, 10),
      period_type: 'annual',
    }),
    enabled: !!symbol,
  });
  const option = useMemo(() => {
    const series = q.data?.series || [];
    if (!series.length) return null;
    const periods = [...new Set(series.flatMap((s) => s.points.map((p) => p.period)))].sort();
    return {
      xAxis: { type: 'category', data: periods, boundaryGap: false },
      yAxis: { type: 'value', scale: true },
      legend: { bottom: 0, type: 'scroll', itemWidth: 18, itemHeight: 8 },
      tooltip: { trigger: 'axis' },
      grid: { left: 56, right: 20, top: 20, bottom: 44 },
      series: series.map((s) => ({
        name: `${s.symbol}·${s.metric}`, type: 'line', smooth: true, connectNulls: true,
        data: periods.map((p) => { const pt = s.points.find((x) => x.period === p); return pt ? pt.value : null; }),
      })),
    };
  }, [q.data]);
  if (q.isLoading) return <Loading />;
  if (!option) return <Empty label={`${symbol || '该 symbol'} 暂无财务数据`} />;
  return (
    <div style={{ position: 'relative', width: '100%', height: '100%' }}>
      <WorkstationLink nav={nav} to={`/data/charts/fundamentals`} />
      <EChart option={option} height="100%" />
    </div>
  );
}

// Count rows by the value of `col`, return a pie option.
function distributionOption(rows, col, kind = 'pie') {
  const counts = {};
  rows.forEach((r) => {
    const v = r[col];
    const key = v == null || v === '' ? '(空)' : String(v);
    counts[key] = (counts[key] || 0) + 1;
  });
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 30);
  if (!entries.length) return null;
  if (kind === 'bar') {
    return {
      xAxis: { type: 'category', data: entries.map((e) => e[0]), axisLabel: { interval: 0, rotate: entries.length > 8 ? 35 : 0 } },
      yAxis: { type: 'value' },
      tooltip: { trigger: 'axis' },
      grid: { left: 48, right: 20, top: 20, bottom: 70 },
      series: [{ type: 'bar', data: entries.map((e) => e[1]) }],
    };
  }
  return {
    tooltip: { trigger: 'item' },
    legend: { type: 'scroll', orient: 'vertical', left: 'left', textStyle: { fontSize: 10 } },
    series: [{ type: 'pie', radius: ['35%', '68%'], data: entries.map(([name, value]) => ({ name, value })) }],
  };
}

// pick a sensible categorical/business column to group by
function pickGroupColumn(rows, preferred = []) {
  if (!rows.length) return null;
  const keys = Object.keys(rows[0]).filter((k) => !INTERNAL.has(k));
  for (const p of preferred) if (keys.includes(p)) return p;
  // first non-numeric, non-id column
  for (const k of keys) {
    if (/symbol|id|code|date|name$/i.test(k)) continue;
    const v = rows.find((r) => r[k] != null)?.[k];
    if (typeof v === 'string') return k;
  }
  return keys.find((k) => !/symbol|^id$/i.test(k)) || keys[0] || null;
}

export default function TableQuickChart({ table, rows = [], filters = {}, nav }) {
  const firstSym = (filters.symbols || '').split(',').map((s) => s.trim()).filter(Boolean)[0]
    || rows[0]?.symbol || '';

  switch (table) {
    case 'raw.bars_daily':
      return firstSym
        ? <EmbeddedKline symbol={firstSym} frequency="1d" nav={nav} />
        : <Empty label="请在筛选中指定 symbol" hint="K 线视图需要单个 symbol" />;
    case 'raw.bars_minute':
      return firstSym
        ? <EmbeddedKline symbol={firstSym} frequency={filters.frequency || '1m'} nav={nav} />
        : <Empty label="请在筛选中指定 symbol" hint="K 线视图需要单个 symbol" />;
    case 'raw.fundamentals':
      return firstSym
        ? <EmbeddedFundamentals symbol={firstSym} asOf={filters.as_of_date} nav={nav} />
        : <Empty label="请在筛选中指定 symbol" hint="财务视图需要单个 symbol" />;
    default: {
      if (!rows.length) return <Empty label="暂无数据" hint="先调整筛选以加载行数据" />;
      const preferredByTable = {
        'raw.universe': ['sector', 'industry', 'sector_name', 'index_code'],
        'raw.calendar': ['exchange', 'is_open', 'market'],
        'raw.corporate_actions': ['action_type', 'type'],
        'reference.symbols': ['exchange', 'market', 'board', 'list_status'],
        'reference.sectors': ['parent', 'level', 'parent_code'],
      };
      const col = pickGroupColumn(rows, preferredByTable[table] || []);
      if (!col) return <Empty label="该表暂无可用快捷图" />;
      const kind = table === 'raw.corporate_actions' || table === 'raw.calendar' ? 'bar' : 'pie';
      const option = distributionOption(rows, col, kind);
      if (!option) return <Empty label="该表暂无可用快捷图" />;
      return (
        <div style={{ position: 'relative', width: '100%', height: '100%', padding: 14 }}>
          <span style={{ position: 'absolute', top: 12, left: 16, fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', zIndex: 5 }}>
            分布 · group by <b style={{ color: 'var(--fg-2)' }}>{col}</b> · {rows.length} 行
          </span>
          <EChart option={option} height="100%" />
        </div>
      );
    }
  }
}
