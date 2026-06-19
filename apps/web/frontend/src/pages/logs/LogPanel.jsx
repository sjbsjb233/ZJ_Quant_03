// LogPanel — the reusable app_log viewer extracted from AppLogs (V3 §4.4).
//
// One component, three shapes:
//   • central (lockSource=false)  → admin 中枢全量视图 (AppLogs)
//   • layered (lockSource + source) → 某层本层视图 (FactorLogs source="factor")
//   • embed   (embed + traceId)    → 嵌在运行详情/抽屉里的本 run 日志面板 (§4.4 D)
//
// The backend narrows ?source= by role (V3 §4.2), so lockSource is UX only —
// a viewer can't widen past their allowed layers even by editing the URL.
import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, keepPreviousData } from '@tanstack/react-query';
import { FilterBar, FInput, FSelect } from '../../components/shell';
import { Loading, ErrorState } from '../../components/ui';
import { endpoints, logsStreamUrl } from '../../lib/api';
import { fmtN } from '../../lib/format';
import { LogTable, LogDetail, DetailHeader, SrcMark } from './shared';

export const LEVEL_OPTIONS = [
  { value: '', label: 'all' },
  { value: 'WARN,ERROR,CRITICAL', label: 'WARN+' },
  { value: 'ERROR,CRITICAL', label: 'ERROR+' },
  { value: 'INFO', label: 'INFO' },
  { value: 'WARN', label: 'WARN' },
  { value: 'ERROR', label: 'ERROR' },
  { value: 'CRITICAL', label: 'CRITICAL' },
  { value: 'DEBUG', label: 'DEBUG' },
];
export const SOURCE_OPTIONS = [
  { value: '', label: 'all' },
  { value: 'backend', label: 'backend' },
  { value: 'data', label: 'data' },
  { value: 'factor', label: 'factor' },
  { value: 'frontend', label: 'frontend' },
  { value: 'monitor', label: 'monitor' },
  { value: 'data,factor,monitor', label: 'infra' },
];
const RANGE_OPTIONS = [
  { value: '24h', label: '24h' },
  { value: '7d', label: '7d' },
  { value: '30d', label: '30d' },
  { value: '', label: 'all' },
];
const PAGE_SIZE = 50;
const LIVE_CAP = 300;

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

// Live tail over SSE. Narrowed to `source` by the backend (per role); we further
// filter by trace_id client-side for the run-scoped embed. Never throws.
function useLiveTail(enabled, { source, level, traceId }) {
  const [rows, setRows] = useState([]);
  useEffect(() => {
    if (!enabled) { setRows([]); return undefined; }
    let es;
    try {
      es = new EventSource(logsStreamUrl({ source: source || undefined, level: level || undefined }));
    } catch {
      return undefined;
    }
    es.onmessage = (e) => {
      try {
        const row = JSON.parse(e.data);
        if (traceId && row.trace_id !== traceId) return;
        setRows((prev) => [row, ...prev].slice(0, LIVE_CAP));
      } catch { /* keepalive / malformed line — ignore */ }
    };
    es.onerror = () => { /* browser auto-reconnects; keep the buffer */ };
    return () => es.close();
  }, [enabled, source, level, traceId]);
  return rows;
}

