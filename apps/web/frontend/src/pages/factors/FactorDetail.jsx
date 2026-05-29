// /factors/library/:factorId — single factor detail with 8 tabs.
import React from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { endpoints } from '../../lib/api';
import { TerminalShell, TPanel } from '../../components/shell';
import { Empty, Loading } from '../../components/ui';
import RunConfigDrawer from './RunConfigDrawer';
import { BarChart, ICChart, LineChart } from './charts';
import { catLabel, fmtNum, fmtPct, tone } from './shared';

const TABS = [
  { id: 'overview', l: '概览' },
  { id: 'ic', l: 'IC / IR' },
  { id: 'decay', l: 'IC 衰减' },
  { id: 'layered', l: '分层回测' },
  { id: 'turnover', l: '换手率' },
  { id: 'tracking', l: '持续跟踪' },
  { id: 'version', l: '版本与代码' },
  { id: 'history', l: '历史运行' },
];

export default function FactorDetail() {
  const { factorId } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();

  const [tab, setTab] = React.useState('overview');
  const [runId, setRunId] = React.useState(null);
  const [showRun, setShowRun] = React.useState(false);

  const factorQ = useQuery({
    queryKey: ['factor', factorId],
    queryFn: () => endpoints.factor(factorId),
  });
  const runsQ = useQuery({
    queryKey: ['factor.runs', factorId],
    queryFn: () => endpoints.runs({ factor_id: factorId, limit: 50 }),
    enabled: !!factorId,
  });
  const runs = runsQ.data?.items || [];
  const selectedRunId = runId || runs.find((r) => r.status === 'success')?.run_id || runs[0]?.run_id;

  const factor = factorQ.data;
  const patch = useMutation({
    mutationFn: (body) => endpoints.patchFactor(factorId, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['factor', factorId] }),
  });

  return (
    <TerminalShell
      module="factor" current="library"
      path={['factor_mgmt/', 'library/', factorId]}
      title={factorId}
      subtitle={factor ? `${factor.display_name || factor.base_name} · ${catLabel(factor.category)} · ${factor.direction > 0 ? '正向' : '反向'} · ${factor.source}` : '…'}
      badge={factor && (
        <span className="chip ok" style={{ fontSize: 10 }}>
          ● {factor.status?.toUpperCase()}{factor.tracking_enabled ? ' · 日常跟踪' : ''}
        </span>
      )}
      actions={factor && (
        <>
          <button className="btn ghost sm"
            onClick={() => patch.mutate({ tracking_enabled: !factor.tracking_enabled })}>
            {factor.tracking_enabled ? '关闭跟踪' : '加入跟踪'}
          </button>
          <button className="btn sm" style={{ background: 'var(--fg)', color: 'var(--bg)', borderColor: 'var(--fg)' }}
            onClick={() => setShowRun(true)}>+ 新建运行</button>
        </>
      )}
    >
      {factorQ.isLoading && <Loading label="加载因子…" />}
      {!factorQ.isLoading && !factor && <Empty label="未找到该因子" />}

      {factor && (
        <>
          {/* Header KPI strip */}
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(9, 1fr)',
            background: 'var(--surface)', borderBottom: '1px solid var(--border-strong)', height: 56, flexShrink: 0,
          }}>
            {[
              { l: 'CATEGORY', v: catLabel(factor.category) },
              { l: 'DIRECTION', v: factor.direction > 0 ? '正向 (+)' : '反向 (−)', t: factor.direction < 0 ? 'warn' : 'ok' },
              { l: 'SOURCE', v: factor.source },
              { l: 'CURRENT VER', v: factor.versions?.find((v) => v.enabled)?.version || 'v1' },
              { l: 'DEPENDS ON', v: (factor.dependencies?.[0] || '—'), sub: `${factor.dependencies?.length || 0} 表` },
              { l: 'TRACKING', v: factor.tracking_enabled ? (factor.tracking_universe_id || factor.last_universe_id || '—') : 'off' },
              { l: 'LAST EVAL', v: factor.last_eval_at?.slice(11, 16) || '—', sub: factor.last_eval_at?.slice(0, 10) },
              { l: 'ICIR ANN', v: fmtNum(factor.icir_annual, 2), t: 'ok' },
              { l: 'TAGS', v: (factor.tags || []).map((t) => '#' + t).join(' ') || '—' },
            ].map((c, i) => (
              <div key={i} style={{
                padding: '6px 14px', borderRight: i < 8 ? '1px solid var(--border-faint)' : 'none',
                display: 'flex', flexDirection: 'column', justifyContent: 'space-between', minWidth: 0,
              }}>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{c.l}</span>
                <div style={{ minWidth: 0 }}>
                  <div style={{
                    fontFamily: 'var(--font-mono)', fontSize: 12.5, fontWeight: 500,
                    color: c.t === 'ok' ? 'var(--ok)' : c.t === 'warn' ? 'var(--warn)' : 'var(--fg)',
                    whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                  }}>{c.v}</div>
                  {c.sub && <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', marginTop: 1 }}>{c.sub}</div>}
                </div>
              </div>
            ))}
          </div>

          {/* Run picker */}
          <div style={{
            height: 30, padding: '0 14px', background: 'var(--surface-2)', borderBottom: '1px solid var(--border)',
            display: 'flex', alignItems: 'center', gap: 12, fontFamily: 'var(--font-mono)', fontSize: 11, flexShrink: 0,
          }}>
            <span style={{ fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>评估运行</span>
            <span style={{ color: 'var(--fg-mute)' }}>│</span>
            <select data-testid="factor-detail-runpicker" value={selectedRunId || ''} onChange={(e) => setRunId(e.target.value)}
              style={{ flex: 1, maxWidth: 480, height: 22, background: 'var(--surface)', border: '1px solid var(--border)', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)' }}>
              {runs.length === 0 && <option value="">尚无评估运行</option>}
              {runs.map((r) => (
                <option key={r.run_id} value={r.run_id}>
                  {r.run_id} · {r.created_at?.slice(0, 16)?.replace('T', ' ')} · {r.status}
                  {r.summary?.icir_annual != null ? ` · ICIR ${fmtNum(r.summary.icir_annual, 2)}` : ''}
                </option>
              ))}
            </select>
            <span style={{ color: 'var(--fg-3)' }}>{runs.length} 次评估</span>
          </div>

          {/* Tab bar */}
          <div style={{
            height: 32, padding: '0 14px', background: 'var(--surface-2)', borderBottom: '1px solid var(--border)',
            display: 'flex', alignItems: 'flex-end', flexShrink: 0,
          }}>
            {TABS.map((t) => (
              <button key={t.id} data-testid={`factor-detail-tab-${t.id}`} onClick={() => setTab(t.id)} style={{
                height: 32, padding: '0 14px',
                background: tab === t.id ? 'var(--surface)' : 'transparent',
                border: 'none',
                borderTop: tab === t.id ? '1px solid var(--fg)' : '1px solid transparent',
                borderLeft: tab === t.id ? '1px solid var(--border)' : 'none',
                borderRight: tab === t.id ? '1px solid var(--border)' : 'none',
                fontFamily: 'var(--font-mono)', fontSize: 11,
                color: tab === t.id ? 'var(--fg)' : 'var(--fg-3)',
                cursor: 'pointer', marginBottom: -1,
              }}>{t.l}</button>
            ))}
          </div>

          <div className="scroll" style={{ flex: 1, minHeight: 0 }}>
            {tab === 'overview' && <OverviewTab factor={factor} runId={selectedRunId} />}
            {tab === 'ic' && <ICTab runId={selectedRunId} />}
            {tab === 'decay' && <DecayTab factorId={factorId} runId={selectedRunId} />}
            {tab === 'layered' && <LayeredTab runId={selectedRunId} />}
            {tab === 'turnover' && <TurnoverTab runId={selectedRunId} />}
            {tab === 'tracking' && <TrackingTab factor={factor} onCloseTracking={() => patch.mutate({ tracking_enabled: false })} onRetest={() => setShowRun(true)} />}
            {tab === 'version' && <VersionTab factor={factor} />}
            {tab === 'history' && <HistoryTab runs={runs} onSelect={(rid) => { setRunId(rid); setTab('overview'); }} />}
          </div>
        </>
      )}

      {showRun && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.18)', zIndex: 30, display: 'flex' }}>
          <div style={{ flex: 1 }} onClick={() => setShowRun(false)} />
          <RunConfigDrawer factorId={factorId} onClose={() => setShowRun(false)}
            onSubmitted={() => { setShowRun(false); qc.invalidateQueries({ queryKey: ['factor.runs', factorId] }); nav('/factors/tasks'); }} />
        </div>
      )}
    </TerminalShell>
  );
}

