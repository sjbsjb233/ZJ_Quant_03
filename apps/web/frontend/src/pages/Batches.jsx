// 批次台账 – list of all data ingestion batches
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery } from '@tanstack/react-query';
import { TerminalShell, KpiStrip, FilterBar, FInput, FSelect } from '../components/shell';
import { CopyableId, StatusGlyph, Loading, ErrorState, Modal } from '../components/ui';
import { useAuth } from '../lib/auth';
import { endpoints } from '../lib/api';
import { downloadCSV } from '../lib/download';
import { fmtCompact, fmtDuration, relTime, absTime } from '../lib/format';

const TARGET_TABLES = [
  { value: '', label: 'any (8)' },
  { value: 'raw.bars_daily', label: 'raw.bars_daily' },
  { value: 'raw.bars_minute', label: 'raw.bars_minute' },
  { value: 'raw.fundamentals', label: 'raw.fundamentals' },
  { value: 'raw.universe', label: 'raw.universe' },
  { value: 'raw.corporate_actions', label: 'raw.corporate_actions' },
  { value: 'raw.index_components', label: 'raw.index_components' },
  { value: 'raw.calendar', label: 'raw.calendar' },
  { value: 'reference.symbols', label: 'reference.symbols' },
];

const STATUS_OPTIONS = [
  { value: '', label: 'all' },
  { value: 'success', label: 'success' },
  { value: 'failed', label: 'failed' },
  { value: 'running', label: 'running' },
  { value: 'invalidated', label: 'invalidated' },
];

const SOURCE_OPTIONS = [
  { value: '', label: 'all' },
  { value: 'akshare', label: 'akshare' },
  { value: 'tushare_pro', label: 'tushare_pro' },
  { value: 'manual', label: 'manual' },
  { value: 'wind_intern', label: 'wind_intern' },
];

const RANGE_OPTIONS = [
  { value: '', label: 'all time' },
  { value: '7d', label: 'last 7 days' },
  { value: '30d', label: 'last 30 days' },
];

const PAGE_SIZE = 50;

