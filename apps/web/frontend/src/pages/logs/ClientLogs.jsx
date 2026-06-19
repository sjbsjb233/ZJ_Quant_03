// 前端日志 (ClientLogs) — /data/logs/client · JS 错误 + 用户动作 · 含会话视图 (doc §8.5)
import React, { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, keepPreviousData } from '@tanstack/react-query';
import { TerminalShell, FilterBar, FInput, FSelect } from '../../components/shell';
import { Loading, ErrorState, Empty } from '../../components/ui';
import { endpoints } from '../../lib/api';
import { fmtN, smartTs } from '../../lib/format';
import { LogTable, LogDetail, DetailHeader, TraceShort, fmtTs } from './shared';

const LEVEL_OPTIONS = [
  { value: '', label: 'all' },
  { value: 'ERROR,CRITICAL', label: 'ERROR+' },
  { value: 'WARN,ERROR,CRITICAL', label: 'WARN+' },
  { value: 'INFO', label: 'INFO' },
];
const RANGE_OPTIONS = [
  { value: '24h', label: '24h' }, { value: '7d', label: '7d' },
  { value: '30d', label: '30d' }, { value: '', label: 'all' },
];

function rangeToFrom(range) {
  const ms = { '24h': 864e5, '7d': 7 * 864e5, '30d': 30 * 864e5 }[range];
  return ms ? new Date(Date.now() - ms).toISOString() : undefined;
}

function browserOf(ua = '') {
  const b = /Firefox/.test(ua) ? 'Firefox' : /Edg/.test(ua) ? 'Edge'
    : /Chrome/.test(ua) ? 'Chrome' : /Safari/.test(ua) ? 'Safari' : 'Browser';
  const os = /Windows/.test(ua) ? 'Windows' : /Mac OS|Macintosh/.test(ua) ? 'macOS'
    : /Linux/.test(ua) ? 'Linux' : /iPhone|iPad/.test(ua) ? 'iOS' : /Android/.test(ua) ? 'Android' : '';
  return os ? `${b} · ${os}` : b;
}

export default function ClientLogs() {
  const nav = useNavigate();
  const [view, setView] = useState('list');
  const [search, setSearch] = useState('');
  const [level, setLevel] = useState('');
  const [session, setSession] = useState('');
  const [route, setRoute] = useState('');
  const [range, setRange] = useState('24h');
  const [selected, setSelected] = useState(null);

  // stable cutoff (see AppLogs): recomputing Date.now() each render loops the query
  const fromTs = useMemo(() => rangeToFrom(range), [range]);
  const params = {
    source: 'frontend', page_size: 200,
    ...(search && { search }), ...(level && { level }),
    ...(session && { session_id: session }), ...(route && { route }),
    ...(fromTs && { from: fromTs }),
  };
  const q = useQuery({
    queryKey: ['clientLogs', params],
    queryFn: () => endpoints.logs(params),
    placeholderData: keepPreviousData,
  });
  const items = q.data?.items || [];
  const total = q.data?.total || 0;
  const applied = [search, level, session, route].filter(Boolean).map((k) => ({ k }));

  function clearFilters() { setSearch(''); setLevel(''); setSession(''); setRoute(''); }

  return (
    <TerminalShell
      module="observ" current="sessions"
      path={['observ/', 'sessions']}
      fkeys={[
        { k: 'F1', l: '总览', onClick: () => nav('/observ/overview') },
        { k: 'F2', l: '日志', onClick: () => nav('/observ/logs') },
        { k: 'F3', l: '实时流', onClick: () => nav('/observ/stream') },
        { k: 'F5', l: '刷新', onClick: () => q.refetch() },
      ]}
      title="前端会话"
      subtitle={`sessions · JS 错误 + Promise rejection + React boundary + 用户动作 · ${fmtN(total)} 条`}
      actions={<>
        <div style={{ display: 'inline-flex', height: 26, border: '1px solid var(--border-strong)', borderRadius: 4, overflow: 'hidden', fontFamily: 'var(--font-mono)', fontSize: 11 }}>
          {[['list', '列表视图'], ['session', '会话视图']].map(([k, l]) => (
            <button key={k} onClick={() => setView(k)} style={{
              padding: '0 12px', background: view === k ? 'var(--fg)' : 'var(--surface)',
              color: view === k ? 'var(--bg)' : 'var(--fg-2)', border: 'none',
              borderRight: k === 'list' ? '1px solid var(--border-strong)' : 'none', cursor: 'pointer',
            }}>{l}</button>
          ))}
        </div>
        <button className="btn ghost sm" onClick={() => q.refetch()}>↻ 刷新</button>
      </>}
      filterBar={
        <FilterBar applied={applied} onClear={clearFilters}>
          <FInput label="search" placeholder="message / event" value={search} onChange={setSearch} width={180} testId="filter-search" />
          <FSelect label="range" value={range} options={RANGE_OPTIONS} onChange={setRange} width={80} testId="filter-range" />
          <FSelect label="level" value={level} options={LEVEL_OPTIONS} onChange={setLevel} width={110} testId="filter-level" />
          <FInput label="session" placeholder="s_..." value={session} onChange={setSession} width={130} testId="filter-session" />
          <FInput label="fe.route" placeholder="/data/*" value={route} onChange={setRoute} width={140} testId="filter-route" />
        </FilterBar>
      }
      statusMiddle={<span>view: <span style={{ color: 'var(--fg)' }}>{view}</span></span>}
    >
      {q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} onRetry={q.refetch} />
        : view === 'list' ? (
          <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1.55fr 1fr', minHeight: 0 }}>
            <div className="scroll" style={{ minWidth: 0, borderRight: '1px solid var(--border)', overflow: 'auto' }}>
              <LogTable rows={items} selectedId={selected?.id} onSelect={setSelected} emptyLabel="无前端日志" />
            </div>
            <div style={{ minWidth: 0, display: 'flex', flexDirection: 'column', background: 'var(--surface)', minHeight: 0 }}>
              <DetailHeader label="FE DETAIL" meta={selected ? selected.event : 'select a row'} />
              <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
                <LogDetail row={selected} onTrace={(t) => nav(`/observ/trace/${t}`)} />
              </div>
            </div>
          </div>
        ) : (
          <SessionView items={items} onTrace={(t) => nav(`/observ/trace/${t}`)} />
        )}
    </TerminalShell>
  );
}

