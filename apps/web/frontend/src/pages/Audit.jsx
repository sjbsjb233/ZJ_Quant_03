// 审计日志 – audit event log with split detail panel
import React, { useState } from 'react';
import { useQuery, keepPreviousData } from '@tanstack/react-query';
import { TerminalShell, TPanel, FilterBar, FInput, FSelect } from '../components/shell';
import { Loading, ErrorState, Empty } from '../components/ui';
import { endpoints } from '../lib/api';
import { downloadCSV } from '../lib/download';
import { absTime } from '../lib/format';

const ACTOR_OPTIONS = [
  { value: '', label: 'all' },
  { value: 'system', label: 'system' },
  { value: 'weilin', label: 'weilin' },
  { value: 'lijun', label: 'lijun' },
  { value: 'unknown', label: 'unknown' },
];

const ACTION_OPTIONS = [
  { value: '', label: 'all' },
  { value: 'batch.write', label: 'batch.write' },
  { value: 'batch.invalidate', label: 'batch.invalidate' },
  { value: 'batch.replace', label: 'batch.replace' },
  { value: 'field.create', label: 'field.create' },
  { value: 'field.promote', label: 'field.promote' },
  { value: 'source.update', label: 'source.update' },
  { value: 'source.create', label: 'source.create' },
  { value: 'quality.alert.fired', label: 'quality.alert.fired' },
  { value: 'quality.rule.create', label: 'quality.rule.create' },
  { value: 'maintenance.vacuum', label: 'maintenance.vacuum' },
  { value: 'maintenance.compact', label: 'maintenance.compact' },
  { value: 'auth.login', label: 'auth.login' },
  { value: 'role.grant', label: 'role.grant' },
  { value: 'table.alter', label: 'table.alter' },
];

const STATUS_OPTIONS = [
  { value: '', label: 'all' },
  { value: 'success', label: 'success' },
  { value: 'failed', label: 'failed' },
];

const PAGE_SIZE = 50;

function actorColor(actor) {
  if (actor === 'system') return 'var(--info)';
  if (actor === 'unknown') return 'var(--err)';
  return 'var(--fg)';
}