export default function Batches() {
  const nav = useNavigate();
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const [source, setSource] = useState('');
  const [table, setTable] = useState('');
  const [range, setRange] = useState('7d');
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState(() => new Set());
  const [bulkOpen, setBulkOpen] = useState(false);
  const [bulkReason, setBulkReason] = useState('');

  const toggleSelect = (id) => setSelected((prev) => {
    const next = new Set(prev);
    next.has(id) ? next.delete(id) : next.add(id);
    return next;
  });

  const appliedCount = [search, status, source, table].filter(Boolean).length;

  const rangeToDate = (r) => {
    if (!r) return undefined;
    const d = new Date();
    if (r === '7d') { d.setDate(d.getDate() - 7); return d.toISOString().slice(0, 10); }
    if (r === '30d') { d.setDate(d.getDate() - 30); return d.toISOString().slice(0, 10); }
    return undefined;
  };

  const filters = {
    ...(search ? { batch_id: search } : {}),
    ...(status ? { status } : {}),
    ...(source ? { source } : {}),
    ...(table ? { target_table: table } : {}),
    ...(range ? { started_at_from: rangeToDate(range) } : {}),
    sort: 'started_at:desc',
    page,
    page_size: PAGE_SIZE,
  };

  const batchesQ = useQuery({
    queryKey: ['batches', filters],
    queryFn: () => endpoints.batches(filters),
    keepPreviousData: true,
  });

  const statsQ = useQuery({
    queryKey: ['batchStats', '7d'],
    queryFn: () => endpoints.batchStats('7d'),
  });

  const bulkInvalidate = useMutation({
    mutationFn: async (reason) => {
      const ids = [...selected];
      const results = await Promise.allSettled(ids.map((id) => endpoints.invalidateBatch(id, reason)));
      return { ids, results };
    },
    onSuccess: ({ ids, results }) => {
      batchesQ.refetch();
      const failedAny = results.some((r) => r.status === 'rejected');
      // drop the successfully-invalidated ids from the selection
      setSelected((prev) => {
        const next = new Set(prev);
        results.forEach((r, i) => { if (r.status === 'fulfilled') next.delete(ids[i]); });
        return next;
      });
      // only auto-close when every selected batch was invalidated; otherwise keep
      // the modal open so the partial-failure summary stays visible
      if (!failedAny) { setBulkOpen(false); setBulkReason(''); }
    },
  });

  function exportCsv() {
    const rows = [['batch_id', 'status', 'target_table', 'source', 'row_count', 'partition_count', 'started_at', 'duration_sec']];
    (batchesQ.data?.items || []).forEach((b) => rows.push([
      b.batch_id, b.status, b.target_table, b.source,
      b.row_count ?? '', b.partition_count ?? '', b.started_at, b.duration_sec ?? '',
    ]));
    downloadCSV(rows, `batches_${new Date().toISOString().slice(0, 10)}.csv`);
  }

  const selCount = selected.size;

  const stats = statsQ.data;
  const kpiCells = [
    { label: 'MATCHED', value: batchesQ.data ? String(batchesQ.data.total) : '—', sub: 'total' },
    { label: 'SUCCESS', value: stats ? String(stats.success) : '—', sub: `${stats ? Math.round(stats.success_rate * 100) : '—'}%`, tone: 'ok' },
    { label: 'FAILED', value: stats ? String(stats.failed) : '—', sub: 'last 7d', tone: stats?.failed ? 'err' : undefined },
    { label: 'RUNNING', value: stats ? String(stats.running) : '—', sub: 'active', tone: 'info', pulse: stats?.running > 0 },
    { label: 'INVALIDATED', value: stats ? String(stats.invalidated) : '—', sub: '7d', tone: 'neutral' },
    { label: 'ROWS WRITTEN', value: stats ? fmtCompact(stats.rows_written) : '—', sub: '7d window' },
  ];

  const items = batchesQ.data?.items || [];
  const total = batchesQ.data?.total || 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const startRow = (page - 1) * PAGE_SIZE + 1;
  const endRow = Math.min(page * PAGE_SIZE, total);

  function clearFilters() {
    setSearch(''); setStatus(''); setSource(''); setTable(''); setRange('7d'); setPage(1);
  }

  const applied = [search, status, source, table].filter(Boolean).map((k) => ({ k }));

  return (
    <TerminalShell
      module="data"
      current="batches"
      path={['data_mgmt/', 'batches']}
      fkeys={[
        { k: 'F2', l: selCount ? `批量作废 (${selCount})` : '批量作废', onClick: () => selCount && isAdmin && setBulkOpen(true), active: selCount > 0 },
        { k: 'F3', l: '导出', onClick: exportCsv },
        { k: 'F5', l: '刷新', onClick: () => batchesQ.refetch() },
      ]}
      title="批次台账"
      subtitle={`batches · ${total.toLocaleString()} total · sorted by started_at desc`}
      actions={
        <>
          <button className="btn ghost sm" onClick={exportCsv}>导出 CSV</button>
          <button
            className="btn ghost sm"
            disabled={!selCount || !isAdmin}
            title={!isAdmin ? '需要 admin 权限' : selCount ? '' : '请先勾选批次'}
            onClick={() => setBulkOpen(true)}
          >批量作废{selCount ? ` (${selCount})` : ''}</button>
        </>
      }
      filterBar={
        <FilterBar applied={applied} onClear={clearFilters}>
          <FInput
            label="search"
            placeholder="batch_id prefix…"
            value={search}
            onChange={(v) => { setSearch(v); setPage(1); }}
            width={190}
          />
          <FSelect
            label="status"
            value={status}
            options={STATUS_OPTIONS}
            onChange={(v) => { setStatus(v); setPage(1); }}
            width={120}
          />
          <FSelect
            label="source"
            value={source}
            options={SOURCE_OPTIONS}
            onChange={(v) => { setSource(v); setPage(1); }}
            width={130}
          />
          <FSelect
            label="table"
            value={table}
            options={TARGET_TABLES}
            onChange={(v) => { setTable(v); setPage(1); }}
            width={150}
          />
          <FSelect
            label="range"
            value={range}
            options={RANGE_OPTIONS}
            onChange={(v) => { setRange(v); setPage(1); }}
            width={120}
          />
        </FilterBar>
      }
    >
      <KpiStrip cells={kpiCells} height={52} />

      <div className="scroll" style={{ flex: 1, minHeight: 0 }}>
        {batchesQ.isLoading ? (
          <Loading />
        ) : batchesQ.error ? (
          <ErrorState error={batchesQ.error} onRetry={batchesQ.refetch} />
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
            <thead style={{ position: 'sticky', top: 0, background: 'var(--surface)', zIndex: 1 }}>
              <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
                {[
                  { l: '', w: 30, a: 'center' },
                  { l: 'batch_id', w: '18%', a: 'left' },
                  { l: 'status', w: 90, a: 'center' },
                  { l: 'table', w: '16%', a: 'left' },
                  { l: 'src', w: 110, a: 'left' },
                  { l: 'rows', w: 80, a: 'right' },
                  { l: 'parts', w: 50, a: 'right' },
                  { l: 'started ↓', w: '15%', a: 'left' },
                  { l: 'duration', w: 80, a: 'right' },
                  { l: 'q', w: 28, a: 'center' },
                  { l: '', w: 80, a: 'right' },
                ].map((c, i) => (
                  <th key={i} style={{
                    textAlign: c.a, padding: '7px 10px',
                    fontSize: 9.5, color: 'var(--fg-3)', fontWeight: 500,
                    fontFamily: 'var(--font-mono)', textTransform: 'uppercase', letterSpacing: '0.06em',
                    width: typeof c.w === 'number' ? c.w : undefined,
                  }}>{c.l}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr><td colSpan={11} style={{ textAlign: 'center', padding: 40, color: 'var(--fg-3)', fontSize: 12 }}>∅ 无匹配批次</td></tr>
              ) : items.map((b, i) => {
                const dimmed = b.status === 'invalidated';
                const errBg = b.status === 'failed' ? 'rgba(204,67,52,0.04)' : (i % 2 === 0 ? 'transparent' : 'rgba(0,0,0,0.012)');
                return (
                  <tr
                    key={b.batch_id}
                    onClick={() => nav(`/data/batches/${b.batch_id}`)}
                    style={{
                      borderBottom: '1px solid var(--border-faint)',
                      height: 27,
                      background: errBg,
                      opacity: dimmed ? 0.55 : 1,
                      cursor: 'pointer',
                      textDecoration: dimmed ? 'line-through' : 'none',
                    }}
                    onMouseEnter={(e) => { if (!dimmed) e.currentTarget.style.background = 'var(--hover)'; }}
                    onMouseLeave={(e) => { e.currentTarget.style.background = errBg; }}
                  >
                    <td style={{ padding: '0 6px', textAlign: 'center' }} onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        data-testid={`batch-select-${b.batch_id}`}
                        style={{ accentColor: 'var(--fg)' }}
                        checked={selected.has(b.batch_id)}
                        disabled={b.status === 'invalidated'}
                        onChange={() => toggleSelect(b.batch_id)}
                      />
                    </td>
                    <td style={{ padding: '0 10px', fontFamily: 'var(--font-mono)', color: 'var(--fg)' }}>
                      <CopyableId value={b.batch_id} length={24} />
                    </td>
                    <td style={{ padding: '0 10px', textAlign: 'center' }}>
                      <StatusGlyph status={b.status} withLabel />
                    </td>
                    <td style={{ padding: '0 10px', fontFamily: 'var(--font-mono)', color: 'var(--fg-2)', fontSize: 10.5 }}>{b.target_table}</td>
                    <td style={{ padding: '0 10px', fontFamily: 'var(--font-mono)', color: 'var(--fg-2)', fontSize: 10.5 }}>{b.source}</td>
                    <td style={{ padding: '0 10px', textAlign: 'right', fontFamily: 'var(--font-mono)', fontVariantNumeric: 'tabular-nums', color: 'var(--fg-2)' }}>
                      {b.row_count != null ? fmtCompact(b.row_count) : '—'}
                    </td>
                    <td style={{ padding: '0 10px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--fg-3)' }}>
                      {b.partition_count ?? '—'}
                    </td>
                    <td style={{ padding: '0 10px', fontFamily: 'var(--font-mono)', color: 'var(--fg-2)', fontSize: 10.5, whiteSpace: 'nowrap' }}>
                      <span title={absTime(b.started_at)}>{absTime(b.started_at)}</span>
                      <span style={{ color: 'var(--fg-4)', marginLeft: 5 }}>· {relTime(b.started_at)}</span>
                    </td>
                    <td style={{ padding: '0 10px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--fg-2)' }}>
                      {fmtDuration(b.duration_sec)}
                    </td>
                    <td style={{ padding: '0 10px', textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 13 }}>
                      {b.has_quality_issues
                        ? <span style={{ color: 'var(--warn)' }}>⚠</span>
                        : <span style={{ color: 'var(--fg-mute)' }}>·</span>}
                    </td>
                    <td style={{ padding: '0 10px', textAlign: 'right', fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>
                      {b.status === 'failed' ? 'view · retry →' : b.status === 'running' ? 'view · cancel →' : 'view →'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination footer */}
      <div style={{
        height: 30, padding: '0 14px',
        borderTop: '1px solid var(--border)',
        background: 'var(--surface-2)',
        display: 'flex', alignItems: 'center', gap: 12,
        fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)',
        flexShrink: 0,
      }}>
        <span>
          showing{' '}
          <span style={{ color: 'var(--fg)' }}>{total ? `${startRow}–${endRow}` : '0'}</span>
          {' '}of{' '}
          <span style={{ color: 'var(--fg)' }}>{total.toLocaleString()}</span>
        </span>
        <span style={{ color: 'var(--fg-mute)' }}>│</span>
        <span>page size: <span style={{ color: 'var(--fg)' }}>{PAGE_SIZE}</span></span>
        <div style={{ flex: 1 }} />
        <button
          className="btn ghost sm"
          style={{ height: 20, fontSize: 10, fontFamily: 'var(--font-mono)' }}
          disabled={page <= 1}
          onClick={() => setPage((p) => Math.max(1, p - 1))}
        >← prev</button>
        <span style={{ color: 'var(--fg)' }}>{page}</span>
        <span>/ {totalPages}</span>
        <button
          className="btn ghost sm"
          style={{ height: 20, fontSize: 10, fontFamily: 'var(--font-mono)' }}
          disabled={page >= totalPages}
          onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
        >next →</button>
      </div>

      {bulkOpen && (
        <Modal
          width={480}
          title={`批量作废 · ${selCount} 个批次`}
          onClose={() => !bulkInvalidate.isPending && setBulkOpen(false)}
          footer={<>
            <button className="btn sm" onClick={() => setBulkOpen(false)} disabled={bulkInvalidate.isPending}>取消</button>
            <button
              className="btn sm danger"
              disabled={bulkReason.trim().length < 10 || bulkInvalidate.isPending}
              onClick={() => bulkInvalidate.mutate(bulkReason.trim())}
            >{bulkInvalidate.isPending ? '执行中…' : '确认作废'}</button>
          </>}
        >
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11.5, color: 'var(--fg-2)', lineHeight: 1.7 }}>
            <p style={{ marginTop: 0 }}>即将作废以下 <b style={{ color: 'var(--err)' }}>{selCount}</b> 个批次。作废后这些批次写入的行将从当前生效视图移除(可在「含作废」视图查看)。</p>
            <div style={{ maxHeight: 120, overflow: 'auto', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3, padding: 8, marginBottom: 10 }}>
              {[...selected].map((id) => <div key={id} style={{ color: 'var(--fg-3)' }}>{id}</div>)}
            </div>
            <div style={{ fontSize: 10.5, color: 'var(--fg-3)', marginBottom: 4 }}>作废原因(至少 10 个字符):</div>
            <textarea
              data-testid="bulk-invalidate-reason"
              value={bulkReason}
              onChange={(e) => setBulkReason(e.target.value)}
              rows={3}
              placeholder="例如:上游源数据修订,重新回填后此批次失效"
              style={{ width: '100%', boxSizing: 'border-box', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)', padding: 8, resize: 'vertical' }}
            />
            {bulkInvalidate.data && (() => {
              const res = bulkInvalidate.data.results;
              const ok = res.filter((r) => r.status === 'fulfilled').length;
              const failed = res.filter((r) => r.status === 'rejected').length;
              return (
                <div style={{ marginTop: 8, color: failed ? 'var(--err)' : 'var(--ok)' }}>
                  完成 · 成功 {ok} · 失败 {failed}
                  {failed > 0 && <span style={{ color: 'var(--fg-3)' }}> · 失败项仍保留勾选,可修改原因后重试</span>}
                </div>
              );
            })()}
          </div>
        </Modal>
      )}
    </TerminalShell>
  );
}