// ─── Overview ────────────────────────────────────────────────────────────
function OverviewTab({ factor, runId }) {
  const runQ = useQuery({
    queryKey: ['run', runId],
    queryFn: () => endpoints.run(runId),
    enabled: !!runId,
  });
  const r = runQ.data;
  const s = r?.summary || {};
  const cfg = r?.run_config || {};
  const kpis = [
    { l: 'RANK IC', v: fmtNum(s.rank_ic_mean, 3), sub: '均值', t: tone(s.rank_ic_mean, 'ic') },
    { l: 'ICIR', v: fmtNum(s.icir, 2), sub: '原始' },
    { l: 'ICIR ANN', v: fmtNum(s.icir_annual, 2), sub: '√n', t: tone(s.icir_annual, 'icir') },
    { l: 'IC T-STAT', v: fmtNum(s.ic_t_stat, 2), sub: s.ic_t_stat > 2 ? 'p<0.05' : '' },
    { l: 'IC WIN%', v: fmtPct(s.ic_win_rate, 1) },
    { l: 'L/S ANN', v: fmtPct(s.ls_annual_return, 1), t: 'ok' },
    { l: 'TURN', v: fmtPct(s.turnover, 0), t: tone(s.turnover, 'turn') },
    { l: 'COVERAGE', v: s.coverage ? Math.round(s.coverage) : '—', t: 'ok' },
  ];
  return (
    <div style={{ display: 'flex', height: '100%' }}>
      <div style={{ flex: 1.6, minWidth: 0, borderRight: '1px solid var(--border)', display: 'flex', flexDirection: 'column' }}>
        <TPanel title="KEY METRICS · 本次评估" meta={runId || ''} last>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)' }}>
            {kpis.map((c, i) => (
              <div key={i} style={{
                padding: '14px 16px',
                borderRight: i % 4 < 3 ? '1px solid var(--border-faint)' : 'none',
                borderBottom: i < 4 ? '1px solid var(--border-faint)' : 'none',
              }}>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', textTransform: 'uppercase' }}>{c.l}</div>
                <div style={{
                  fontFamily: 'var(--font-mono)', fontSize: 26, fontWeight: 500,
                  color: c.t === 'ok' ? 'var(--ok)' : c.t === 'warn' ? 'var(--warn)' : 'var(--fg)',
                  fontVariantNumeric: 'tabular-nums', lineHeight: 1, marginTop: 4,
                }}>{c.v}</div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', marginTop: 4 }}>{c.sub}</div>
              </div>
            ))}
          </div>
        </TPanel>
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <TPanel title="RUN CONFIGURATION" meta="evaluation parameters" last>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
            <tbody>
              {[
                ['股票池', cfg.universe_id || '—'],
                ['评估区间', `${cfg.start || '—'} → ${cfg.end || '—'}`],
                ['调仓频率', cfg.rebalance || 'W'],
                ['前向收益周期', (cfg.horizons || []).join(', ')],
                ['主周期', `${cfg.primary_horizon || 5}D`],
                ['IC 类型', cfg.ic_type || 'rank'],
                ['收益口径', cfg.return_price || 'close'],
                ['去极值', cfg.winsorize === 'none' ? '关' : `${cfg.winsorize} · 阈值 ${cfg.winsorize_n}`],
                ['标准化', cfg.standardize === 'zscore' ? '开' : '关'],
                ['市值中性化', cfg.neutralize_size ? `开 (${cfg.size_field})` : '关'],
                ['行业中性化', cfg.neutralize_industry ? `开 (${cfg.classification_system}/${cfg.industry_level}级)` : '关'],
                ['分层数', cfg.quantiles || 5],
                ['组内加权', cfg.layer_weight || 'equal'],
                ['任务车道', <span style={{ color: cfg.lane === 'interactive' ? 'var(--info)' : 'var(--fg-3)' }}>{cfg.lane || 'interactive'}</span>],
                ['提交人', r?.initiated_by || '—'],
                ['运行耗时', r?.duration_sec ? `${Math.round(r.duration_sec)}s` : '—'],
              ].map(([k, v], i) => (
                <tr key={i} style={{ borderBottom: '1px solid var(--border-faint)' }}>
                  <td style={{ padding: '6px 14px', color: 'var(--fg-3)', width: 130 }}>{k}</td>
                  <td style={{ padding: '6px 14px', color: 'var(--fg)' }}>{v}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TPanel>
      </div>
    </div>
  );
}

// ─── IC tab ──────────────────────────────────────────────────────────────
function ICTab({ runId }) {
  const q = useQuery({
    queryKey: ['ic', runId],
    queryFn: () => endpoints.runIcSeries(runId),
    enabled: !!runId,
  });
  const items = q.data?.items || [];
  const chartData = items.map((it, i) => ({
    label: it.date?.slice(2, 10) || `T${i}`,
    ic: it.rank_ic ?? 0,
  }));
  return (
    <TPanel title="IC SERIES · 每期 RANK IC + 累计 IC" meta={`${items.length} 个调仓期`} last>
      <div style={{ padding: '16px 24px' }}>
        {items.length === 0 ? <Empty label="此运行无 IC 数据" /> : <ICChart data={chartData} width={1280} height={360} />}
      </div>
    </TPanel>
  );
}

// ─── Decay tab ───────────────────────────────────────────────────────────
function DecayTab({ factorId }) {
  const q = useQuery({
    queryKey: ['factor.ic', factorId],
    queryFn: () => endpoints.factorIc(factorId),
  });
  const items = q.data?.items || [];
  return (
    <div style={{ display: 'flex', height: '100%' }}>
      <div style={{ flex: 1.4, minWidth: 0, borderRight: '1px solid var(--border)' }}>
        <TPanel title="IC DECAY · 不同前向窗口下的 RANK IC" meta="信号在多少天后衰减" last>
          <div style={{ padding: '24px 28px' }}>
            <BarChart data={items.map((it) => ({ l: `${it.horizon}D`, v: it.rank_ic_mean ?? 0 }))} width={820} height={340} />
          </div>
        </TPanel>
      </div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <TPanel title="DECAY DETAIL" meta="逐周期表" last>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
            <thead><tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
              {['window', 'ic', 'icir', 'win%', 't-stat'].map((c) => (
                <th key={c} style={{ textAlign: c === 'window' ? 'left' : 'right', padding: '8px 14px', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase' }}>{c}</th>
              ))}
            </tr></thead>
            <tbody>
              {items.map((it, i) => (
                <tr key={i} style={{ borderBottom: '1px solid var(--border-faint)', height: 28 }}>
                  <td style={{ padding: '0 14px', fontWeight: 500, color: 'var(--fg)' }}>{it.horizon}D</td>
                  <td style={{ padding: '0 14px', textAlign: 'right', color: 'var(--fg)' }}>{fmtNum(it.rank_ic_mean, 4)}</td>
                  <td style={{ padding: '0 14px', textAlign: 'right', color: 'var(--fg-2)' }}>{fmtNum(it.icir, 2)}</td>
                  <td style={{ padding: '0 14px', textAlign: 'right', color: 'var(--fg-2)' }}>{fmtPct(it.ic_win_rate, 1)}</td>
                  <td style={{ padding: '0 14px', textAlign: 'right', color: 'var(--fg-2)' }}>{fmtNum(it.ic_t_stat, 2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TPanel>
      </div>
    </div>
  );
}

// ─── Layered ─────────────────────────────────────────────────────────────
function LayeredTab({ runId }) {
  const q = useQuery({
    queryKey: ['layers', runId],
    queryFn: () => endpoints.runLayers(runId),
    enabled: !!runId,
  });
  const rows = q.data?.items || [];
  // Group by quantile -> cumulative ret series
  const byQ = {};
  rows.forEach((r) => {
    byQ[r.quantile] = byQ[r.quantile] || [];
    byQ[r.quantile].push(r);
  });
  const series = Object.keys(byQ).sort((a, b) => Number(a) - Number(b)).map((k) => {
    let cum = 0;
    const data = byQ[k].sort((a, b) => (a.date < b.date ? -1 : 1)).map((r) => { cum += r.ret; return cum; });
    return { label: `Q${k}`, data, bold: Number(k) === 1 || Number(k) === Object.keys(byQ).length };
  });
  const runQ = useQuery({
    queryKey: ['run', runId], queryFn: () => endpoints.run(runId), enabled: !!runId,
  });
  const s = runQ.data?.summary || {};
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <TPanel title="LAYERED BACKTEST · N 分组累计收益" meta="每组等权" noBorderBottom last>
        <div style={{ padding: '16px 24px' }}>
          {series.length ? <LineChart series={series} width={1280} height={380} /> : <Empty label="此运行无分层数据" />}
        </div>
      </TPanel>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', borderTop: '1px solid var(--border)' }}>
        {[
          { l: 'L/S 年化', v: fmtPct(s.ls_annual_return, 1), t: 'ok' },
          { l: 'L/S 夏普', v: fmtNum(s.ls_sharpe, 2), t: 'ok' },
          { l: 'L/S 最大回撤', v: fmtPct(s.ls_max_drawdown, 1) },
          { l: '多头年化', v: fmtPct(s.long_annual_return, 1), t: 'ok' },
          { l: '空头年化', v: fmtPct(s.short_annual_return, 1) },
        ].map((c, i) => (
          <div key={i} style={{ padding: '14px 18px', borderRight: i < 4 ? '1px solid var(--border-faint)' : 'none' }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', textTransform: 'uppercase' }}>{c.l}</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 20, fontWeight: 500, color: c.t === 'ok' ? 'var(--ok)' : 'var(--fg)', marginTop: 3 }}>{c.v}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Turnover ────────────────────────────────────────────────────────────
function TurnoverTab({ runId }) {
  const q = useQuery({
    queryKey: ['turnover', runId],
    queryFn: () => endpoints.runTurnover(runId),
    enabled: !!runId,
  });
  const data = q.data?.data || {};
  const series = (data.series || []).map((s) => s.turnover ?? 0);
  return (
    <TPanel title="TURNOVER · 头部组合相邻调仓换手率" meta={`${series.length} 期`} last>
      <div style={{ padding: '16px 24px' }}>
        {series.length ? (
          <LineChart series={[{ label: 'turnover', data: series, bold: true, color: 'var(--warn)' }]} width={1280} height={380} />
        ) : <Empty label="无换手率数据 (是否在配置中关闭了 compute_turnover?)" />}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', marginTop: 18, border: '1px solid var(--border)', borderRadius: 3 }}>
          {[
            ['均值', fmtPct(data.mean, 1)],
            ['含成本年化', fmtPct(-(data.mean_after_cost_ann || 0), 1) + ' (估)'],
            ['期数', series.length],
          ].map(([k, v], i) => (
            <div key={i} style={{ padding: '12px 14px', borderRight: i < 2 ? '1px solid var(--border-faint)' : 'none' }}>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', textTransform: 'uppercase' }}>{k}</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 17, fontWeight: 500, color: 'var(--fg)', marginTop: 3 }}>{v}</div>
            </div>
          ))}
        </div>
      </div>
    </TPanel>
  );
}

// ─── Tracking ────────────────────────────────────────────────────────────
function TrackingTab({ factor, onCloseTracking, onRetest }) {
  const q = useQuery({
    queryKey: ['tracking', factor.factor_id],
    queryFn: () => endpoints.factorTracking(factor.factor_id),
  });
  const items = q.data?.items || [];
  const series = items.map((it) => it.icir_annual ?? 0);
  const xLabels = items.map((it) => (it.as_of || '').slice(0, 10));
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {q.data?.decaying && (
        <div data-testid="factor-detail-decay-banner" style={{
          padding: '10px 18px', background: 'var(--warn-bg)', borderBottom: '1px solid var(--border)',
          display: 'flex', alignItems: 'center', gap: 12, fontSize: 11.5, color: 'var(--fg-2)',
        }}>
          <span className="chip warn">⚠ 衰减监控</span>
          <span>近期评估 ICIR 持续下降,建议关注。</span>
          <div style={{ flex: 1 }} />
          {factor.tracking_enabled && <button className="btn ghost sm" style={{ fontSize: 10 }} onClick={onCloseTracking}>关闭跟踪</button>}
          <button className="btn ghost sm" data-testid="factor-detail-retest" style={{ fontSize: 10 }} onClick={onRetest}>立刻重测</button>
        </div>
      )}
      <TPanel title="TRACKING · 历次评估 ICIR 时间序列" meta={`${items.length} 次评估`} last>
        <div style={{ padding: '16px 24px' }}>
          {series.length ? (
            <LineChart series={[
              { label: 'ICIR 年化', data: series, xLabels, bold: true, color: 'var(--info)' },
              { label: '基准 1.5', data: series.map(() => 1.5), color: 'var(--fg-3)', dim: true },
              { label: '基准 2.0', data: series.map(() => 2.0), color: 'var(--ok)', dim: true },
            ]} width={1280} height={380} />
          ) : <Empty label="尚无跟踪数据 — 提交一次评估后这里会持续生长" />}
        </div>
        {items.length > 0 && (
          <div style={{ borderTop: '1px solid var(--border-faint)', background: 'var(--surface-2)',
            display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)' }}>
            {[
              ['首次评估', items[0].as_of?.slice(0, 10) || '—'],
              ['评估次数', items.length],
              ['当前 / 首次', `${fmtNum(items[items.length - 1].icir_annual, 2)} / ${fmtNum(items[0].icir_annual, 2)}`],
              ['是否衰减', q.data?.decaying ? '是' : '否', q.data?.decaying ? 'warn' : 'ok'],
              ['最近评估', items[items.length - 1].as_of?.slice(0, 10)],
            ].map(([k, v, t], i) => (
              <div key={i} style={{ padding: '10px 14px', borderRight: i < 4 ? '1px solid var(--border-faint)' : 'none' }}>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase' }}>{k}</div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 500,
                  color: t === 'warn' ? 'var(--warn)' : t === 'ok' ? 'var(--ok)' : 'var(--fg)', marginTop: 2 }}>{v}</div>
              </div>
            ))}
          </div>
        )}
      </TPanel>
    </div>
  );
}

// ─── Version + code ─────────────────────────────────────────────────────
function VersionTab({ factor }) {
  const versions = factor.versions || [];
  const [pickedVer, setPickedVer] = React.useState(versions.find((v) => v.enabled)?.version);
  const codeQ = useQuery({
    queryKey: ['imports.code', factor.factor_id, pickedVer],
    queryFn: () => endpoints.importCode(factor.factor_id, pickedVer),
    enabled: !!pickedVer,
  });
  return (
    <div style={{ display: 'flex', height: '100%' }}>
      <div style={{ flex: 1, minWidth: 0, borderRight: '1px solid var(--border)' }}>
        <TPanel title="VERSIONS · 因子版本列表" meta={`${versions.length} 个 · ${versions.filter((v) => v.enabled).length} 启用`} last>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
            <thead><tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
              {['version', 'status', 'requires_pit', 'created', 'sha256'].map((c) => (
                <th key={c} style={{ textAlign: 'left', padding: '7px 12px', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase' }}>{c}</th>
              ))}
            </tr></thead>
            <tbody>
              {versions.map((v) => (
                <tr key={v.version} onClick={() => setPickedVer(v.version)} style={{
                  borderBottom: '1px solid var(--border-faint)', height: 30,
                  background: v.version === pickedVer ? 'var(--info-bg)' : v.enabled ? 'var(--ok-bg)' : 'transparent',
                  cursor: 'pointer',
                }}>
                  <td style={{ padding: '0 12px', fontWeight: 500, color: 'var(--fg)' }}>{v.version}</td>
                  <td style={{ padding: '0 12px' }}>{v.enabled ? <span style={{ color: 'var(--ok)' }}>● 启用</span> : <span style={{ color: 'var(--fg-3)' }}>○ 未启用</span>}</td>
                  <td style={{ padding: '0 12px', color: 'var(--fg-2)' }}>{v.requires_pit ? '是' : '否'}</td>
                  <td style={{ padding: '0 12px', color: 'var(--fg-3)' }}>{v.created_at?.slice(0, 16)?.replace('T', ' ')}</td>
                  <td style={{ padding: '0 12px', color: 'var(--fg-3)', fontSize: 10 }}>{v.code_sha256?.slice(0, 12)}…</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TPanel>
      </div>
      <div style={{ flex: 1.4, minWidth: 0 }}>
        <TPanel title={`CODE · ${factor.factor_id} @ ${pickedVer || '—'}`} meta="只读" last>
          {codeQ.isLoading && <Loading label="读取代码…" />}
          {codeQ.data && (
            <pre data-testid="factor-detail-code" style={{
              margin: 0, padding: '14px 18px',
              fontFamily: 'var(--font-mono)', fontSize: 11.5, lineHeight: 1.55,
              color: 'var(--fg-2)', whiteSpace: 'pre', overflow: 'auto',
            }}>{codeQ.data.source}</pre>
          )}
        </TPanel>
      </div>
    </div>
  );
}

// ─── History runs ───────────────────────────────────────────────────────
function HistoryTab({ runs, onSelect }) {
  return (
    <TPanel title="EVALUATION RUNS · 历次评估" meta={`${runs.length} 次`} last>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
        <thead style={{ position: 'sticky', top: 0, background: 'var(--surface)', zIndex: 1 }}>
          <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
            {['run_id', 'started', 'duration', 'pool', 'status', 'icir_ann', 'l/s ann', 'lane', 'user'].map((c, i) => (
              <th key={i} style={{ textAlign: 'left', padding: '8px 12px', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase' }}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.run_id} onClick={() => onSelect(r.run_id)} style={{ borderBottom: '1px solid var(--border-faint)', height: 28, cursor: 'pointer' }}>
              <td style={{ padding: '0 12px', fontWeight: 500, color: 'var(--fg)' }}>{r.run_id}</td>
              <td style={{ padding: '0 12px', color: 'var(--fg-2)' }}>{r.created_at?.slice(0, 16)?.replace('T', ' ')}</td>
              <td style={{ padding: '0 12px', color: 'var(--fg-2)' }}>{r.duration_sec ? `${Math.round(r.duration_sec)}s` : '—'}</td>
              <td style={{ padding: '0 12px', color: 'var(--fg-2)' }}>{r.run_config?.universe_id || '—'}</td>
              <td style={{ padding: '0 12px' }}>
                <span style={{ color: r.status === 'success' ? 'var(--ok)' : r.status === 'failed' ? 'var(--err)' : 'var(--info)' }}>
                  {r.status === 'success' ? '● ok' : r.status === 'failed' ? '✕ failed' : r.status}
                </span>
              </td>
              <td style={{ padding: '0 12px', textAlign: 'right', color: 'var(--ok)' }}>{fmtNum(r.summary?.icir_annual, 2)}</td>
              <td style={{ padding: '0 12px', textAlign: 'right', color: 'var(--ok)' }}>{fmtPct(r.summary?.ls_annual_return, 1)}</td>
              <td style={{ padding: '0 12px', color: r.run_config?.lane === 'interactive' ? 'var(--info)' : 'var(--fg-3)' }}>{r.run_config?.lane || '—'}</td>
              <td style={{ padding: '0 12px', color: 'var(--fg-3)' }}>{r.initiated_by}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TPanel>
  );
}
