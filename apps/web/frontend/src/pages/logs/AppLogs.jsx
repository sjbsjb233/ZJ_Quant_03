// 应用日志 (AppLogs) — /data/logs/app · backend + quant_data 查询 (doc §8.3)
import React, { useMemo, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { TerminalShell, TPanel, FilterBar, FInput, FSelect } from '../../components/shell';
import { Loading, ErrorState } from '../../components/ui';
import { endpoints } from '../../lib/api';
import { fmtN } from '../../lib/format';
import { LogTable, LogDetail, DetailHeader } from './shared';

const LEVEL_OPTIONS = [
  { value: '', label: 'all' },
  { value: 'WARN,ERROR,CRITICAL', label: 'WARN+' },
  { value: 'ERROR,CRITICAL', label: 'ERROR+' },
  { value: 'INFO', label: 'INFO' },
  { value: 'WARN', label: 'WARN' },
  { value: 'ERROR', label: 'ERROR' },
  { value: 'CRITICAL', label: 'CRITICAL' },
  { value: 'DEBUG', label: 'DEBUG' },
];
const SOURCE_OPTIONS = [
  { value: '', label: 'all' },
  { value: 'backend', label: 'backend' },
  { value: 'data', label: 'data' },
  { value: 'factor', label: 'factor' },
  { value: 'frontend', label: 'frontend' },
  { value: 'backend,data', label: 'be + data' },
];
const RANGE_OPTIONS = [
  { value: '24h', label: '24h' },
  { value: '7d', label: '7d' },
  { value: '30d', label: '30d' },
  { value: '', label: 'all' },
];
const PAGE_SIZE = 50;

function rangeToFrom(range) {
  if (!range) return undefined;
  const ms = { '24h': 864e5, '7d': 7 * 864e5, '30d': 30 * 864e5 }[range];
  return ms ? new Date(Date.now() - ms).toISOString() : undefined;
}

async function download(params, fmt) {
  const blob = await endpoints.logsExport({ ...params, format: fmt });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `app_log.${fmt === 'csv' ? 'csv' : 'jsonl'}`;
  a.click();
  URL.revokeObjectURL(url);
}

export default function AppLogs() {
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const [search, setSearch] = useState('');
  const [level, setLevel] = useState(sp.get('level') || '');
  const [source, setSource] = useState(sp.get('source') || '');
  const [logger, setLogger] = useState('');
  const [event, setEvent] = useState(sp.get('event') || '');
  const [actor, setActor] = useState('');
  const [route, setRoute] = useState('');
  const [traceId, setTraceId] = useState(sp.get('trace_id') || '');
  const [range, setRange] = useState('24h');
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState(null);

  // memoize the time cutoff so the query key is stable across renders
  // (recomputing Date.now() every render would loop the query forever)
  const fromTs = useMemo(() => rangeToFrom(range), [range]);
  const params = {
    ...(search && { search }), ...(level && { level }), ...(source && { source }),
    ...(logger && { logger }), ...(event && { event }), ...(actor && { actor }),
    ...(route && { route }), ...(traceId && { trace_id: traceId }),
    ...(fromTs && { from: fromTs }),
  };
  const q = useQuery({
    queryKey: ['appLogs', params, page],
    queryFn: () => endpoints.logs({ ...params, page, page_size: PAGE_SIZE }),
    keepPreviousData: true,
  });

  const items = q.data?.items || [];
  const total = q.data?.total || 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const applied = [search, level, source, logger, event, actor, route, traceId].filter(Boolean).map((k) => ({ k }));

  function clearFilters() {
    setSearch(''); setLevel(''); setSource(''); setLogger(''); setEvent('');
    setActor(''); setRoute(''); setTraceId(''); setPage(1);
  }
  const reset = (setter) => (v) => { setter(v); setPage(1); };

  return (
    <TerminalShell
      module="data" current="logs"
      path={['data_mgmt/', 'logs/', 'app']}
      fkeys={[
        { k: 'F1', l: '日志中心', onClick: () => nav('/data/logs') },
        { k: 'F2', l: '前端日志', onClick: () => nav('/data/logs/client') },
        { k: 'F3', l: '实时流', onClick: () => nav('/data/logs/stream') },
        { k: 'F4', l: '导出 JSONL', onClick: () => download(params, 'jsonl') },
        { k: 'F5', l: '刷新', onClick: () => q.refetch() },
      ]}
      title="应用日志"
      subtitle={`logs/app · backend + quant_data · ${fmtN(total)} 条 (已过滤)`}
      actions={<>
        <button className="btn ghost sm" onClick={() => download(params, 'csv')}>↓ CSV</button>
        <button className="btn ghost sm" onClick={() => download(params, 'jsonl')}>↓ JSONL</button>
        <button className="btn ghost sm" onClick={() => q.refetch()}>↻ 刷新</button>
      </>}
      filterBar={
        <FilterBar applied={applied} onClear={clearFilters}>
          <FInput label="search" placeholder="message / event / logger" value={search} onChange={reset(setSearch)} width={190} testId="filter-search" />
          <FSelect label="range" value={range} options={RANGE_OPTIONS} onChange={reset(setRange)} width={80} testId="filter-range" />
          <FSelect label="level" value={level} options={LEVEL_OPTIONS} onChange={reset(setLevel)} width={110} testId="filter-level" />
          <FSelect label="source" value={source} options={SOURCE_OPTIONS} onChange={reset(setSource)} width={110} testId="filter-source" />
          <FInput label="logger" placeholder="quant_data.*" value={logger} onChange={reset(setLogger)} width={130} testId="filter-logger" />
          <FInput label="event" placeholder="reader.query.*" value={event} onChange={reset(setEvent)} width={130} testId="filter-event" />
          <FInput label="actor" placeholder="username" value={actor} onChange={reset(setActor)} width={100} testId="filter-actor" />
          <FInput label="route" placeholder="/api/v1/..." value={route} onChange={reset(setRoute)} width={130} testId="filter-route" />
          <FInput label="trace_id" placeholder="t_..." value={traceId} onChange={reset(setTraceId)} width={130} testId="filter-trace" />
        </FilterBar>
      }
      statusMiddle={selected && <span>sel: <span style={{ color: 'var(--info)' }}>{(selected.trace_id || '').replace(/^t_/, '').slice(-6)}</span></span>}
    >
      <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1.55fr 1fr', minHeight: 0 }}>
        {/* list + pagination */}
        <div style={{ minWidth: 0, borderRight: '1px solid var(--border)', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div className="scroll" style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
            {q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} onRetry={q.refetch} />
              : <LogTable rows={items} selectedId={selected?.id} onSelect={setSelected} />}
          </div>
          <div style={{ height: 30, padding: '0 14px', borderTop: '1px solid var(--border)', background: 'var(--surface-2)', display: 'flex', alignItems: 'center', gap: 10, fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)', flexShrink: 0 }}>
            <button className="btn ghost sm" style={{ height: 20, fontSize: 10 }} disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>‹ prev</button>
            <span>page <span style={{ color: 'var(--fg)' }}>{page}</span> / {totalPages}</span>
            <button className="btn ghost sm" style={{ height: 20, fontSize: 10 }} disabled={page >= totalPages} onClick={() => setPage((p) => Math.min(totalPages, p + 1))}>next ›</button>
            <span style={{ flex: 1 }} />
            <span>{fmtN(total)} 条 · {PAGE_SIZE}/页</span>
          </div>
        </div>

        {/* detail */}
        <div style={{ minWidth: 0, display: 'flex', flexDirection: 'column', background: 'var(--surface)', minHeight: 0 }}>
          <DetailHeader label="LOG DETAIL" meta={selected ? selected.event : 'select a row'} />
          <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
            <LogDetail row={selected} onTrace={(t) => nav(`/data/logs/trace/${t}`)} />
          </div>
        </div>
      </div>
    </TerminalShell>
  );
}
