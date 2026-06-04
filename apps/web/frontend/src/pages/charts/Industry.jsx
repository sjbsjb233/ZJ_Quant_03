// 行业视图 / Industry Lens (VIZ Layer v2 §5) — classification tree + constituents +
// cross-industry compare + reverse lookup. Tree / level selection drives ②③④; the
// bottom bar (⑤) reverse-looks-up any symbol's full行业归属 path + peers. Every
// rollup goes through the backend's shared util (== 因子中性化口径, §5.2).
import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { TerminalShell, FSelect } from '../../components/shell';
import { EChart, SymbolSearch, DataTable } from '../../components/ui';
import { endpoints } from '../../lib/api';
import {
  CoverageBadge, PanelState, MiniToggle, VizPanel, fmtCap, fmtNum, panelStateProps,
} from './vizShared';

const RECENT_KEY = 'zjq_recent_charts';
function pushRecent(label) {
  try {
    const arr = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
    const next = [{ label, type: '行业' }, ...arr.filter((r) => r.label !== label)].slice(0, 20);
    localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch { /* ignore */ }
}

const CAP_METRICS = new Set(['total_mv', 'circ_mv', 'revenue', 'net_income', 'net_income_attr_parent', 'total_assets', 'total_equity']);
const PCT_METRICS = new Set(['roe', 'gross_margin']);
const FRAC_METRICS = new Set(['revenue_yoy', 'net_income_yoy']);

function fmtMetric(metric, v) {
  if (v == null || Number.isNaN(v)) return '—';
  if (CAP_METRICS.has(metric)) return fmtCap(v);
  if (FRAC_METRICS.has(metric)) return `${(v * 100).toFixed(1)}%`;
  if (PCT_METRICS.has(metric)) return `${v.toFixed(1)}%`;
  if (metric === 'pe_ttm') return v.toFixed(1);
  return fmtNum(v, 2);
}
function median(arr) {
  const v = arr.filter((x) => x != null).sort((a, b) => a - b);
  if (!v.length) return null;
  const m = Math.floor(v.length / 2);
  return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
}

// thin amber strip explaining a degraded panel (e.g. 市值未入库 -> 按家数显示).
function NoticeBar({ text, testId }) {
  return (
    <div data-testid={testId} style={{
      display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0,
      padding: '4px 10px', background: 'var(--warn-bg)',
      borderBottom: '1px solid var(--border-faint)',
      fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--warn)',
      lineHeight: 1.4,
    }}>
      <span style={{ flexShrink: 0 }}>⚠</span>
      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>{text}</span>
    </div>
  );
}

// ── tree (nested from flat nodes) ────────────────────────────────────────────
function IndustryTree({ nodes, sizeBy, selected, onSelect }) {
  const [expanded, setExpanded] = useState({});
  const { byParent, max } = useMemo(() => {
    const bp = {};
    let mx = 0;
    (nodes || []).forEach((n) => { (bp[n.parent || '__root__'] = bp[n.parent || '__root__'] || []).push(n); mx = Math.max(mx, n.value || 0); });
    Object.values(bp).forEach((list) => list.sort((a, b) => (b.value || 0) - (a.value || 0)));
    return { byParent: bp, max: mx || 1 };
  }, [nodes]);

  const renderNode = (n, depth) => {
    const kids = byParent[n.code] || [];
    const open = expanded[n.code];
    const isSel = n.code === selected;
    return (
      <React.Fragment key={n.code}>
        <div
          data-testid={`industry-tree-node-${n.code}`}
          onClick={() => { onSelect(n); if (kids.length) setExpanded((e) => ({ ...e, [n.code]: !e[n.code] })); }}
          style={{
            display: 'flex', alignItems: 'center', gap: 6, padding: '4px 10px',
            paddingLeft: 10 + depth * 16, cursor: 'pointer',
            background: isSel ? 'var(--info-bg)' : 'transparent',
            borderLeft: isSel ? '2px solid var(--info)' : '2px solid transparent',
          }}>
          <span style={{ width: 10, color: 'var(--fg-4)', fontSize: 9 }}>{kids.length ? (open ? '▾' : '▸') : ''}</span>
          <span style={{ flex: 1, color: isSel ? 'var(--info)' : depth ? 'var(--fg-2)' : 'var(--fg)', fontWeight: isSel ? 600 : depth ? 400 : 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{n.name || n.code}</span>
          <span style={{ width: 40, height: 6, background: 'var(--surface-2)', borderRadius: 2, position: 'relative', flexShrink: 0 }}>
            <span style={{ position: 'absolute', inset: 0, width: `${((n.value || 0) / max) * 100}%`, background: isSel ? 'var(--info)' : 'oklch(0.6 0.08 248 / 0.55)', borderRadius: 2 }} />
          </span>
          <span style={{ width: 50, textAlign: 'right', color: 'var(--fg-3)', fontSize: 9.5, fontVariantNumeric: 'tabular-nums', flexShrink: 0 }}>
            {sizeBy === 'mv' ? fmtCap(n.value) : fmtNum(n.value)}
          </span>
        </div>
        {open && kids.map((k) => renderNode(k, depth + 1))}
      </React.Fragment>
    );
  };

  const roots = byParent.__root__ || [];
  if (!roots.length) return <PanelState kind="empty" hint="该体系无行业节点" />;
  return <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11 }}>{roots.map((n) => renderNode(n, 0))}</div>;
}

