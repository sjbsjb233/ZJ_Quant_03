// /factors/baskets/:id/items/:itemId — §10.3 条目结果详情页. A single entry
// (factor × params) in basket context. Snapshot switcher, KEY METRICS, four
// chart blocks (settled vs pending shown distinctly), 历代追踪, 最近一次执行, 操作.
// Ported from the Claude Design mockup fl-entry-detail.jsx into the real
// terminal shell + react-query + API + shared basket kit.
import React from 'react';
import {
  useParams, useNavigate, useSearchParams, Link,
} from 'react-router-dom';
import { useQuery, useMutation } from '@tanstack/react-query';
import { endpoints } from '../../../lib/api';
import { TerminalShell } from '../../../components/shell';
import { Loading, ErrorState, Empty } from '../../../components/ui';
import {
  Badge, ParamChips, SectionHead, KPIStrip, fmtNum, fmtPct, degradeValue,
} from './kit';

// Full metric set shown in the KEY METRICS strip (§10.3).
const METRIC_COLS = [
  { k: 'icir_annual', l: 'ICIR(年化)', f: (v) => fmtNum(v, 2), hi: 0.5 },
  { k: 'rank_ic_mean', l: 'RankIC均值', f: (v) => fmtNum(v, 3), hi: 0.03 },
  { k: 'ic_win_rate', l: 'IC胜率', f: (v) => fmtPct(v, 1), hi: 0.55 },
  { k: 'ls_sharpe', l: '多空夏普', f: (v) => fmtNum(v, 2), hi: 1 },
  { k: 'ls_max_drawdown', l: '多空最大回撤', f: (v) => fmtPct(v, 1), neg: true },
  { k: 'turnover', l: '换手率', f: (v) => fmtPct(v, 1) },
  { k: 'coverage', l: '覆盖度', f: (v) => fmtPct(v, 1), hi: 0.9 },
  { k: 'ic_t_stat', l: 'IC t值', f: (v) => fmtNum(v, 2), hi: 2 },
];