export default function Audit() {
  const [search, setSearch] = useState('');
  const [actor, setActor] = useState('');
  const [action, setAction] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState(null);

  const filters = {
    ...(search ? { target: search } : {}),
    ...(actor ? { actor } : {}),
    ...(action ? { action } : {}),
    ...(statusFilter ? { status: statusFilter } : {}),
    page,
    page_size: PAGE_SIZE,
  };

  const auditQ = useQuery({
    queryKey: ['audit', filters],
    queryFn: () => endpoints.audit(filters),
    placeholderData: keepPreviousData,
  });

  function clearFilters() {
    setSearch(''); setActor(''); setAction(''); setStatusFilter(''); setPage(1);
  }

  function exportCsv() {
    const list = auditQ.data?.items || [];
    const rows = [['ts', 'actor', 'action', 'target', 'status', 'trace_id']];
    list.forEach((e) => rows.push([
      e.ts || e.timestamp || '', e.actor, e.action, e.target ?? e.target_id ?? '',
      e.status, e.trace_id ?? '',
    ]));
    downloadCSV(rows, `audit_${new Date().toISOString().slice(0, 10)}.csv`);
  }

  const applied = [search, actor, action, statusFilter].filter(Boolean).map((k) => ({ k }));
  const items = auditQ.data?.items || [];
  const total = auditQ.data?.total || 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const startRow = (page - 1) * PAGE_SIZE + 1;
  const endRow = Math.min(page * PAGE_SIZE, total);

  return (
    <TerminalShell
      module="data"
      current="audit"
      path={['data_mgmt/', 'audit']}
      fkeys={[
        { k: 'F1', l: '导出 CSV', onClick: exportCsv },
        { k: 'F5', l: '刷新', onClick: () => auditQ.refetch() },
      ]}
      title="审计日志"
      subtitle={`audit · ${total.toLocaleString()} events · last 30 days`}
      actions={
        <button className="btn ghost sm" onClick={exportCsv}>导出 CSV</button>
      }
      filterBar={
        <FilterBar applied={applied} onClear={clearFilters}>
          <FInput
            label="search"
            placeholder="target_id / object…"
            value={search}
            onChange={(v) => { setSearch(v); setPage(1); }}
            width={190}
          />
          <FSelect
            label="actor"
            value={actor}
            options={ACTOR_OPTIONS}
            onChange={(v) => { setActor(v); setPage(1); }}
            width={110}
          />
          <FSelect
            label="action"
            value={action}
            options={ACTION_OPTIONS}
            onChange={(v) => { setAction(v); setPage(1); }}
            width={180}
          />
          <FSelect
            label="status"
            value={statusFilter}
            options={STATUS_OPTIONS}
            onChange={(v) => { setStatusFilter(v); setPage(1); }}
            width={100}
          />
        </FilterBar>
      }
    >
      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        {/* Left: event list */}
        <div style={{ flex: 1.5, minWidth: 0, borderRight: '1px solid var(--border)', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div className="scroll" style={{ flex: 1, minHeight: 0 }}>
            {auditQ.isLoading ? (
              <Loading />
            ) : auditQ.error ? (
              <ErrorState error={auditQ.error} onRetry={auditQ.refetch} />
            ) : items.length === 0 ? (
              <Empty label="无匹配审计事件" />
            ) : (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
                <thead style={{ position: 'sticky', top: 0, background: 'var(--surface)', zIndex: 1 }}>
                  <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
                    {['ts (UTC+8)', 'actor', 'action', 'object', 'status', 'ip', ''].map((c) => (
                      <th key={c} style={{
                        textAlign: 'left', padding: '6px 10px',
                        fontSize: 9.5, color: 'var(--fg-3)', fontWeight: 500,
                        textTransform: 'uppercase', letterSpacing: '0.05em',
                      }}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {items.map((ev, i) => {
                    const isSel = selected?.id === ev.id;
                    const isFail = ev.result_status === 'failed';
                    return (
                      <tr
                        key={ev.id}
                        onClick={() => setSelected(isSel ? null : ev)}
                        style={{
                          borderBottom: '1px solid var(--border-faint)',
                          height: 26,
                          background: isSel ? 'var(--info-bg)' : (i % 2 === 0 ? 'transparent' : 'rgba(0,0,0,0.012)'),
                          cursor: 'pointer',
                        }}
                        onMouseEnter={(e) => { if (!isSel) e.currentTarget.style.background = 'var(--hover)'; }}
                        onMouseLeave={(e) => { e.currentTarget.style.background = isSel ? 'var(--info-bg)' : (i % 2 === 0 ? 'transparent' : 'rgba(0,0,0,0.012)'); }}
                      >
                        <td style={{ padding: '0 10px', color: 'var(--fg-2)', fontSize: 10.5, whiteSpace: 'nowrap' }}>
                          {absTime(ev.occurred_at)}
                        </td>
                        <td style={{ padding: '0 10px', color: actorColor(ev.actor) }}>{ev.actor}</td>
                        <td style={{ padding: '0 10px', color: 'var(--fg)', fontWeight: 500 }}>{ev.action}</td>
                        <td style={{ padding: '0 10px', color: 'var(--fg-2)', fontSize: 10.5, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {ev.target_id || '—'}
                        </td>
                        <td style={{ padding: '0 10px', color: isFail ? 'var(--err)' : 'var(--ok)' }}>
                          {isFail ? '✕ fail' : '✓ ok'}
                        </td>
                        <td style={{ padding: '0 10px', color: 'var(--fg-3)', fontSize: 10 }}>{ev.ip || '—'}</td>
                        <td style={{ padding: '0 10px', color: 'var(--info)', fontSize: 10, textAlign: 'right' }}>
                          {isSel ? '◀' : 'diff →'}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>

          {/* Pagination */}
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
            <div style={{ flex: 1 }} />
            <button
              className="btn ghost sm"
              style={{ height: 20, fontSize: 10 }}
              disabled={page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >← prev</button>
            <span style={{ color: 'var(--fg)' }}>{page}</span>
            <span>/ {totalPages}</span>
            <button
              className="btn ghost sm"
              style={{ height: 20, fontSize: 10 }}
              disabled={page >= totalPages}
              onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
            >next →</button>
          </div>
        </div>

        {/* Right: event detail panel */}
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <TPanel
            title="EVENT DETAIL"
            meta={selected ? `${selected.action} · ${selected.target_id || '—'}` : 'select a row'}
            noBorderRight
            last
          >
            {!selected ? (
              <div style={{ padding: 40, textAlign: 'center', color: 'var(--fg-3)', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
                ← 点击左侧行查看详情
              </div>
            ) : (
              <div style={{ padding: 14, fontFamily: 'var(--font-mono)', fontSize: 11, overflowY: 'auto' }}>
                {/* KV details */}
                <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr', gap: '4px 8px', marginBottom: 14 }}>
                  {[
                    ['event_id', selected.id],
                    ['occurred_at', absTime(selected.occurred_at)],
                    ['actor', selected.actor],
                    ['action', selected.action],
                    ['target_type', selected.target_type || '—'],
                    ['target_id', selected.target_id || '—'],
                    ['result_status', selected.result_status === 'failed' ? '✕ failed' : '✓ success'],
                    ['ip', selected.ip || '—'],
                    ...(selected.trace_id ? [['trace_id', selected.trace_id]] : []),
                    ...(selected.error_message ? [['error_message', selected.error_message]] : []),
                  ].map(([k, v]) => (
                    <React.Fragment key={k}>
                      <span style={{ color: 'var(--fg-3)', paddingTop: 2 }}>{k}</span>
                      <span style={{
                        color: k === 'result_status'
                          ? (selected.result_status === 'failed' ? 'var(--err)' : 'var(--ok)')
                          : k === 'actor' ? actorColor(selected.actor)
                          : 'var(--fg)',
                        wordBreak: 'break-all',
                      }}>{v}</span>
                    </React.Fragment>
                  ))}
                </div>

                {/* Payload */}
                <div style={{ fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>
                  REQUEST PAYLOAD
                </div>
                <div style={{
                  background: 'var(--surface-2)', border: '1px solid var(--border)',
                  borderRadius: 3, padding: 10, fontSize: 10.5,
                  whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                  color: 'var(--fg-2)', maxHeight: 300, overflowY: 'auto',
                }}>
                  {selected.request_payload
                    ? JSON.stringify(selected.request_payload, null, 2)
                    : 'null'}
                </div>

                <div style={{ marginTop: 12, display: 'flex', gap: 6 }}>
                  <button
                    className="btn ghost sm"
                    style={{ fontSize: 10 }}
                    onClick={() => navigator.clipboard?.writeText(JSON.stringify(selected, null, 2))}
                  >复制 JSON</button>
                  {selected.target_id && (
                    <button
                      className="btn ghost sm"
                      style={{ fontSize: 10 }}
                      onClick={() => {
                        const id = selected.target_id;
                        if (id.startsWith('b_')) window.location.assign(`/data/batches/${id}`);
                      }}
                    >跳到对象 →</button>
                  )}
                  {selected.trace_id && (
                    <button
                      className="btn ghost sm"
                      style={{ fontSize: 10 }}
                      onClick={() => window.location.assign(`/observ/trace/${selected.trace_id}`)}
                    >关联技术日志 →</button>
                  )}
                </div>
              </div>
            )}
          </TPanel>
        </div>
      </div>
    </TerminalShell>
  );
}