// ── ③ cross-industry: median dot + 25–75% spread, click to select ────────────
function DistBars({ rows, metric, selected, onSelect }) {
  const max = Math.max(1, ...rows.map((r) => r.p75 ?? r.value ?? 0));
  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      {rows.map((r) => {
        const isSel = r.code === selected;
        const lo = (r.p25 ?? r.value ?? 0) / max;
        const hi = (r.p75 ?? r.value ?? 0) / max;
        const med = (r.p50 ?? r.value ?? 0) / max;
        return (
          <div key={r.code} onClick={() => onSelect(r.code)} style={{
            display: 'flex', alignItems: 'center', gap: 8, padding: '3px 12px',
            fontFamily: 'var(--font-mono)', fontSize: 10.5, cursor: 'pointer',
            background: isSel ? 'var(--info-bg)' : 'transparent',
            borderLeft: isSel ? '2px solid var(--info)' : '2px solid transparent',
          }}>
            <span style={{ width: 72, color: isSel ? 'var(--info)' : 'var(--fg)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: isSel ? 600 : 400 }}>{r.name || r.code}</span>
            <div style={{ flex: 1, height: 14, position: 'relative' }}>
              <div style={{ position: 'absolute', top: 5, left: `${lo * 100}%`, width: `${Math.max(0, hi - lo) * 100}%`, height: 4, background: 'oklch(0.55 0.13 248 / 0.28)', borderRadius: 2 }} />
              <div style={{ position: 'absolute', top: 3, left: `${med * 100}%`, width: 8, height: 8, marginLeft: -4, borderRadius: '50%', background: isSel ? 'var(--info)' : 'oklch(0.55 0.13 248)' }} />
            </div>
            <span style={{ width: 64, textAlign: 'right', color: 'var(--fg-2)', fontVariantNumeric: 'tabular-nums' }}>{fmtMetric(metric, r.value)}</span>
          </div>
        );
      })}
    </div>
  );
}

function RankBars({ rows, metric, onClick }) {
  const max = Math.max(1, ...rows.map((r) => Math.abs(r.value || 0)));
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5, padding: '10px 12px' }}>
      {rows.map((r, i) => (
        <div key={r.symbol} onClick={() => onClick && onClick(r.symbol)} style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: 'var(--font-mono)', fontSize: 10.5, cursor: onClick ? 'pointer' : 'default' }}>
          <span style={{ width: 14, color: 'var(--fg-4)', textAlign: 'right' }}>{i + 1}</span>
          <span style={{ width: 86, color: 'var(--fg)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name || r.symbol}</span>
          <div style={{ flex: 1, height: 12, background: 'var(--surface-2)', borderRadius: 2, position: 'relative' }}>
            <div style={{ position: 'absolute', inset: 0, width: `${(Math.abs(r.value || 0) / max) * 100}%`, background: 'oklch(0.55 0.13 248 / 0.7)', borderRadius: 2 }} />
          </div>
          <span style={{ width: 64, textAlign: 'right', color: 'var(--fg-2)', fontVariantNumeric: 'tabular-nums' }}>{fmtMetric(metric, r.value)}</span>
        </div>
      ))}
    </div>
  );
}