export default function EntryDetail() {
  const { id, itemId } = useParams();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const snapshot = searchParams.get('snapshot') || undefined;

  const head = useQuery({
    queryKey: ['basketItem', id, itemId, snapshot || null],
    queryFn: () => endpoints.basketItem(id, itemId, snapshot),
  });
  const icQ = useQuery({
    queryKey: ['basketItemSeries', id, itemId, 'ic', snapshot || null],
    queryFn: () => endpoints.basketItemSeries(id, itemId, 'ic', snapshot),
  });
  const layersQ = useQuery({
    queryKey: ['basketItemSeries', id, itemId, 'layers', snapshot || null],
    queryFn: () => endpoints.basketItemSeries(id, itemId, 'layers', snapshot),
  });
  const turnQ = useQuery({
    queryKey: ['basketItemSeries', id, itemId, 'turnover', snapshot || null],
    queryFn: () => endpoints.basketItemSeries(id, itemId, 'turnover', snapshot),
  });
  const histQ = useQuery({
    queryKey: ['basketItemHistory', id, itemId],
    queryFn: () => endpoints.basketItemHistory(id, itemId),
  });
  const snapsQ = useQuery({
    queryKey: ['basketSnapshots', id],
    queryFn: () => endpoints.basketSnapshots(id),
  });

  const rerun = useMutation({
    mutationFn: () => endpoints.submitBasketRun(id, { mode: 'incremental' }),
    onSuccess: () => navigate('/factors/tasks'),
  });

  const d = head.data;
  const snaps = snapsQ.data?.items || [];

  function pickSnapshot(sid) {
    const next = new URLSearchParams(searchParams);
    if (sid) next.set('snapshot', sid); else next.delete('snapshot');
    setSearchParams(next);
  }

  // export the IC ledger slice client-side → CSV download.
  function exportCsv() {
    const rows = icQ.data?.rows || [];
    const header = ['date', 'ic', 'rank_ic', 'coverage', 'settled'];
    const lines = [header.join(',')];
    rows.forEach((r) => {
      lines.push([r.date, r.ic ?? '', r.rank_ic ?? '', r.coverage ?? '', r.settled ? 1 : 0].join(','));
    });
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${d?.readable_id || `${id}.${itemId}`}_ic_ledger.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  const snapSwitcher = (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>快照</span>
      <select
        value={snapshot || (snaps[0]?.snapshot_id ?? snaps[0]?.id ?? '')}
        onChange={(e) => pickSnapshot(e.target.value)}
        style={{ height: 26, padding: '0 8px', background: 'var(--surface-2)', border: '1px solid var(--border-strong)', borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)' }}
      >
        {snaps.length === 0 && <option value="">（最新）</option>}
        {snaps.map((s) => {
          const sid = s.snapshot_id || s.id;
          return <option key={sid} value={sid}>{s.asOf || sid}{s.rev != null ? ` · rev ${s.rev}` : ''}</option>;
        })}
      </select>
    </span>
  );

  return (
    <TerminalShell
      module="factor"
      current="baskets"
      path={['factor_mgmt/', { label: 'baskets/', to: '/factors/baskets' }, { label: '条目', to: `/factors/baskets/${id}` }, d?.label || itemId]}
      title={d?.label || '条目结果'}
      subtitle="basket entry · 单条目结果详情 · 结算/待结算分别呈现"
      actions={snapSwitcher}
    >
      {head.isLoading ? (
        <Loading />
      ) : head.isError ? (
        <ErrorState error={head.error} onRetry={head.refetch} />
      ) : !d ? (
        <Empty label="无条目数据" hint="该条目在此快照下没有结果" />
      ) : (
        <div className="scroll" style={{ flex: 1, minHeight: 0, background: 'var(--bg)' }}>
          <Header d={d} id={id} itemId={itemId} />

          <div style={{ padding: 16 }}>
            <SectionHead title="KEY METRICS" meta="主周期 · 全部指标" />
            <div style={{ marginTop: -1, marginBottom: 16 }}>
              <KeyMetrics metrics={d.metrics || {}} />
            </div>

            {/* Charts 2×2 */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
              <ChartCard title="IC 序列" meta="柱=每期 RankIC · 线=累计 · 实心已结算 / 空心待结算"
                q={icQ} empty="无 IC 序列">
                <ICChart rows={icQ.data?.rows || []} />
              </ChartCard>

              <ChartCard title="分层回测" meta={`${guessGroups(layersQ.data?.rows)} 分组累计收益`}
                q={layersQ} empty="无分层数据">
                <LayersChart rows={layersQ.data?.rows || []} />
              </ChartCard>

              <ChartCard title="换手" meta="相邻调仓换手率" q={turnQ} empty="无换手数据">
                <TurnoverChart rows={turnQ.data?.rows || []} />
              </ChartCard>

              <ChartCard title="历代追踪" meta="ICIR / 夏普 随快照演进" q={histQ} empty="无历史快照">
                <HistoryChart series={histQ.data?.series || []} />
              </ChartCard>
            </div>

            {/* delta table + run/actions */}
            <div style={{ marginTop: 16, display: 'grid', gridTemplateColumns: '1.2fr 1fr', gap: 16 }}>
              <HistoryDeltaTable series={histQ.data?.series || []} />
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                <LastRun d={d} />
                <Actions
                  d={d} id={id}
                  rerun={rerun} exportCsv={exportCsv}
                  onRobustness={() => navigate(`/factors/${d.factor_id}/robustness?basket_id=${id}`)}
                />
              </div>
            </div>
          </div>
        </div>
      )}
    </TerminalShell>
  );
}

// ─── Header ──────────────────────────────────────────────────────────────────
function Header({ d, id, itemId }) {
  const [copied, setCopied] = React.useState(false);
  const readable = d.readable_id || `${id}.${itemId}`;
  const copy = () => {
    navigator.clipboard?.writeText(readable);
    setCopied(true);
    setTimeout(() => setCopied(false), 1000);
  };
  return (
    <div style={{ padding: '12px 18px', background: 'var(--surface)', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <h1 style={{ margin: 0, fontFamily: 'var(--font-mono)', fontSize: 17, fontWeight: 500, color: 'var(--fg)' }}>{d.label}</h1>
        <ParamChips params={d.params} />
        <span style={{ fontFamily: 'var(--font-mono)', color: d.direction === '+' ? 'var(--ok)' : 'var(--err)', fontWeight: 600 }}>{d.direction}</span>
        {d.degradation_level && d.degradation_level !== 'none'
          && <Badge group="degrade" value={degradeValue(d.degradation_level)} />}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 7, fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)', flexWrap: 'wrap' }}>
        <span>因子 <Link to={`/factors/library/${d.factor_id}`} style={{ color: 'var(--info)' }}>{d.factor_name || d.factor_id}</Link></span>
        {d.category && <span>类别 {d.category}</span>}
        {d.version != null && <span>v{d.version}</span>}
        <span style={{ color: 'var(--fg-mute)' }}>│</span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
          id <span style={{ color: 'var(--fg-2)' }}>{readable}</span>
          <span onClick={copy} title="复制" style={{ cursor: 'pointer', color: 'var(--info)' }}>⎘{copied && <span style={{ color: 'var(--ok)', marginLeft: 3 }}>✓</span>}</span>
        </span>
        <span style={{ color: 'var(--fg-mute)' }}>│</span>
        <span>台账 IC <span style={{ color: 'var(--fg)' }}>{d.rows_ic ?? '—'}</span> 行 · 因子 {d.rows_factor ?? '—'} 行</span>
        <span style={{ color: 'var(--fg-mute)' }}>│</span>
        <span><Badge group="settle" value="已结算" sm /> {d.settled_n ?? 0}</span>
        <span><Badge group="settle" value="待结算" sm /> {d.pending_n ?? 0}</span>
        {d.asOf && <><span style={{ color: 'var(--fg-mute)' }}>│</span><span>as_of <span style={{ color: 'var(--fg-2)' }}>{d.asOf}</span></span></>}
      </div>
    </div>
  );
}

// ─── KEY METRICS strip ───────────────────────────────────────────────────────
function KeyMetrics({ metrics }) {
  const items = METRIC_COLS.map((c) => {
    const v = metrics[c.k];
    let t;
    if (v == null) t = undefined;
    else if (c.neg) t = 'err';
    else if (c.hi != null && Math.abs(Number(v)) >= c.hi) t = 'ok';
    return { l: c.l, v: v == null ? '—' : c.f(v), t };
  });
  return <KPIStrip items={items} cols={8} />;
}

// ─── Chart card wrapper (handles per-block loading/error/empty) ──────────────
function ChartCard({ title, meta, q, empty, children }) {
  let body;
  if (q && q.isLoading) body = <Loading />;
  else if (q && q.isError) body = <ErrorState error={q.error} onRetry={q.refetch} />;
  else if (q && (q.data?.rows ? q.data.rows.length === 0 : (q.data?.series ? q.data.series.length === 0 : false))) body = <Empty label={empty} />;
  else body = children;
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
      <SectionHead title={title} meta={meta} />
      <div style={{ padding: 12, overflow: 'auto' }}>{body}</div>
    </div>
  );
}

// ─── IC series: per-period rank-IC bars + cumulative line; dashed divider at
// the settled→pending flip; pending periods drawn as hollow bars. ────────────
function ICChart({ rows }) {
  if (!rows.length) return <Empty label="无 IC 序列" />;
  const n = rows.length;
  const ics = rows.map((r) => Number(r.rank_ic ?? r.ic ?? 0));
  const cum = ics.reduce((a, v, i) => { a.push((a[i - 1] || 0) + v); return a; }, []);
  const W = 440; const H = 220; const pad = { t: 12, r: 40, b: 24, l: 34 };
  const maxAbs = (Math.max(0.0001, ...ics.map((v) => Math.abs(v)))) * 1.15;
  const cumMax = Math.max(0, ...cum) * 1.05 || 0.01;
  const cumMin = Math.min(0, ...cum) * 1.05;
  const Wi = W - pad.l - pad.r; const Hi = H - pad.t - pad.b;
  const bw = Wi / n; const y0 = pad.t + Hi / 2; const bs = (Hi / 2) / maxAbs;
  const cumY = (v) => pad.t + Hi - ((v - cumMin) / ((cumMax - cumMin) || 1)) * Hi;
  // first pending index → vertical divider
  const firstPending = rows.findIndex((r) => r.settled === false);
  const xSettle = firstPending >= 0 ? pad.l + firstPending * bw : null;
  return (
    <svg width={W} height={H} className="tnum" style={{ display: 'block' }}>
      <line x1={pad.l} y1={y0} x2={W - pad.r} y2={y0} stroke="var(--border-strong)" strokeWidth="0.5" />
      {xSettle != null && (
        <>
          <line x1={xSettle} y1={pad.t} x2={xSettle} y2={pad.t + Hi} stroke="var(--warn)" strokeWidth="1" strokeDasharray="3 3" opacity="0.7" />
          <text x={xSettle - 3} y={pad.t + 9} textAnchor="end" style={{ fontFamily: 'var(--font-mono)', fontSize: 8, fill: 'var(--warn)' }}>待结算→</text>
        </>
      )}
      {rows.map((r, i) => {
        const v = ics[i];
        const h = Math.abs(v) * bs; const y = v >= 0 ? y0 - h : y0;
        const pos = v >= 0; const pending = r.settled === false;
        return (
          <rect
            key={i} x={pad.l + i * bw + bw * 0.15} y={y} width={bw * 0.7} height={Math.max(0.6, h)}
            fill={pending ? 'none' : (pos ? 'var(--ok)' : 'var(--err)')} fillOpacity={0.6}
            stroke={pending ? (pos ? 'var(--ok)' : 'var(--err)') : 'none'} strokeWidth={pending ? 0.8 : 0}
            strokeDasharray={pending ? '1.5 1.5' : '0'}
          />
        );
      })}
      <path
        d={cum.map((v, i) => `${i === 0 ? 'M' : 'L'}${(pad.l + i * bw + bw / 2).toFixed(1)},${cumY(v).toFixed(1)}`).join(' ')}
        fill="none" stroke="var(--fg)" strokeWidth="1.3"
      />
      <g style={{ fontFamily: 'var(--font-mono)', fontSize: 9, fill: 'var(--fg-3)' }}>
        <text x={pad.l - 4} y={pad.t + 8} textAnchor="end">{maxAbs.toFixed(2)}</text>
        <text x={pad.l - 4} y={y0 + 3} textAnchor="end">0</text>
        <text x={W - pad.r + 4} y={pad.t + 8}>{cumMax.toFixed(2)}</text>
      </g>
      <g style={{ fontFamily: 'var(--font-mono)', fontSize: 9 }}>
        <Legend x={pad.l} y={H - 6} />
      </g>
    </svg>
  );
}

function Legend({ x, y }) {
  return (
    <>
      <rect x={x} y={y - 8} width={8} height={8} fill="var(--ok)" fillOpacity={0.6} />
      <text x={x + 12} y={y - 1} style={{ fill: 'var(--fg-3)' }}>已结算</text>
      <rect x={x + 56} y={y - 8} width={8} height={8} fill="none" stroke="var(--fg-3)" strokeDasharray="1.5 1.5" />
      <text x={x + 68} y={y - 1} style={{ fill: 'var(--fg-3)' }}>待结算</text>
    </>
  );
}

// ─── 分层回测: N-group cumulative-return lines, grouped by quantile. ──────────
function guessGroups(rows) {
  if (!rows || !rows.length) return 'N';
  return new Set(rows.map((r) => r.quantile)).size || 'N';
}
function LayersChart({ rows }) {
  if (!rows.length) return <Empty label="无分层数据" />;
  // group by quantile → ordered dates → cumulative ret
  const byQ = new Map();
  rows.forEach((r) => {
    const q = r.quantile;
    if (!byQ.has(q)) byQ.set(q, []);
    byQ.get(q).push(r);
  });
  const quantiles = Array.from(byQ.keys()).sort((a, b) => Number(a) - Number(b));
  const series = quantiles.map((q) => {
    const list = byQ.get(q).slice().sort((a, b) => String(a.date).localeCompare(String(b.date)));
    let acc = 0;
    const data = list.map((r) => { acc += Number(r.ret || 0); return acc; });
    return { q, data };
  });
  const len = Math.max(1, ...series.map((s) => s.data.length));
  const allVals = series.flatMap((s) => s.data);
  const lo = Math.min(0, ...allVals); const hi = Math.max(0.0001, ...allVals);
  const W = 440; const H = 220; const pad = { t: 12, r: 12, b: 22, l: 38 };
  const Wi = W - pad.l - pad.r; const Hi = H - pad.t - pad.b;
  const X = (i) => pad.l + (len <= 1 ? 0 : (i / (len - 1)) * Wi);
  const Y = (v) => pad.t + Hi - ((v - lo) / ((hi - lo) || 1)) * Hi;
  const palette = ['#a23b3b', '#c07a34', '#a89234', '#5a8a34', '#34908a', '#3478a0', '#5a4ea0', '#8a4ea0', '#a84e7a', '#7a7468'];
  return (
    <svg width={W} height={H} className="tnum" style={{ display: 'block' }}>
      <line x1={pad.l} y1={Y(0)} x2={W - pad.r} y2={Y(0)} stroke="var(--border-strong)" strokeWidth="0.5" />
      {series.map((s, si) => (
        <path
          key={s.q} fill="none" strokeWidth={si === 0 || si === series.length - 1 ? 1.6 : 1}
          stroke={palette[si % palette.length]}
          d={s.data.map((v, i) => `${i === 0 ? 'M' : 'L'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(' ')}
        />
      ))}
      <g style={{ fontFamily: 'var(--font-mono)', fontSize: 9, fill: 'var(--fg-3)' }}>
        <text x={pad.l - 4} y={pad.t + 8} textAnchor="end">{hi.toFixed(2)}</text>
        <text x={pad.l - 4} y={Y(0) + 3} textAnchor="end">0</text>
        <text x={pad.l - 4} y={pad.t + Hi} textAnchor="end">{lo.toFixed(2)}</text>
      </g>
    </svg>
  );
}

// ─── 换手: simple area/line. ─────────────────────────────────────────────────
function TurnoverChart({ rows }) {
  if (!rows.length) return <Empty label="无换手数据" />;
  const list = rows.slice().sort((a, b) => String(a.date).localeCompare(String(b.date)));
  const vals = list.map((r) => Number(r.turnover || 0));
  const hi = Math.max(0.0001, ...vals);
  const W = 440; const H = 220; const pad = { t: 12, r: 12, b: 22, l: 38 };
  const Wi = W - pad.l - pad.r; const Hi = H - pad.t - pad.b;
  const n = vals.length;
  const X = (i) => pad.l + (n <= 1 ? 0 : (i / (n - 1)) * Wi);
  const Y = (v) => pad.t + Hi - (v / hi) * Hi;
  const line = vals.map((v, i) => `${i === 0 ? 'M' : 'L'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(' ');
  const area = `${line} L${X(n - 1).toFixed(1)},${(pad.t + Hi).toFixed(1)} L${X(0).toFixed(1)},${(pad.t + Hi).toFixed(1)} Z`;
  return (
    <svg width={W} height={H} className="tnum" style={{ display: 'block' }}>
      <path d={area} fill="var(--warn)" fillOpacity={0.12} stroke="none" />
      <path d={line} fill="none" stroke="var(--warn)" strokeWidth="1.3" />
      <g style={{ fontFamily: 'var(--font-mono)', fontSize: 9, fill: 'var(--fg-3)' }}>
        <text x={pad.l - 4} y={pad.t + 8} textAnchor="end">{fmtPct(hi, 0)}</text>
        <text x={pad.l - 4} y={pad.t + Hi} textAnchor="end">0</text>
      </g>
    </svg>
  );
}

// ─── 历代追踪: small multi-series line (icir_annual / ls_sharpe over asOf). ───
function HistoryChart({ series }) {
  if (!series.length) return <Empty label="无历史快照" />;
  const list = series.slice().sort((a, b) => String(a.asOf).localeCompare(String(b.asOf)));
  const lines = [
    { label: 'ICIR(年化)', key: 'icir_annual', color: '#3478a0' },
    { label: '多空夏普', key: 'ls_sharpe', color: '#a89234' },
  ];
  const all = lines.flatMap((ln) => list.map((r) => Number(r[ln.key])).filter((v) => !Number.isNaN(v)));
  if (!all.length) return <Empty label="无可绘制指标" />;
  const lo = Math.min(...all); const hi = Math.max(...all);
  const W = 440; const H = 200; const pad = { t: 12, r: 12, b: 22, l: 38 };
  const Wi = W - pad.l - pad.r; const Hi = H - pad.t - pad.b;
  const n = list.length;
  const X = (i) => pad.l + (n <= 1 ? Wi / 2 : (i / (n - 1)) * Wi);
  const Y = (v) => pad.t + Hi - ((v - lo) / ((hi - lo) || 1)) * Hi;
  return (
    <svg width={W} height={H} className="tnum" style={{ display: 'block' }}>
      {lines.map((ln) => {
        const pts = list.map((r, i) => ({ i, v: Number(r[ln.key]) })).filter((p) => !Number.isNaN(p.v));
        return (
          <g key={ln.key}>
            <path
              fill="none" stroke={ln.color} strokeWidth="1.4"
              d={pts.map((p, j) => `${j === 0 ? 'M' : 'L'}${X(p.i).toFixed(1)},${Y(p.v).toFixed(1)}`).join(' ')}
            />
            {pts.map((p) => <circle key={p.i} cx={X(p.i)} cy={Y(p.v)} r={1.8} fill={ln.color} />)}
          </g>
        );
      })}
      <g style={{ fontFamily: 'var(--font-mono)', fontSize: 9, fill: 'var(--fg-3)' }}>
        <text x={pad.l - 4} y={pad.t + 8} textAnchor="end">{hi.toFixed(2)}</text>
        <text x={pad.l - 4} y={pad.t + Hi} textAnchor="end">{lo.toFixed(2)}</text>
      </g>
      <g style={{ fontFamily: 'var(--font-mono)', fontSize: 9 }}>
        {lines.map((ln, i) => (
          <g key={ln.key} transform={`translate(${pad.l + i * 92}, ${H - 5})`}>
            <rect x={0} y={-8} width={8} height={3} fill={ln.color} />
            <text x={12} y={-3} style={{ fill: 'var(--fg-3)' }}>{ln.label}</text>
          </g>
        ))}
      </g>
    </svg>
  );
}

// ─── 历代追踪 · Δ vs previous snapshot table. ────────────────────────────────
function HistoryDeltaTable({ series }) {
  const list = (series || []).slice().sort((a, b) => String(a.asOf).localeCompare(String(b.asOf)));
  const cur = list[list.length - 1];
  const prev = list[list.length - 2];
  const fields = [
    ['ICIR(年化)', 'icir_annual', 2],
    ['IC均值', 'ic_mean', 3],
    ['RankIC均值', 'rank_ic_mean', 3],
    ['多空夏普', 'ls_sharpe', 2],
  ];
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
      <SectionHead title="历代追踪 · Δ" meta={cur ? `本代 ${cur.asOf || ''}` : '无历史'} />
      {!cur ? (
        <div style={{ padding: 16 }}><Empty label="无历史快照" /></div>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
          <thead>
            <tr style={{ background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}>
              {['指标', '上一代', '本代', 'Δ'].map((h, i) => (
                <th key={h} style={{ textAlign: i ? 'right' : 'left', padding: '5px 12px', fontSize: 9, color: 'var(--fg-3)', fontWeight: 500, fontFamily: 'var(--font-mono)' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {fields.map(([label, key, dg]) => {
              const c = cur[key] == null ? null : Number(cur[key]);
              const p = prev && prev[key] != null ? Number(prev[key]) : null;
              const delta = c != null && p != null ? c - p : null;
              return (
                <tr key={key} style={{ borderBottom: '1px solid var(--border-faint)', height: 30 }}>
                  <td style={{ padding: '0 12px', color: 'var(--fg-2)' }}>{label}</td>
                  <td style={{ padding: '0 12px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--fg-3)' }}>{p == null ? '—' : p.toFixed(dg)}</td>
                  <td style={{ padding: '0 12px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--fg)' }}>{c == null ? '—' : c.toFixed(dg)}</td>
                  <td style={{ padding: '0 12px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: delta == null ? 'var(--fg-mute)' : delta >= 0 ? 'var(--ok)' : 'var(--err)' }}>
                    {delta == null ? '—' : `${delta >= 0 ? '▲' : '▼'}${Math.abs(delta).toFixed(dg)}`}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ─── 最近一次执行 ─────────────────────────────────────────────────────────────
function LastRun({ d }) {
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
      <SectionHead title="最近一次执行" meta={d.run_id || '—'} />
      <div style={{ padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-2)', flexWrap: 'wrap' }}>
          <span style={{ color: 'var(--fg-3)' }}>run_id</span>
          <span style={{ color: 'var(--fg)' }}>{d.run_id || '—'}</span>
          {d.degradation_level && d.degradation_level !== 'none'
            && <Badge group="degrade" value={degradeValue(d.degradation_level)} sm />}
        </div>
        {d.factor_id && (
          <Link to={`/factors/library/${d.factor_id}`} className="btn ghost sm" style={{ alignSelf: 'flex-start', textDecoration: 'none' }}>
            查看运行日志 →
          </Link>
        )}
      </div>
    </div>
  );
}

// ─── 操作 ─────────────────────────────────────────────────────────────────────
function Actions({ d, id, rerun, exportCsv, onRobustness }) {
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
      <SectionHead title="操作" />
      <div style={{ padding: 14, display: 'flex', flexDirection: 'column', gap: 8 }}>
        <button className="btn sm" style={{ justifyContent: 'flex-start' }} disabled={rerun.isPending} onClick={() => rerun.mutate()}>
          {rerun.isPending ? '提交中…' : '↻ 重跑该条目（增量）'}
        </button>
        <button className="btn sm" style={{ justifyContent: 'flex-start' }} onClick={exportCsv}>⬇ 导出账本切片 CSV</button>
        <button className="btn sm" style={{ justifyContent: 'flex-start' }} onClick={onRobustness}>◇ 稳健性视图（跨口径）</button>
        {rerun.isError && (
          <div style={{ fontSize: 10.5, color: 'var(--err)', fontFamily: 'var(--font-mono)' }}>
            提交失败：{rerun.error?.apiError?.message || String(rerun.error?.message || rerun.error)}
          </div>
        )}
      </div>
    </div>
  );
}