export default function LogPanel({
  source = '',           // initial / locked source layer ('' = all)
  lockSource = false,    // hide the source selector (UX; backend is the real gate)
  defaultLevel = '',
  defaultEvent = '',     // initial (editable) event prefix filter (from URL)
  defaultTrace = '',     // initial (editable) trace_id filter (from URL)
  traceId = '',          // lock to one trace (run-scoped embed); hides the trace input
  embed = false,         // compact, single-column, fixed-height (for drawers)
  height,                // embed scroll height (px)
  onTrace,               // (trace_id) => void; defaults to navigate to the trace page
  defaultRange = '24h',
}) {
  const nav = useNavigate();
  const goTrace = onTrace || ((t) => nav(`/observ/trace/${t}`));

  const [search, setSearch] = useState('');
  const [level, setLevel] = useState(defaultLevel);
  const [srcSel, setSrcSel] = useState(source);
  const [logger, setLogger] = useState('');
  const [event, setEvent] = useState(defaultEvent);
  const [actor, setActor] = useState('');
  const [route, setRoute] = useState('');
  const [traceFilter, setTraceFilter] = useState(defaultTrace);
  const [range, setRange] = useState(traceId ? '' : defaultRange);  // a trace ignores the time window
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState(null);
  const [live, setLive] = useState(false);

  const effSource = lockSource ? source : srcSel;
  const fromTs = useMemo(() => rangeToFrom(range), [range]);
  const params = {
    ...(search && { search }), ...(level && { level }), ...(effSource && { source: effSource }),
    ...(logger && { logger }), ...(event && { event }), ...(actor && { actor }),
    ...(route && { route }),
    ...((traceId || traceFilter) && { trace_id: traceId || traceFilter }),
    ...(fromTs && { from: fromTs }),
  };

  const q = useQuery({
    queryKey: ['logPanel', params, page],
    queryFn: () => endpoints.logs({ ...params, page, page_size: PAGE_SIZE }),
    placeholderData: keepPreviousData,
    refetchInterval: live ? false : undefined,
  });

  const liveRows = useLiveTail(live, { source: effSource, level, traceId });

  const queried = q.data?.items || [];
  const rows = live ? liveRows : queried;
  const total = live ? liveRows.length : (q.data?.total || 0);
  const totalPages = Math.max(1, Math.ceil((q.data?.total || 0) / PAGE_SIZE));

  const applied = [search, level, lockSource ? '' : effSource, logger, event, actor, route,
    traceId ? '' : traceFilter].filter(Boolean).map((k) => ({ k }));

  function clearFilters() {
    setSearch(''); setLevel(''); if (!lockSource) setSrcSel('');
    setLogger(''); setEvent(''); setActor(''); setRoute('');
    if (!traceId) setTraceFilter('');
    setPage(1);
  }
  const reset = (setter) => (v) => { setter(v); setPage(1); };

  // ── filter + action toolbar ───────────────────────────────────────────────
  const toolbar = (
    <FilterBar applied={applied} onClear={clearFilters}>
      {!embed && (
        <FInput label="search" placeholder="message / event / logger" value={search}
          onChange={reset(setSearch)} width={170} testId="filter-search" />
      )}
      {!traceId && (
        <FSelect label="range" value={range} options={RANGE_OPTIONS} onChange={reset(setRange)} width={70} testId="filter-range" />
      )}
      <FSelect label="level" value={level} options={LEVEL_OPTIONS} onChange={reset(setLevel)} width={100} testId="filter-level" />
      {lockSource ? (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
          <span style={{ color: 'var(--fg-3)', fontSize: 10 }}>source:</span>
          <SrcMark src={source} full />
        </span>
      ) : (
        <FSelect label="source" value={srcSel} options={SOURCE_OPTIONS} onChange={reset(setSrcSel)} width={100} testId="filter-source" />
      )}
      {!embed && <FInput label="logger" placeholder="factor.*" value={logger} onChange={reset(setLogger)} width={120} testId="filter-logger" />}
      {!embed && <FInput label="event" placeholder="run.stage.*" value={event} onChange={reset(setEvent)} width={120} testId="filter-event" />}
      {!embed && <FInput label="actor" placeholder="username" value={actor} onChange={reset(setActor)} width={95} testId="filter-actor" />}
      {!embed && !traceId && <FInput label="trace_id" placeholder="t_..." value={traceFilter} onChange={reset(setTraceFilter)} width={120} testId="filter-trace" />}
      <span style={{ flex: 1 }} />
      <button className={`btn ghost sm${live ? ' active' : ''}`} data-testid="logpanel-live"
        onClick={() => setLive((v) => !v)} title="实时流 (SSE)"
        style={{ height: 20, fontSize: 10, color: live ? 'var(--ok)' : undefined }}>
        {live ? '● live' : '○ live'}
      </button>
      {!embed && <button className="btn ghost sm" onClick={() => download(params, 'csv')} style={{ height: 20, fontSize: 10 }}>↓ CSV</button>}
      {!embed && <button className="btn ghost sm" onClick={() => download(params, 'jsonl')} style={{ height: 20, fontSize: 10 }}>↓ JSONL</button>}
      <button className="btn ghost sm" data-testid="logpanel-refresh" onClick={() => q.refetch()} style={{ height: 20, fontSize: 10 }}>↻</button>
    </FilterBar>
  );

  const listBody = q.isLoading && !live
    ? <Loading />
    : q.error && !live
      ? <ErrorState error={q.error} onRetry={q.refetch} />
      : <LogTable rows={rows} selectedId={selected?.id}
          onSelect={embed ? (r) => goTrace(r.trace_id) : setSelected}
          emptyLabel={live ? '等待实时日志…' : '无匹配日志'} />;

  // ── embed: compact single column, row click → trace. Fixed `height`, or
  //    flex-fills its parent when height is omitted (e.g. a tall side drawer). ─
  if (embed) {
    const fill = height == null;
    return (
      <div data-testid="logpanel-embed" style={{ display: 'flex', flexDirection: 'column', minHeight: 0,
        flex: fill ? 1 : undefined,
        border: '1px solid var(--border)', borderRadius: 4, overflow: 'hidden' }}>
        {toolbar}
        <div className="scroll" style={{ ...(fill ? { flex: 1, minHeight: 0 } : { height }), overflow: 'auto', background: 'var(--surface)' }}>
          {listBody}
        </div>
        <div style={{ height: 24, padding: '0 12px', borderTop: '1px solid var(--border)', background: 'var(--surface-2)',
          display: 'flex', alignItems: 'center', gap: 10, fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>
          <span>{fmtN(rows.length)} 行{live ? ' · live' : ''}</span>
          <span style={{ flex: 1 }} />
          <span style={{ color: 'var(--info)', cursor: 'pointer' }} onClick={() => traceId && goTrace(traceId)}>
            {traceId ? '看整条 trace →' : ''}
          </span>
        </div>
      </div>
    );
  }

  // ── full page: filter bar + split list/detail ─────────────────────────────
  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, flex: 1 }}>
      {toolbar}
      <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1.55fr 1fr', minHeight: 0 }}>
        <div style={{ minWidth: 0, borderRight: '1px solid var(--border)', display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div className="scroll" style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
            {listBody}
          </div>
          <div style={{ height: 30, padding: '0 14px', borderTop: '1px solid var(--border)', background: 'var(--surface-2)', display: 'flex', alignItems: 'center', gap: 10, fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)', flexShrink: 0 }}>
            {live ? (
              <span><span className="dot ok" /> 实时流 · {fmtN(rows.length)} 行 (滚动缓冲)</span>
            ) : (
              <>
                <button className="btn ghost sm" style={{ height: 20, fontSize: 10 }} disabled={page <= 1} onClick={() => setPage((p) => Math.max(1, p - 1))}>‹ prev</button>
                <span>page <span style={{ color: 'var(--fg)' }}>{page}</span> / {totalPages}</span>
                <button className="btn ghost sm" style={{ height: 20, fontSize: 10 }} disabled={page >= totalPages} onClick={() => setPage((p) => Math.min(totalPages, p + 1))}>next ›</button>
                <span style={{ flex: 1 }} />
                <span>{fmtN(total)} 条 · {PAGE_SIZE}/页</span>
              </>
            )}
          </div>
        </div>
        <div style={{ minWidth: 0, display: 'flex', flexDirection: 'column', background: 'var(--surface)', minHeight: 0 }}>
          <DetailHeader label="LOG DETAIL" meta={selected ? selected.event : 'select a row'} />
          <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
            <LogDetail row={selected} onTrace={goTrace} />
          </div>
        </div>
      </div>
    </div>
  );
}
