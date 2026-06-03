// 市场全景 / Market Overview (VIZ Layer v2 §4) — full-market cross-section + breadth.
// Six live ECharts panels over raw.universe + bars_daily 市值列 + reference.symbols.
// 时序 panels (①②③⑥) use a range ending at as_of; 当日快照 panels (④⑤) take the
// as_of current view so no future value leaks into a historical day (§8.1 PIT).
import React, { useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { TerminalShell, FSelect } from '../../components/shell';
import { EChart } from '../../components/ui';
import { endpoints } from '../../lib/api';
import { downloadDataURL } from '../../lib/download';
import {
  CoverageBadge, SnapshotTag, TimeseriesTag, PanelState, MiniToggle, RangeButtons,
  VizPanel, VIZ_UP, VIZ_DOWN, VIZ_SERIES, fmtCap, fmtNum, panelStateOf,
} from './vizShared';

const RECENT_KEY = 'zjq_recent_charts';
function pushRecent(label) {
  try {
    const arr = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
    const next = [{ label, type: '全景' }, ...arr.filter((r) => r.label !== label)].slice(0, 20);
    localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch { /* ignore */ }
}

const POOL_OPTIONS = [
  { value: 'all', label: '全市场' },
  { value: 'hs300', label: '沪深300' },
  { value: 'zz500', label: '中证500' },
  { value: 'zz1000', label: '中证1000' },
  { value: 'sse50', label: '上证50' },
];
const RANGES = ['1M', '3M', '6M', '1Y', '3Y', 'ALL'];
const RANGE_MONTHS = { '1M': 1, '3M': 3, '6M': 6, '1Y': 12, '3Y': 36, ALL: null };

function today() { return new Date().toISOString().slice(0, 10); }
function computeStart(asOf, label) {
  const months = RANGE_MONTHS[label];
  if (!months) return undefined;
  const d = new Date(`${asOf}T00:00:00`);
  d.setMonth(d.getMonth() - months);
  return d.toISOString().slice(0, 10);
}

const AXIS_DATE = { type: 'category', boundaryGap: true, axisLabel: { hideOverlap: true } };

export default function MarketPage() {
  const [asOf, setAsOf] = useState(today());
  const [pool, setPool] = useState('all');
  const [range, setRange] = useState('1Y');
  const [mvField, setMvField] = useState('total_mv');
  const asOfRef = useRef(null);
  const refs = useRef({});

  const start = computeStart(asOf, range);
  const tsParams = { ...(start ? { start } : {}), end: asOf, ...(pool !== 'all' ? { pool } : {}) };

  const breadthQ = useQuery({ queryKey: ['mkt-breadth', tsParams], queryFn: () => endpoints.marketBreadth(tsParams) });
  const membersQ = useQuery({ queryKey: ['mkt-members', start, asOf], queryFn: () => endpoints.marketIndexMembers({ ...(start ? { start } : {}), end: asOf }) });
  const statusQ = useQuery({ queryKey: ['mkt-status', tsParams], queryFn: () => endpoints.marketStatus(tsParams) });
  const mvQ = useQuery({ queryKey: ['mkt-mv', asOf, mvField, pool], queryFn: () => endpoints.marketMvDistribution({ as_of: asOf, field: mvField, bins: 30, ...(pool !== 'all' ? { pool } : {}) }) });
  const compQ = useQuery({ queryKey: ['mkt-comp', asOf], queryFn: () => endpoints.marketComposition({ as_of: asOf }) });
  const ipoQ = useQuery({ queryKey: ['mkt-ipo'], queryFn: () => endpoints.marketNewListings({ bucket: 'month' }) });

  React.useEffect(() => { if (breadthQ.data) pushRecent(`全市场 · ${asOf}`); }, [breadthQ.data, asOf]);

  // ── ① 涨跌广度 (diverging stacked bars; 涨停/跌停 as the darker subset) ───────
  const breadthOpt = useMemo(() => {
    const d = breadthQ.data; if (!d?.dates?.length) return null;
    const lu = d.limit_up || [], ld = d.limit_down || [];
    const advPlain = d.advancers.map((v, i) => Math.max(0, v - (lu[i] || 0)));
    const decPlain = d.decliners.map((v, i) => -Math.max(0, v - (ld[i] || 0)));
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      legend: { show: true, top: 0, itemWidth: 12, itemHeight: 8, data: ['上涨家数', '涨停', '下跌家数', '跌停'] },
      grid: { left: 44, right: 14, top: 24, bottom: 24 },
      xAxis: { ...AXIS_DATE, data: d.dates },
      yAxis: { type: 'value' },
      series: [
        { name: '上涨家数', type: 'bar', stack: 'up', data: advPlain, itemStyle: { color: VIZ_UP, opacity: 0.85 } },
        { name: '涨停', type: 'bar', stack: 'up', data: lu, itemStyle: { color: 'oklch(0.45 0.2 27)' } },
        { name: '下跌家数', type: 'bar', stack: 'down', data: decPlain, itemStyle: { color: VIZ_DOWN, opacity: 0.8 } },
        { name: '跌停', type: 'bar', stack: 'down', data: ld.map((v) => -v), itemStyle: { color: 'oklch(0.42 0.14 152)' } },
      ],
    };
  }, [breadthQ.data]);

  // ── ② 指数成分覆盖 (multi-line) ──────────────────────────────────────────────
  const membersOpt = useMemo(() => {
    const d = membersQ.data; if (!d?.dates?.length) return null;
    const names = { hs300: '沪深300', zz500: '中证500', zz1000: '中证1000', sse50: '上证50' };
    return {
      tooltip: { trigger: 'axis' },
      legend: { show: true, top: 0, itemWidth: 14, itemHeight: 8, data: Object.values(names) },
      grid: { left: 44, right: 14, top: 24, bottom: 24 },
      xAxis: { ...AXIS_DATE, data: d.dates },
      yAxis: { type: 'value' },
      series: Object.keys(names).map((k, i) => ({
        name: names[k], type: 'line', showSymbol: false, smooth: false,
        data: d.series[k] || [], lineStyle: { width: 1.6, color: VIZ_SERIES[i] }, itemStyle: { color: VIZ_SERIES[i] },
      })),
    };
  }, [membersQ.data]);

  // ── ③ 交易状态占比 (100% stacked area) ───────────────────────────────────────
  const statusOpt = useMemo(() => {
    const d = statusQ.data; if (!d?.dates?.length) return null;
    const pct = (arr) => arr.map((v, i) => (d.total[i] ? +(v / d.total[i] * 100).toFixed(2) : 0));
    const mk = (name, arr, color) => ({
      name, type: 'line', stack: 'mix', areaStyle: { color, opacity: 0.55 },
      showSymbol: false, lineStyle: { width: 0.6, color }, data: pct(arr),
    });
    return {
      tooltip: { trigger: 'axis', valueFormatter: (v) => `${v}%` },
      legend: { show: true, top: 0, itemWidth: 12, itemHeight: 8, data: ['可交易', '停牌', 'ST'] },
      grid: { left: 44, right: 14, top: 24, bottom: 24 },
      xAxis: { ...AXIS_DATE, data: d.dates },
      yAxis: { type: 'value', max: 100, axisLabel: { formatter: '{value}%' } },
      series: [
        mk('可交易', d.tradable, 'oklch(0.62 0.13 145)'),
        mk('停牌', d.suspended, 'oklch(0.66 0.13 70)'),
        mk('ST', d.st, 'oklch(0.58 0.18 27)'),
      ],
    };
  }, [statusQ.data]);

  // ── ④ 市值分布 (log-scale histogram + P10/P50/P90) ───────────────────────────
  const mvOpt = useMemo(() => {
    const d = mvQ.data; if (!d?.bins?.length) return null;
    const labels = d.bins.map((b) => fmtCap(b.lo));
    const counts = d.bins.map((b) => b.count);
    const markAt = (val) => {
      if (val == null) return null;
      const i = d.bins.findIndex((b) => val >= b.lo && val < b.hi);
      return i < 0 ? (val >= d.bins[d.bins.length - 1].lo ? d.bins.length - 1 : 0) : i;
    };
    const lines = [['P10', d.p10], ['P50', d.p50], ['P90', d.p90]]
      .map(([l, v]) => ({ l, x: markAt(v), v }))
      .filter((m) => m.x != null)
      .map((m) => ({ xAxis: m.x, label: { formatter: m.l, color: 'var(--fg)', fontSize: 9 }, lineStyle: { color: 'var(--fg)', type: 'dashed', width: 1 } }));
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' },
        formatter: (ps) => { const b = d.bins[ps[0].dataIndex]; return `${fmtCap(b.lo)} – ${fmtCap(b.hi)}<br/>家数 ${b.count}`; } },
      grid: { left: 40, right: 14, top: 16, bottom: 40 },
      xAxis: { type: 'category', data: labels, axisLabel: { hideOverlap: true, rotate: 35, fontSize: 8.5 } },
      yAxis: { type: 'value' },
      series: [{
        name: '家数', type: 'bar', data: counts, itemStyle: { color: 'oklch(0.55 0.12 248)', opacity: 0.78 },
        markLine: lines.length ? { symbol: 'none', data: lines } : undefined,
      }],
    };
  }, [mvQ.data]);

  // ── ⑤ 市场结构 (sunburst: exchange → board) ─────────────────────────────────
  const compOpt = useMemo(() => {
    const d = compQ.data; if (!d?.tree?.length) return null;
    const byEx = {};
    d.tree.forEach((r) => {
      byEx[r.exchange_label] = byEx[r.exchange_label] || { name: r.exchange_label, children: [] };
      byEx[r.exchange_label].children.push({ name: r.board_label, value: r.count });
    });
    const palette = ['oklch(0.5 0.13 248)', 'oklch(0.55 0.16 27)', 'oklch(0.6 0.13 70)', 'oklch(0.6 0.13 145)'];
    const data = Object.values(byEx).map((ex, i) => ({
      ...ex, itemStyle: { color: palette[i % palette.length] },
    }));
    return {
      tooltip: { trigger: 'item', formatter: (p) => `${p.name}<br/>${fmtNum(p.value)} 只 (${(p.percent || 0).toFixed(1)}%)` },
      series: [{
        type: 'sunburst', radius: ['18%', '92%'], data, sort: 'desc',
        emphasis: { focus: 'ancestor' },
        label: { fontSize: 9, fontFamily: 'var(--font-mono)', color: '#fff', minAngle: 8 },
        levels: [{}, { r0: '18%', r: '52%' }, { r0: '52%', r: '92%', label: { align: 'right' } }],
      }],
    };
  }, [compQ.data]);

  // ── ⑥ 新股节奏 (monthly bars) ───────────────────────────────────────────────
  const ipoOpt = useMemo(() => {
    const d = ipoQ.data; if (!d?.periods?.length) return null;
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 36, right: 14, top: 16, bottom: 28 },
      xAxis: { type: 'category', data: d.periods, axisLabel: { hideOverlap: true } },
      yAxis: { type: 'value' },
      series: [{ name: '当月新上市', type: 'bar', data: d.counts, itemStyle: { color: 'oklch(0.66 0.13 70)', opacity: 0.8 } }],
    };
  }, [ipoQ.data]);

  // footers (latest-point summary, mirrors the design's hover line)
  const breadthFoot = (() => { const d = breadthQ.data; if (!d?.dates?.length) return '—'; const i = d.dates.length - 1;
    return `${d.dates[i]} · 涨 ${fmtNum(d.advancers[i])} · 跌 ${fmtNum(d.decliners[i])} · 平 ${fmtNum(d.unchanged[i])} · 涨停 ${fmtNum(d.limit_up[i])} · 跌停 ${fmtNum(d.limit_down[i])}`; })();
  const membersFoot = (() => { const d = membersQ.data; if (!d?.dates?.length) return '—'; const i = d.dates.length - 1;
    return `${d.dates[i]} · 沪深300=${d.series.hs300[i]} · 中证500=${d.series.zz500[i]} · 中证1000=${d.series.zz1000[i]} · 上证50=${d.series.sse50[i]}`; })();
  const statusFoot = (() => { const d = statusQ.data; if (!d?.dates?.length) return '—'; const i = d.dates.length - 1; const t = d.total[i] || 1;
    return `${d.dates[i]} · 可交易 ${(d.tradable[i] / t * 100).toFixed(1)}% · 停牌 ${(d.suspended[i] / t * 100).toFixed(1)}% · ST ${(d.st[i] / t * 100).toFixed(1)}%`; })();
  const mvFoot = (() => { const d = mvQ.data; if (!d) return '—';
    return `中位数 ${fmtCap(d.p50)} · P10 ${fmtCap(d.p10)} · P90 ${fmtCap(d.p90)}`; })();
  const compFoot = (() => { const d = compQ.data; if (!d?.tree?.length) return '—'; const top = d.tree[0];
    return `点击外圈下钻 · ${top.exchange_label} ${top.board_label} ${fmtNum(top.count)} 只 (${(top.count / d.total * 100).toFixed(1)}%)`; })();
  const ipoFoot = (() => { const d = ipoQ.data; if (!d?.periods?.length) return '—'; const i = d.periods.length - 1; const last12 = d.counts.slice(-12).reduce((a, b) => a + b, 0);
    return `${d.periods[i]} · 新上市 ${d.counts[i]} 家 · 近 12 月累计 ${last12} 家`; })();

  const exportPanel = (id, name) => {
    const inst = refs.current[id]?.getEchartsInstance?.();
    if (!inst) return;
    downloadDataURL(inst.getDataURL({ pixelRatio: 2, backgroundColor: '#ffffff' }), `market_${name}_${asOf}.png`);
  };

  // Inlined as a plain function (not <Body/>) so the EChart keeps a stable tree
  // position across the page's many query-driven re-renders instead of remounting.
  const renderBody = ({ query, option, id, name, empty }) => {
    const pt = `market-panel-${id}`;
    const st = panelStateOf(query);
    if (st) return <PanelState kind={st} testId={`${pt}-${st}`} onAction={() => query.refetch()} />;
    if (!option) return <PanelState kind="empty" testId={`${pt}-empty`} hint={empty} />;
    return (
      <div data-testid={`${pt}-chart`} style={{ flex: 1, minHeight: 0, position: 'relative' }}
        onDoubleClick={() => exportPanel(id, name)} title="双击导出 PNG">
        <EChart ref={(r) => { refs.current[id] = r; }} option={option} height="100%" />
      </div>
    );
  };

  const refetchAll = () => [breadthQ, membersQ, statusQ, mvQ, compQ, ipoQ].forEach((q) => q.refetch());

  return (
    <TerminalShell
      module="viz" current="market" path={['viz/', 'market']}
      fkeys={[
        { k: 'F1', l: '切日期', onClick: () => { asOfRef.current?.focus(); asOfRef.current?.showPicker?.(); } },
        { k: 'F2', l: '刷新', onClick: refetchAll },
        { k: 'F3', l: '导出', onClick: () => exportPanel('breadth', 'breadth') },
      ]}
      titleBar={false}
    >
      {/* top control bar */}
      <div data-testid="viz-market-page" style={{
        padding: '0 14px', minHeight: 44, background: 'var(--surface)',
        borderBottom: '1px solid var(--border-strong)', display: 'flex', alignItems: 'center',
        gap: 0, flexShrink: 0, flexWrap: 'wrap', rowGap: 6, fontFamily: 'var(--font-mono)', fontSize: 11,
      }}>
        <span style={{ fontSize: 13, color: 'var(--fg)', fontWeight: 500, marginRight: 4 }}>市场全景</span>
        <span style={{ color: 'var(--fg-3)', fontSize: 10, marginRight: 16 }}>full-market cross-section &amp; breadth</span>

        <div style={{
          height: 28, padding: '0 10px', background: 'var(--surface-2)', border: '1px solid var(--border-strong)',
          borderRadius: 3, display: 'inline-flex', alignItems: 'center', gap: 8,
        }}>
          <span style={{ color: 'var(--fg-3)', fontSize: 10 }}>◷ DATE</span>
          <input ref={asOfRef} type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)}
            data-testid="market-datepicker"
            style={{ background: 'transparent', border: 'none', color: 'var(--fg)', fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 600, cursor: 'pointer' }} />
        </div>
        <span style={{ color: 'var(--fg-3)', fontSize: 9.5, margin: '0 14px 0 8px' }}>切日期 = 整屏快照重算</span>

        <FSelect label="池" value={pool} options={POOL_OPTIONS} onChange={setPool} width={100} testId="market-pool-select" />
        <span style={{ marginRight: 10 }} />
        <span style={{ color: 'var(--fg-3)', fontSize: 10, marginRight: 6 }}>区间:</span>
        <RangeButtons options={RANGES} active={range} onChange={setRange} testIdPrefix="market-range" />

        <div style={{ flex: 1 }} />
        <button className="btn ghost sm" data-testid="market-refresh" style={{ height: 24 }} onClick={refetchAll}>↻ 刷新</button>
        <button className="btn ghost sm" data-testid="market-export" style={{ height: 24 }} onClick={() => exportPanel('breadth', 'breadth')}>↗ 导出</button>
      </div>

      {/* 6-panel grid */}
      <div className="scroll" style={{ flex: 1, minHeight: 0, padding: 10, background: 'var(--bg)' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
          <VizPanel testId="market-panel-breadth" n="①" title="涨跌广度" en="advance / decline breadth" tag={<TimeseriesTag />}
            badge={breadthQ.data && <CoverageBadge testId="market-panel-breadth-coverage" covered={breadthQ.data.coverage?.covered || 0} total={breadthQ.data.coverage?.total || 0} />}
            footer={breadthFoot}>
            {renderBody({ query: breadthQ, option: breadthOpt, id: "breadth", name: "breadth", empty: "该区间无行情数据" })}
          </VizPanel>

          <VizPanel testId="market-panel-index" n="②" title="指数成分覆盖" en="index constituent count" tag={<TimeseriesTag />} footer={membersFoot}>
            {renderBody({ query: membersQ, option: membersOpt, id: "index", name: "index", empty: "该区间无成分数据" })}
          </VizPanel>

          <VizPanel testId="market-panel-status" n="③" title="交易状态占比" en="trading-status mix · 100% stacked" tag={<TimeseriesTag />}
            badge={statusQ.data?.total?.length ? <CoverageBadge testId="market-panel-status-coverage" covered={statusQ.data.total[statusQ.data.total.length - 1]} total={statusQ.data.total[statusQ.data.total.length - 1]} /> : null}
            footer={statusFoot}>
            {renderBody({ query: statusQ, option: statusOpt, id: "status", name: "status", empty: "该区间无交易状态数据" })}
          </VizPanel>

          <VizPanel testId="market-panel-mvdist" n="④" title="市值分布" en="market-cap distribution" tag={<SnapshotTag date={mvQ.data?.as_of || asOf} />}
            controls={<MiniToggle testIdPrefix="market-mvdist-toggle" options={[{ value: 'total_mv', label: '总市值' }, { value: 'circ_mv', label: '流通市值' }]} active={mvField} onChange={setMvField} />}
            badge={mvQ.data && <CoverageBadge testId="market-panel-mvdist-coverage" covered={mvQ.data.coverage?.covered || 0} total={mvQ.data.coverage?.total || 0} />}
            footer={mvFoot}>
            {renderBody({ query: mvQ, option: mvOpt, id: "mvdist", name: "mvdist", empty: "该日无市值数据 · 试试更近的交易日" })}
          </VizPanel>

          <VizPanel testId="market-panel-composition" n="⑤" title="市场结构" en="exchange → board · sunburst" tag={<SnapshotTag date={compQ.data?.as_of || asOf} />}
            badge={compQ.data && <CoverageBadge testId="market-panel-composition-coverage" covered={compQ.data.coverage?.covered ?? compQ.data.total ?? 0} total={compQ.data.coverage?.total ?? compQ.data.total ?? 0} />}
            footer={compFoot}>
            {renderBody({ query: compQ, option: compOpt, id: "composition", name: "composition", empty: "无标的结构数据" })}
          </VizPanel>

          <VizPanel testId="market-panel-newlistings" n="⑥" title="新股节奏" en="IPO cadence · monthly" tag={<TimeseriesTag />} footer={ipoFoot}>
            {renderBody({ query: ipoQ, option: ipoOpt, id: "newlistings", name: "ipo", empty: "无上市日期数据" })}
          </VizPanel>
        </div>
      </div>
    </TerminalShell>
  );
}