export default function IndustryPage() {
  const nav = useNavigate();
  const [searchParams] = useSearchParams();
  const [system, setSystem] = useState('sw');
  const [level, setLevel] = useState(1);
  const [metric, setMetric] = useState('total_mv');
  const [selectedCode, setSelectedCode] = useState(null);
  const [withinMode, setWithinMode] = useState('rank'); // 'dist' | 'rank'
  const [lookupSym, setLookupSym] = useState(searchParams.get('symbol') || '600519.SH');

  const systemsQ = useQuery({ queryKey: ['ind-systems'], queryFn: () => endpoints.industrySystems() });
  const systemOptions = (systemsQ.data?.systems || []).map((s) => ({ value: s.value, label: `${s.label} (${s.count})` }));
  const metricOptions = (systemsQ.data?.metrics || []).map((m) => ({ value: m.value, label: m.label }));
  const metricLabel = metricOptions.find((m) => m.value === metric)?.label || metric;

  // default the system selector to whatever the warehouse actually has.
  useEffect(() => {
    const sys = systemsQ.data?.systems;
    if (sys?.length && !sys.find((s) => s.value === system)) setSystem(sys[0].value);
  }, [systemsQ.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const sizeBy = (metric === 'total_mv' || metric === 'circ_mv') ? 'mv' : 'count';
  const treeQ = useQuery({ queryKey: ['ind-tree', system, sizeBy], queryFn: () => endpoints.industryTree({ system, size_by: sizeBy }), enabled: !!system });
  const aggQ = useQuery({ queryKey: ['ind-agg', system, level, metric], queryFn: () => endpoints.industryAggregates({ system, level, metric }), enabled: !!system });
  const compQ = useQuery({ queryKey: ['ind-comp', system, level, selectedCode], queryFn: () => endpoints.industryComposition({ system, level, code: selectedCode }), enabled: !!system && !!selectedCode });
  const breakdownQ = useQuery({ queryKey: ['ind-bd', system, level, metric, selectedCode], queryFn: () => endpoints.industryAggregates({ system, level, metric, breakdown: 'symbol', code: selectedCode }), enabled: !!system && !!selectedCode });
  const lookupQ = useQuery({ queryKey: ['ind-lookup', lookupSym, system], queryFn: () => endpoints.industryLookup({ symbol: lookupSym, system }), enabled: !!lookupSym && !!system });

  // Non-data panel states (loading / idle-waiting / error / 数据未入库). ②④ are
  // disabled until an industry is selected -> they show an 'idle' hint, never an
  // endless spinner; a DATA_UNAVAILABLE response renders as a calm "数据未入库".
  const treeState = panelStateProps(treeQ);
  const aggState = panelStateProps(aggQ);
  const compState = panelStateProps(compQ, { idleHint: '请先在左侧行业树或上方对比图中选择行业' });
  const breakdownState = panelStateProps(breakdownQ, { idleHint: '请先选择行业以查看行业内分布' });

  const industries = aggQ.data?.industries || [];
  // keep a valid selection: default to the largest industry at this level.
  useEffect(() => {
    if (!industries.length) return;
    if (!selectedCode || !industries.find((r) => r.code === selectedCode)) {
      setSelectedCode(industries[0].code);
    }
  }, [industries]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (selectedCode) pushRecent(`${systemsQ.data?.systems?.find((s) => s.value === system)?.label || system} · ${compQ.data?.industry?.name || selectedCode}`); }, [selectedCode, compQ.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const selectNode = (n) => { setSelectedCode(n.code); if (n.level && n.level !== level) setLevel(n.level); };

  // selected-industry header stats from the composition payload.
  const members = compQ.data?.members || [];
  const header = useMemo(() => {
    const totalMv = members.reduce((a, m) => a + (m.mv || 0), 0);
    return {
      count: compQ.data?.count ?? members.length,
      totalMv,
      pe: median(members.map((m) => m.pe)),
      roe: median(members.map((m) => m.roe)),
      metricMedian: industries.find((r) => r.code === selectedCode)?.value,
    };
  }, [members, industries, selectedCode, compQ.data]);

  const selName = compQ.data?.industry?.name || industries.find((r) => r.code === selectedCode)?.name || selectedCode || '—';

  // ④ distribution histogram (when 分布 selected)
  const distOpt = useMemo(() => {
    const items = breakdownQ.data?.breakdown || [];
    if (withinMode !== 'dist' || !items.length) return null;
    const vals = items.map((x) => x.value).filter((v) => v != null);
    if (!vals.length) return null;
    const lo = Math.min(...vals), hi = Math.max(...vals);
    const n = Math.min(12, Math.max(4, vals.length));
    const step = (hi - lo) / n || 1;
    const counts = new Array(n).fill(0);
    vals.forEach((v) => { counts[Math.min(n - 1, Math.floor((v - lo) / step))] += 1; });
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 32, right: 14, top: 12, bottom: 36 },
      xAxis: { type: 'category', data: counts.map((_, i) => fmtMetric(metric, lo + i * step)), axisLabel: { rotate: 30, fontSize: 8 } },
      yAxis: { type: 'value' },
      series: [{ type: 'bar', data: counts, itemStyle: { color: 'oklch(0.55 0.12 248)', opacity: 0.75 } }],
    };
  }, [breakdownQ.data, withinMode, metric]);

  const memberCols = [
    { key: 'symbol', label: '代码', mono: true, color: 'var(--info)',
      render: (v) => <span style={{ textDecoration: 'underline', textUnderlineOffset: 2 }}>{v}</span> },
    { key: 'name', label: '名称', color: 'var(--fg)' },
    { key: 'mv', label: '总市值', align: 'right', mono: true, color: 'var(--fg)', render: (v) => fmtCap(v) },
    { key: 'pe', label: 'PE(TTM)', align: 'right', mono: true, render: (v) => (v == null ? '·' : v.toFixed(1)) },
    { key: 'roe', label: 'ROE', align: 'right', mono: true, render: (v) => (v == null ? '·' : `${v.toFixed(1)}%`) },
    { key: '__metric', label: `指标·${metricLabel}`, align: 'right', mono: true, color: 'var(--fg)',
      render: (_, row) => {
        // composition carries mv/pe/roe per member; show those directly and fall
        // back to '·' for metrics it doesn't include (kept honest, not faked).
        const v = metric === 'total_mv' ? row.mv : metric === 'roe' ? row.roe : metric === 'pe_ttm' ? row.pe : undefined;
        return v == null ? '·' : fmtMetric(metric, v);
      } },
    { key: '__jump', label: '跳转', align: 'right',
      render: (_, row) => (
        <span style={{ display: 'inline-flex', gap: 4, justifyContent: 'flex-end' }}>
          <span data-testid={`industry-jump-kline-${row.symbol}`} onClick={(e) => { e.stopPropagation(); nav(`/data/charts/kline?symbol=${row.symbol}`); }}
            style={{ fontSize: 9.5, color: 'var(--fg-3)', border: '1px solid var(--border-strong)', borderRadius: 2, padding: '0 5px', cursor: 'pointer' }}>K线 ↗</span>
          <span data-testid={`industry-jump-fund-${row.symbol}`} onClick={(e) => { e.stopPropagation(); nav(`/data/charts/fundamentals?symbol=${row.symbol}`); }}
            style={{ fontSize: 9.5, color: 'var(--fg-3)', border: '1px solid var(--border-strong)', borderRadius: 2, padding: '0 5px', cursor: 'pointer' }}>财务 ↗</span>
        </span>
      ) },
  ];

  const levelsInfo = treeQ.data?.levels || {};
  const levelsSummary = Object.keys(levelsInfo).length
    ? Object.entries(levelsInfo).map(([lv, c]) => `${['', '一级', '二级', '三级'][+lv] || `L${lv}`} ${c}`).join(' · ')
    : '—';

  return (
    <TerminalShell
      module="viz" current="industry" path={['viz/', 'industry']}
      fkeys={[
        { k: 'F1', l: '切体系' },
        { k: 'F2', l: '切层级', onClick: () => setLevel((l) => (l % 3) + 1) },
        { k: 'F3', l: '刷新', onClick: () => [treeQ, aggQ, compQ, breakdownQ].forEach((q) => q.refetch()) },
      ]}
      titleBar={false}
    >
      {/* top control bar */}
      <div data-testid="viz-industry-page" style={{
        padding: '0 14px', minHeight: 44, background: 'var(--surface)', borderBottom: '1px solid var(--border-strong)',
        display: 'flex', alignItems: 'center', gap: 0, flexShrink: 0, flexWrap: 'wrap', rowGap: 6,
        fontFamily: 'var(--font-mono)', fontSize: 11,
      }}>
        <span style={{ fontSize: 13, color: 'var(--fg)', fontWeight: 500, marginRight: 16 }}>行业视图</span>
        <FSelect label="体系" value={system} options={systemOptions.length ? systemOptions : [{ value: 'sw', label: '申万' }]} onChange={setSystem} width={120} testId="industry-system-select" />
        <span style={{ marginRight: 10 }} />
        <span style={{ color: 'var(--fg-3)', fontSize: 10, marginRight: 6 }}>层级:</span>
        <div style={{ display: 'inline-flex', border: '1px solid var(--border-strong)', borderRadius: 3, overflow: 'hidden' }}>
          {[1, 2, 3].map((lv, i) => (
            <button key={lv} data-testid={`industry-level-L${lv}`} onClick={() => setLevel(lv)} style={{
              height: 26, padding: '0 11px', background: level === lv ? 'var(--fg)' : 'transparent',
              color: level === lv ? 'var(--bg)' : 'var(--fg-2)', border: 'none',
              borderRight: i < 2 ? '1px solid var(--border-strong)' : 'none',
              fontFamily: 'var(--font-mono)', fontSize: 10.5, cursor: 'pointer',
            }}>{`L${lv}`}</button>
          ))}
        </div>
        <span style={{ marginRight: 14 }} />
        <FSelect label="指标" value={metric} options={metricOptions.length ? metricOptions : [{ value: 'total_mv', label: '总市值' }]} onChange={setMetric} width={100} testId="industry-metric-select" />

        <div style={{ flex: 1 }} />
        <span style={{ color: 'var(--fg-3)', fontSize: 10, marginRight: 12 }}>{treeQ.data?.system_label || system} · {levelsSummary}</span>
      </div>

      {/* body: tree | right column */}
      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        {/* ① 行业树 */}
        <aside style={{ width: 248, flexShrink: 0, borderRight: '1px solid var(--border)', background: 'var(--surface)', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <header style={{ padding: '6px 10px', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)' }}>① 行业树</span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--fg-4)' }}>节点大小={(treeQ.data?.size_by || sizeBy) === 'mv' ? '总市值' : '家数'}</span>
            <div style={{ flex: 1 }} />
            {treeQ.data && <CoverageBadge testId="industry-tree-coverage" covered={treeQ.data.coverage?.covered || 0} total={treeQ.data.coverage?.total || 0} />}
          </header>
          {treeQ.data?.notice && <NoticeBar testId="industry-tree-notice" text={treeQ.data.notice} />}
          <div className="scroll" data-testid="industry-tree" style={{ flex: 1, minHeight: 0, padding: '4px 0' }}>
            {treeState
              ? <PanelState {...treeState} onAction={() => treeQ.refetch()} />
              : <IndustryTree nodes={treeQ.data?.nodes} sizeBy={treeQ.data?.size_by} selected={selectedCode} onSelect={selectNode} />}
          </div>
        </aside>

        {/* right column */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0, minHeight: 0 }}>
          {/* selected-industry header */}
          <div data-testid="industry-selected-header" style={{ padding: '8px 14px', borderBottom: '1px solid var(--border)', background: 'var(--surface)', display: 'flex', alignItems: 'center', gap: 16, flexShrink: 0, flexWrap: 'wrap', rowGap: 6 }}>
            <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8, padding: '3px 10px', background: 'var(--info-bg)', border: '1px solid var(--info)', borderRadius: 4 }}>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--info)' }}>● 当前选中</span>
              <span style={{ fontSize: 14, color: 'var(--fg)', fontWeight: 600 }}>{selName}</span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>{selectedCode || '—'}</span>
            </div>
            {[
              ['成分股', `${fmtNum(header.count)} 只`],
              ['总市值', fmtCap(header.totalMv)],
              [`指标·${metricLabel}中位`, fmtMetric(metric, header.metricMedian)],
              ['PE(TTM)中位', header.pe == null ? '—' : header.pe.toFixed(1)],
              ['ROE中位', header.roe == null ? '—' : `${header.roe.toFixed(1)}%`],
            ].map(([k, v]) => (
              <div key={k}>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{k}</div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--fg)', fontWeight: 500, fontVariantNumeric: 'tabular-nums' }}>{v}</div>
              </div>
            ))}
          </div>

          {/* ③ + ④ side by side */}
          <div style={{ flex: 1, display: 'flex', minHeight: 0, borderBottom: '1px solid var(--border)' }}>
            <VizPanel testId="industry-panel-compare" n="③" title="行业横向对比" en={`cross-industry · ${metricLabel}`} minHeight={0}
              style={{ flex: 1.15, borderRight: '1px solid var(--border)', border: 'none', borderRadius: 0 }}
              badge={aggQ.data && <CoverageBadge covered={aggQ.data.coverage?.covered || 0} total={aggQ.data.coverage?.total || 0} />}>
              <div style={{ padding: '3px 12px', fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', borderBottom: '1px solid var(--border-faint)', flexShrink: 0 }}>中位数 ● · 25–75% 分布区间 ▬ · 点行选中</div>
              <div className="scroll" style={{ flex: 1, minHeight: 0, padding: '6px 0' }}>
                {aggState
                  ? <PanelState {...aggState} onAction={() => aggQ.refetch()} />
                  : industries.length
                    ? <DistBars rows={industries} metric={metric} selected={selectedCode} onSelect={setSelectedCode} />
                    : <PanelState kind="empty" hint="该层级 / 指标下无行业数据" />}
              </div>
            </VizPanel>

            <VizPanel testId="industry-panel-within" n="④" title="行业内分布" en={`within ${selName}`} minHeight={0}
              style={{ flex: 1, border: 'none', borderRadius: 0 }}
              controls={<MiniToggle testIdPrefix="industry-within-toggle" options={[{ value: 'dist', label: '分布' }, { value: 'rank', label: '排名' }]} active={withinMode} onChange={setWithinMode} />}
              badge={breakdownQ.data && <CoverageBadge covered={(breakdownQ.data.breakdown || []).length} total={header.count} />}>
              <div style={{ padding: '3px 12px', fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', borderBottom: '1px solid var(--border-faint)', flexShrink: 0 }}>选中行业成分股 · {metricLabel}</div>
              <div className="scroll" style={{ flex: 1, minHeight: 0 }}>
                {breakdownState
                  ? <PanelState {...breakdownState} onAction={() => breakdownQ.refetch()} />
                  : !(breakdownQ.data?.breakdown || []).length
                    ? <PanelState kind="empty" hint="选中行业内无该指标数据" />
                    : withinMode === 'rank'
                      ? <RankBars rows={breakdownQ.data.breakdown} metric={metric} onClick={(s) => setLookupSym(s)} />
                      : distOpt ? <div style={{ height: '100%' }}><EChart option={distOpt} height="100%" /></div>
                        : <PanelState kind="empty" hint="数据点过少,无法成图" />}
              </div>
            </VizPanel>
          </div>

          {/* ② 行业成分 table */}
          <VizPanel testId="industry-panel-constituents" n="②" title="行业成分" en={`constituents of ${selName} · 点击行跳转`} minHeight={0}
            style={{ flex: 1.1, border: 'none', borderRadius: 0 }}
            badge={compQ.data && <CoverageBadge covered={compQ.data.coverage?.covered || 0} total={compQ.data.coverage?.total || 0} />}>
            {compQ.data?.notice && <NoticeBar testId="industry-comp-notice" text={compQ.data.notice} />}
            <div className="scroll" style={{ flex: 1, minHeight: 0 }}>
              {compState
                ? <PanelState {...compState} onAction={() => compQ.refetch()} />
                : <DataTable columns={memberCols} rows={members} rowKey={(r) => r.symbol}
                    rowTestId={(r) => `industry-constituent-row-${r.symbol}`}
                    onRowClick={(r) => nav(`/data/charts/kline?symbol=${r.symbol}`)} emptyLabel="选中行业暂无成分股" />}
            </div>
          </VizPanel>
        </div>
      </div>

      {/* ⑤ 个股反查 — full-width bottom bar */}
      <div style={{
        flexShrink: 0, borderTop: '1px solid var(--border-strong)', background: 'var(--surface-2)',
        padding: '8px 14px', display: 'flex', alignItems: 'center', gap: 16, minHeight: 56, flexWrap: 'wrap', rowGap: 8,
      }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)' }}>⑤ 个股反查 · reverse lookup</span>
          <div data-testid="industry-lookup-input"><SymbolSearch value={lookupSym} onChange={(s) => setLookupSym(s)} width={210} /></div>
        </div>
        <div style={{ width: 1, height: 38, background: 'var(--border)' }} />
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)' }}>行业归属路径 · 点行业可选中</span>
          <div data-testid="industry-lookup-path" style={{ display: 'flex', alignItems: 'center', gap: 6, fontFamily: 'var(--font-mono)', fontSize: 11 }}>
            <span style={{ color: 'var(--fg-3)' }}>{lookupQ.data?.system_label || system}</span>
            {(lookupQ.data?.path || []).map((c, i, a) => (
              <React.Fragment key={c.code}>
                <span style={{ color: 'var(--fg-mute)' }}>›</span>
                <span data-testid={`industry-lookup-path-node-${c.code}`} onClick={() => { setSelectedCode(c.code); if (c.level) setLevel(c.level); }}
                  style={{ color: 'var(--info)', textDecoration: 'underline', textUnderlineOffset: 2, cursor: 'pointer', fontWeight: i === a.length - 1 ? 600 : 400 }}>{c.name || c.code}</span>
              </React.Fragment>
            ))}
            {lookupQ.data && !(lookupQ.data.path || []).length && <span style={{ color: 'var(--warn)' }}>⚠ 该标的无行业归属</span>}
          </div>
        </div>
        <div style={{ width: 1, height: 38, background: 'var(--border)' }} />
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 5, minWidth: 0 }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)' }}>同业 peers · 点击反查该股</span>
          <div data-testid="industry-lookup-peers" style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
            {(lookupQ.data?.peers || []).map((p) => (
              <span key={p.symbol} data-testid={`industry-peer-${p.symbol}`} onClick={() => setLookupSym(p.symbol)} style={{
                fontFamily: 'var(--font-mono)', fontSize: 10.5, padding: '2px 8px', background: 'var(--surface)',
                border: '1px solid var(--border)', borderRadius: 3, color: 'var(--fg-2)', cursor: 'pointer',
              }}>{p.name || p.symbol}</span>
            ))}
            {lookupQ.data && !(lookupQ.data.peers || []).length && <span style={{ color: 'var(--fg-4)', fontSize: 10.5 }}>— 无同业 —</span>}
          </div>
        </div>
      </div>
    </TerminalShell>
  );
}