function SessionView({ items, onTrace }) {
  const sessions = useMemo(() => {
    const by = new Map();
    for (const r of items) {
      const id = r.session_id || '—';
      if (!by.has(id)) by.set(id, []);
      by.get(id).push(r);
    }
    return Array.from(by.entries()).map(([id, rows]) => {
      rows.sort((a, b) => String(a.ts).localeCompare(String(b.ts)));
      const count = { ERROR: 0, WARN: 0, INFO: 0, DEBUG: 0, CRITICAL: 0 };
      rows.forEach((r) => { count[r.level] = (count[r.level] || 0) + 1; });
      const ua = rows.find((r) => r.extra?.user_agent)?.extra?.user_agent || '';
      return {
        id, rows,
        start: rows[0]?.ts, end: rows[rows.length - 1]?.ts,
        actor: rows.find((r) => r.actor)?.actor || '—',
        errs: (count.ERROR || 0) + (count.CRITICAL || 0), warns: count.WARN || 0, infos: count.INFO || 0,
        browser: browserOf(ua), viewport: rows.find((r) => r.extra?.viewport)?.extra?.viewport || '—',
        lastRoute: rows[rows.length - 1]?.route || '—',
      };
    }).sort((a, b) => String(b.start).localeCompare(String(a.start)));
  }, [items]);

  if (!sessions.length) return <Empty label="无会话数据" />;
  return (
    <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '6px 14px', background: 'var(--surface-2)', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10, fontFamily: 'var(--font-mono)', fontSize: 10.5, flexShrink: 0, color: 'var(--fg-3)' }}>
        <span>{sessions.length} sessions · {sessions.filter((s) => s.errs > 0).length} with errors</span>
      </div>
      <div className="scroll" style={{ flex: 1, overflow: 'auto', padding: 14, background: 'var(--bg)' }}>
        {sessions.map((s) => <SessionCard key={s.id} s={s} onTrace={onTrace} />)}
      </div>
    </div>
  );
}

function SessionCard({ s, onTrace }) {
  const [open, setOpen] = useState(s.errs > 0);
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border-strong)', borderRadius: 4, marginBottom: 12 }}>
      <div onClick={() => setOpen((o) => !o)} style={{
        padding: '10px 14px', display: 'grid', gridTemplateColumns: '230px 150px 90px 1fr 230px',
        gap: 14, alignItems: 'center', borderBottom: open ? '1px solid var(--border)' : 'none',
        background: s.errs > 0 ? 'var(--err-bg)' : 'var(--surface)', cursor: 'pointer',
      }}>
        <div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--fg)', fontWeight: 500 }}>{open ? '▼' : '▶'} {s.id}</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', marginTop: 2 }}>{s.actor}</div>
        </div>
        <div>
          <div title={`${fmtTs(s.start)} – ${fmtTs(s.end)}`} style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-2)' }}>{smartTs(s.start, { seconds: true })} – {smartTs(s.end, { seconds: true })}</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', marginTop: 2 }}>{s.rows.length} events</div>
        </div>
        <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
          {s.errs > 0 && <span className="chip err" style={{ fontSize: 10, height: 16 }}>E×{s.errs}</span>}
          {s.warns > 0 && <span className="chip warn" style={{ fontSize: 10, height: 16 }}>W×{s.warns}</span>}
          <span className="chip neutral" style={{ fontSize: 10, height: 16 }}>I×{s.infos}</span>
        </div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-2)' }}>{s.browser} · {s.viewport}</div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>last: {s.lastRoute}</div>
      </div>
      {open && (
        <div style={{ padding: '8px 14px 14px' }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', margin: '4px 0 8px' }}>TIMELINE</div>
          {s.rows.map((ev, i) => {
            const errish = ev.level === 'ERROR' || ev.level === 'CRITICAL' || ev.level === 'WARN';
            const isErr = ev.level === 'ERROR' || ev.level === 'CRITICAL';
            return (
              <div key={i} style={{ display: 'grid', gridTemplateColumns: '70px 14px 1fr', gap: 8, padding: '3px 0', alignItems: 'center' }}>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', textAlign: 'right' }} title={fmtTs(ev.ts)}>{smartTs(ev.ts, { seconds: true })}</span>
                <span style={{ width: 9, height: 9, borderRadius: isErr ? 0 : 5, transform: isErr ? 'rotate(45deg)' : 'none', justifySelf: 'center', background: isErr ? 'var(--err)' : ev.level === 'WARN' ? 'var(--warn)' : 'var(--info)' }} />
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontFamily: 'var(--font-mono)', fontSize: 11, minWidth: 0 }}>
                  <span style={{ color: errish ? (isErr ? 'var(--err)' : 'var(--warn)') : 'var(--fg)', fontWeight: errish ? 500 : 400, minWidth: 150 }}>{ev.event}</span>
                  <span style={{ color: 'var(--fg-2)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ev.message || ev.exc_message || ev.route || ''}</span>
                  {ev.trace_id && <TraceShort id={ev.trace_id} onClick={() => onTrace(ev.trace_id)} />}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
