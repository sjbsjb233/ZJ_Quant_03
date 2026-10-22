// /factors/library — paginated factor list with KPI strip + filters +
// inline IC trend + per-row actions.
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { endpoints } from '../../lib/api';
import { TerminalShell, FilterBar, FInput, FSelect, FChip, KpiStrip } from '../../components/shell';
import { Empty, ErrorState, Loading } from '../../components/ui';
import { MiniBars } from './charts';
import { FACTOR_CATEGORIES, catLabel, fmtNum, fmtPct, tone } from './shared';
import RunConfigDrawer from './RunConfigDrawer';

export default function FactorLibrary() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [q, setQ] = React.useState('');
  const [category, setCategory] = React.useState('');
  const [status, setStatus] = React.useState('');
  const [source, setSource] = React.useState('');
  const [direction, setDirection] = React.useState('');
  const [trackingOnly, setTrackingOnly] = React.useState(false);
  const [sort, setSort] = React.useState('icir_annual_desc');
  const [page, setPage] = React.useState(1);
  const [pageSize] = React.useState(25);
  const [selected, setSelected] = React.useState(new Set());
  const [runFor, setRunFor] = React.useState(null);

  const qry = useQuery({
    queryKey: ['factors.list', { q, category, status, source, direction, trackingOnly, sort, page, pageSize }],
    queryFn: () => endpoints.factors({
      q: q || undefined,
      category: category || undefined,
      status: status || undefined,
      source: source || undefined,
      direction: direction || undefined,
      tracking_only: trackingOnly || undefined,
      sort,
      page,
      page_size: pageSize,
    }),
    placeholderData: keepPreviousData,
  });
  const data = qry.data;

  const items = data?.items || [];
  const total = data?.total || 0;
  const kpi = data?.kpi || {};

  const patch = useMutation({
    mutationFn: ({ factorId, body }) => endpoints.patchFactor(factorId, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['factors.list'] }),
  });

  const toggleSel = (id) => {
    const n = new Set(selected);
    n.has(id) ? n.delete(id) : n.add(id);
    setSelected(n);
  };

  const exportCsv = async () => {
    const url = `/api/v1/factors?format=csv&page_size=10000` +
      (q ? `&q=${encodeURIComponent(q)}` : '') +
      (category ? `&category=${category}` : '') +
      (status ? `&status=${status}` : '');
    window.open(url, '_blank', 'noopener');
  };

  return (
    <TerminalShell
      module="factor" current="library"
      path={['factor_mgmt/', 'library']}
      title="因子库"
      subtitle={`factors · 共 ${total} · 显示 ${items.length} · sort=${sort}`}
      actions={
        <>
          <button className="btn ghost sm" onClick={exportCsv}>导出 CSV</button>
          <button className="btn ghost sm" onClick={() => nav('/factors/import')}>去导入因子 →</button>
          <button className="btn sm" data-testid="factor-lib-newrun"
            style={{ background: 'var(--fg)', color: 'var(--bg)', borderColor: 'var(--fg)' }}
            onClick={() => setRunFor(items[0]?.factor_id || 'mom')}>+ 新建运行</button>
        </>
      }
      filterBar={
        <FilterBar applied={[q, category, status, source, direction, trackingOnly].filter(Boolean)}
          onClear={() => { setQ(''); setCategory(''); setStatus(''); setSource(''); setDirection(''); setTrackingOnly(false); }}>
          <FInput label="search" placeholder="因子名 / 显示名…" value={q} onChange={setQ} width={180} />
          <FSelect label="category" testId="factor-lib-filter-cat" value={category} onChange={setCategory} width={120} options={[
            { value: '', label: '全部' },
            ...FACTOR_CATEGORIES.map((c) => ({ value: c.id, label: c.l })),
          ]} />
          <FSelect label="status" testId="factor-lib-filter-status" value={status} onChange={setStatus} width={110} options={[
            { value: '', label: '全部' },
            { value: 'enabled', label: '启用' },
            { value: 'disabled', label: '停用' },
            { value: 'archived', label: '归档' },
          ]} />
          <FSelect label="source" testId="factor-lib-filter-src" value={source} onChange={setSource} width={100} options={[
            { value: '', label: '全部' },
            { value: 'builtin', label: 'builtin' },
            { value: 'upload', label: 'upload' },
            { value: 'inbox', label: 'inbox' },
          ]} />
          <FSelect label="direction" value={direction} onChange={setDirection} width={80} options={[
            { value: '', label: '全部' },
            { value: '+', label: '+ 正' },
            { value: '-', label: '− 反' },
          ]} />
          <FSelect label="sort" value={sort} onChange={setSort} width={150} options={[
            { value: 'icir_annual_desc', label: 'ICIR 年化 ↓' },
            { value: 'icir_desc', label: 'ICIR ↓' },
            { value: 'rank_ic_desc', label: 'Rank IC ↓' },
            { value: 'updated_desc', label: '更新时间 ↓' },
          ]} />
          {trackingOnly && <FChip tone="info" onX={() => setTrackingOnly(false)}>仅日常跟踪</FChip>}
          {!trackingOnly && (
            <span onClick={() => setTrackingOnly(true)} style={{ color: 'var(--info)', cursor: 'pointer', fontSize: 10 }}>+ 仅日常跟踪</span>
          )}
        </FilterBar>
      }
    >
      <div data-testid="factor-lib-kpi">
      <KpiStrip cells={[
        { label: 'TOTAL', value: kpi.total ?? 0, sub: '已加载', testId: 'factor-lib-kpi-total' },
        { label: 'ENABLED', value: kpi.enabled ?? 0, tone: 'ok', testId: 'factor-lib-kpi-enabled' },
        { label: 'DISABLED', value: kpi.disabled ?? 0, sub: '低分', tone: 'neutral', testId: 'factor-lib-kpi-disabled' },
        { label: 'ARCHIVED', value: kpi.archived ?? 0, sub: '历史', tone: 'neutral', testId: 'factor-lib-kpi-archived' },
        { label: 'DAILY TRACK', value: kpi.tracked ?? 0, sub: 'auto re-eval', tone: 'info', testId: 'factor-lib-kpi-tracked' },
        { label: 'TOP ICIR', value: fmtNum(kpi.top_icir, 2), sub: kpi.top_icir_id || '—', tone: 'ok', testId: 'factor-lib-kpi-topicir' },
        { label: 'DECAYING', value: kpi.decaying ?? 0, sub: '7d ↓ > 30%', tone: 'warn', testId: 'factor-lib-kpi-decaying' },
        { label: 'NEVER EVAL', value: kpi.never_eval ?? 0, sub: '待评估', tone: 'warn', testId: 'factor-lib-kpi-nevereval' },
      ]} />
      </div>

      {selected.size > 0 && (
        <div data-testid="factor-lib-batchbar">
        <BatchActionBar count={selected.size} onClear={() => setSelected(new Set())}
          onEnable={() => Promise.all([...selected].map((id) => endpoints.patchFactor(id, { status: 'enabled' }))).then(() => { setSelected(new Set()); qc.invalidateQueries({ queryKey: ['factors.list'] }); })}
          onDisable={() => Promise.all([...selected].map((id) => endpoints.patchFactor(id, { status: 'disabled' }))).then(() => { setSelected(new Set()); qc.invalidateQueries({ queryKey: ['factors.list'] }); })}
          onArchive={() => Promise.all([...selected].map((id) => endpoints.archiveFactor(id))).then(() => { setSelected(new Set()); qc.invalidateQueries({ queryKey: ['factors.list'] }); })}
          onTrack={() => { setSelected(new Set()); nav('/factors/baskets/new'); }}
        />
        </div>
      )}

      <div className="scroll" style={{ flex: 1, minHeight: 0 }} data-testid="factor-lib-table">
        {qry.isLoading && <Loading label="加载因子列表…" />}
        {!qry.isLoading && qry.isError && (
          <ErrorState error={qry.error} onRetry={() => qry.refetch()} />
        )}
        {!qry.isLoading && !qry.isError && items.length === 0 && (
          <Empty label="还没有因子" hint="去『因子导入』或先启用内置因子" />
        )}
        {!qry.isLoading && !qry.isError && items.length > 0 && (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
            <thead style={{ position: 'sticky', top: 0, background: 'var(--surface)', zIndex: 1 }}>
              <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
                {[
                  { l: '', a: 'center', w: 30 },
                  { l: 'factor_id / 显示名', a: 'left' },
                  { l: 'cat', a: 'left' },
                  { l: 'src', a: 'left' },
                  { l: 'dir', a: 'center' },
                  { l: 'status', a: 'left' },
                  { l: 'rank_ic', a: 'right' },
                  { l: 'icir', a: 'right' },
                  { l: 'icir_ann', a: 'right' },
                  { l: 'win%', a: 'right' },
                  { l: 'l/s ann', a: 'right' },
                  { l: 'turn', a: 'right' },
                  { l: 'ic trend', a: 'left' },
                  { l: 'track', a: 'left' },
                  { l: 'updated', a: 'left' },
                  { l: '', a: 'right' },
                ].map((c, i) => (
                  <th key={i} style={{
                    textAlign: c.a, padding: '7px 10px', fontSize: 9.5,
                    color: 'var(--fg-3)', fontWeight: 500, fontFamily: 'var(--font-mono)',
                    textTransform: 'uppercase', letterSpacing: '0.06em', width: c.w,
                    whiteSpace: 'nowrap',
                  }}>{c.l}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((f, i) => (
                <tr key={f.factor_id} data-testid={`factor-lib-row-${f.factor_id}`} onClick={() => nav(`/factors/library/${f.factor_id}`)} style={{
                  borderBottom: '1px solid var(--border-faint)', height: 28,
                  background: selected.has(f.factor_id) ? 'var(--info-bg)' : (i % 2 ? 'rgba(0,0,0,0.012)' : 'transparent'),
                  opacity: f.status === 'archived' ? 0.55 : 1,
                  cursor: 'pointer',
                }}>
                  <td onClick={(e) => { e.stopPropagation(); toggleSel(f.factor_id); }} style={{ padding: '0 6px', textAlign: 'center' }}>
                    <input type="checkbox" checked={selected.has(f.factor_id)} onChange={() => toggleSel(f.factor_id)} style={{ accentColor: 'var(--fg)' }} />
                  </td>
                  <td style={{ padding: '0 10px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 500, color: 'var(--fg)' }}>{f.factor_id}</span>
                      <span style={{ color: 'var(--fg-3)', fontSize: 10.5 }}>· {f.display_name}</span>
                      {f.tags?.includes('核心') && <span className="chip ok" style={{ height: 14, fontSize: 9, padding: '0 4px' }}>核心</span>}
                      {f.never_eval && <span className="chip warn" style={{ height: 14, fontSize: 9, padding: '0 4px' }}>未评估</span>}
                      {f.decaying && <span className="chip warn" style={{ height: 14, fontSize: 9, padding: '0 4px' }}>衰减</span>}
                      {f.degradation_level === 'severe' && (
                        <span className="chip" data-testid="factor-lib-degraded" title="最近评估含严重降级,结果可能不可用于决策"
                          style={{ height: 14, fontSize: 9, padding: '0 4px', color: 'var(--err)', background: 'var(--err-bg)' }}>降级(严重)</span>
                      )}
                      {f.degradation_level === 'partial' && (
                        <span className="chip warn" data-testid="factor-lib-degraded" title="最近评估存在降级项"
                          style={{ height: 14, fontSize: 9, padding: '0 4px' }}>降级</span>
                      )}
                    </div>
                  </td>
                  <td style={{ padding: '0 10px', fontFamily: 'var(--font-mono)', color: 'var(--fg-2)', fontSize: 10.5 }}>{catLabel(f.category)}</td>
                  <td style={{ padding: '0 10px', fontFamily: 'var(--font-mono)', color: 'var(--fg-3)', fontSize: 10.5 }}>{f.source}</td>
                  <td style={{ padding: '0 10px', textAlign: 'center', fontFamily: 'var(--font-mono)', fontWeight: 500, color: f.direction > 0 ? 'var(--ok)' : 'var(--err)' }}>{f.direction > 0 ? '+' : '−'}</td>
                  <td style={{ padding: '0 10px', fontFamily: 'var(--font-mono)' }}>
                    <span style={{ color: f.status === 'enabled' ? 'var(--ok)' : 'var(--fg-3)', marginRight: 4 }}>
                      {f.status === 'enabled' ? '●' : f.status === 'disabled' ? '◌' : '⊘'}
                    </span>
                    <span style={{ color: f.status === 'enabled' ? 'var(--ok)' : 'var(--fg-3)', fontSize: 10, textTransform: 'uppercase' }}>
                      {f.status === 'enabled' ? 'on' : f.status === 'disabled' ? 'off' : 'arch'}
                    </span>
                  </td>
                  {[
                    { v: f.rank_ic_mean, fmt: (x) => fmtNum(x, 3), kind: 'ic' },
                    { v: f.icir, fmt: (x) => fmtNum(x, 2) },
                    { v: f.icir_annual, fmt: (x) => fmtNum(x, 2), kind: 'icir' },
                    { v: f.ic_win_rate, fmt: (x) => fmtPct(x, 1) },
                    { v: f.ls_annual_return, fmt: (x) => fmtPct(x, 1) },
                    { v: f.turnover, fmt: (x) => fmtPct(x, 0), kind: 'turn' },
                  ].map((m, mi) => {
                    const t = m.kind ? tone(m.v, m.kind) : null;
                    return (
                      <td key={mi} style={{
                        padding: '0 10px', textAlign: 'right', fontFamily: 'var(--font-mono)',
                        fontVariantNumeric: 'tabular-nums',
                        color: t === 'ok' ? 'var(--ok)' : t === 'warn' ? 'var(--warn)' : (m.v == null ? 'var(--fg-mute)' : 'var(--fg)'),
                      }}>{m.v == null ? '—' : m.fmt(m.v)}</td>
                    );
                  })}
                  <td style={{ padding: '0 10px' }}>
                    {f.ic_spark?.length ? <MiniBars data={f.ic_spark} width={70} height={16} /> : <span style={{ color: 'var(--fg-mute)' }}>—</span>}
                  </td>
                  <td style={{ padding: '0 10px', fontFamily: 'var(--font-mono)', fontSize: 10 }}>
                    {f.tracking_enabled ? (
                      <span style={{ color: 'var(--ok)' }}>● 跟踪 · {f.tracking_universe_id || f.last_universe_id || '—'}</span>
                    ) : <span style={{ color: 'var(--fg-4)' }}>○ off</span>}
                  </td>
                  <td style={{ padding: '0 10px', fontFamily: 'var(--font-mono)', color: 'var(--fg-3)', fontSize: 10.5 }}>
                    {f.updated_at?.slice(0, 16)?.replace('T', ' ')}
                  </td>
                  <td onClick={(e) => e.stopPropagation()} style={{ padding: '0 10px', textAlign: 'right', fontFamily: 'var(--font-mono)', fontSize: 10 }}>
                    <span data-testid={`factor-lib-row-${f.factor_id}-action-toggle`}
                      onClick={() => patch.mutate({ factorId: f.factor_id, body: { status: f.status === 'enabled' ? 'disabled' : 'enabled' } })}
                      style={{ marginRight: 8, cursor: 'pointer', color: 'var(--fg-3)' }}>
                      {f.status === 'enabled' ? '停用' : '启用'}
                    </span>
                    <span data-testid={`factor-lib-row-${f.factor_id}-action-eval`}
                      onClick={() => setRunFor(f.factor_id)} style={{ marginRight: 8, cursor: 'pointer', color: 'var(--info)' }}>评估 →</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <Pagination total={total} page={page} pageSize={pageSize} onPage={setPage} />

      {runFor && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.18)', zIndex: 30, display: 'flex' }}>
          <div style={{ flex: 1 }} onClick={() => setRunFor(null)} />
          <RunConfigDrawer factorId={runFor} onClose={() => setRunFor(null)} onSubmitted={() => nav('/factors/tasks')} />
        </div>
      )}
    </TerminalShell>
  );
}

function BatchActionBar({ count, onClear, onEnable, onDisable, onTrack, onArchive }) {
  return (
    <div style={{
      height: 28, padding: '0 14px', background: 'var(--info-bg)', borderBottom: '1px solid var(--border)',
      display: 'flex', alignItems: 'center', gap: 12, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-2)', flexShrink: 0,
    }}>
      <span style={{ color: 'var(--info)' }}>● 已选 {count} 个因子</span>
      <span style={{ color: 'var(--fg-mute)' }}>│</span>
      <a onClick={onEnable} style={{ cursor: 'pointer' }}>批量启用</a>
      <a onClick={onDisable} style={{ cursor: 'pointer' }}>批量停用</a>
      <a onClick={onTrack} style={{ cursor: 'pointer', color: 'var(--ok)' }}>加入篮子</a>
      <a onClick={onArchive} style={{ color: 'var(--err)', cursor: 'pointer' }}>归档</a>
      <div style={{ flex: 1 }} />
      <a onClick={onClear} style={{ color: 'var(--fg-3)', cursor: 'pointer' }}>取消选择</a>
    </div>
  );
}

function Pagination({ total, page, pageSize, onPage }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const showRange = `${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)}`;
  return (
    <div style={{
      height: 30, padding: '0 14px', borderTop: '1px solid var(--border)',
      background: 'var(--surface-2)', display: 'flex', alignItems: 'center', gap: 12,
      fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', flexShrink: 0,
    }}>
      <span>showing <span style={{ color: 'var(--fg)' }}>{showRange}</span> of <span style={{ color: 'var(--fg)' }}>{total}</span></span>
      <span style={{ color: 'var(--fg-mute)' }}>│</span>
      <span>page size: <span style={{ color: 'var(--fg)' }}>{pageSize}</span></span>
      <div style={{ flex: 1 }} />
      <span className="kbd" onClick={() => onPage(Math.max(1, page - 1))} style={{ cursor: 'pointer' }}>←</span>
      {Array.from({ length: Math.min(pages, 8) }, (_, i) => i + 1).map((p) => (
        <span key={p} onClick={() => onPage(p)} style={{ color: p === page ? 'var(--fg)' : 'var(--fg-3)', cursor: 'pointer' }}>{p}</span>
      ))}
      <span className="kbd" onClick={() => onPage(Math.min(pages, page + 1))} style={{ cursor: 'pointer' }}>→</span>
    </div>
  );
}
