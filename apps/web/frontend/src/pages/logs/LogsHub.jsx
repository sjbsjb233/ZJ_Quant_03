// 日志中心 · 总览 (LogsHub) — /data/logs · 全链路排障入口 (doc §8.2)
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { TerminalShell, TPanel } from '../../components/shell';
import { Loading, ErrorState } from '../../components/ui';
import { endpoints } from '../../lib/api';
import { fmtN } from '../../lib/format';
import { KpiCell, StackedBars, LvlChip, SrcMark, TraceShort, fmtTsTime } from './shared';

export default function LogsHub() {
  const nav = useNavigate();
  const q = useQuery({
    queryKey: ['logsSummary'],
    queryFn: endpoints.logsSummary,
    refetchInterval: 60_000,
  });
  const s = q.data;
  const toTrace = (tid) => tid && nav(`/data/logs/trace/${tid}`);

  return (
    <TerminalShell
      module="data" current="logs"
      path={['data_mgmt/', 'logs']}
      fkeys={[
        { k: 'F1', l: '应用日志', onClick: () => nav('/data/logs/app') },
        { k: 'F2', l: '前端日志', onClick: () => nav('/data/logs/client') },
        { k: 'F3', l: '实时流', onClick: () => nav('/data/logs/stream') },
        { k: 'F4', l: '审计日志', onClick: () => nav('/data/audit') },
        { k: 'F5', l: '刷新', onClick: () => q.refetch() },
      ]}
      title="日志中心"
      subtitle="logs · 全链路排障入口 · admin only"
      badge={<span className="chip warn" style={{ fontSize: 10 }}>ADMIN</span>}
      actions={<button className="btn ghost sm" onClick={() => q.refetch()}>↻ 刷新</button>}
      statusMiddle={s && <span>logs.queue: <span style={{ color: 'var(--ok)' }}>{s.health.queue_size}/{s.health.queue_max ?? 10000}</span></span>}
    >
      {q.isLoading ? <Loading label="加载日志总览…" />
        : q.error ? <ErrorState error={q.error} onRetry={q.refetch} />
        : (
          <>
            {/* A. KPI band */}
            <div data-testid="kpi-strip" style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', background: 'var(--surface)', borderBottom: '1px solid var(--border-strong)', flexShrink: 0 }}>
              <KpiCell label="TODAY · TOTAL" value={fmtN(s.kpis.today_total)} sub="自 00:00 起 (UTC)" />
              <KpiCell label="ERROR + CRITICAL" value={fmtN(s.kpis.error)} tone="err" accent />
              <KpiCell label="WARN" value={fmtN(s.kpis.warn)} tone="warn" />
              <KpiCell label="SLOW REQ > 2s" value={fmtN(s.kpis.slow)} tone="warn" />
              <KpiCell label="HTTP 5xx" value={fmtN(s.kpis['5xx'])} tone="err" />
              <KpiCell label="前端崩溃" value={fmtN(s.kpis.frontend_crash)} tone="err" last />
            </div>

            {/* B. histogram + top errors / actors */}
            <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1.45fr 1fr', minHeight: 0, borderBottom: '1px solid var(--border-strong)' }}>
              <TPanel title="VOLUME · BY HOUR" meta="24h · stacked by level"
                headerExtra={<LegendRow />}>
                <div data-testid="timeline-24h" style={{ padding: '20px 24px 12px', height: '100%', display: 'flex', flexDirection: 'column' }}>
                  <div style={{ flex: 1, display: 'flex', alignItems: 'flex-end', minHeight: 0 }}>
                    <StackedBars data={s.timeline_24h} width={900} height={300} />
                  </div>
                </div>
              </TPanel>

              <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
                <TPanel title="TOP ERRORS · TODAY" meta="grouped by event" style={{ flex: 1, minHeight: 0 }} testId="top-errors">
                  <MiniTable
                    cols={['event', 'cnt', 'last trace', '']}
                    rows={s.top_errors}
                    render={(r) => (
                      <>
                        <td style={{ padding: '0 10px', color: 'var(--fg)' }}>{r.event}</td>
                        <td style={{ padding: '0 10px', textAlign: 'right', color: 'var(--err)', fontWeight: 600 }}>{r.count}</td>
                        <td style={{ padding: '0 10px' }}><TraceShort id={r.last_trace_id} onClick={() => toTrace(r.last_trace_id)} /></td>
                        <td style={{ padding: '0 10px', textAlign: 'right', color: 'var(--info)', fontSize: 10, cursor: 'pointer' }} onClick={() => nav(`/data/logs/app?event=${encodeURIComponent(r.event)}`)}>filter →</td>
                      </>
                    )}
                    empty="今日无 ERROR"
                  />
                </TPanel>
                <TPanel title="TOP ACTORS · TODAY" meta="users + system" last style={{ flex: 1, minHeight: 0 }}>
                  <MiniTable
                    cols={['actor', 'cnt', 'mix']}
                    rows={s.top_actors}
                    render={(r) => {
                      const max = Math.max(1, ...s.top_actors.map((a) => a.count));
                      return (
                        <>
                          <td style={{ padding: '0 10px', color: r.actor === 'system' ? 'var(--info)' : 'var(--fg)' }}>{r.actor}</td>
                          <td style={{ padding: '0 10px', textAlign: 'right', color: 'var(--fg)', fontVariantNumeric: 'tabular-nums' }}>{fmtN(r.count)}</td>
                          <td style={{ padding: '0 10px' }}>
                            <div style={{ height: 6, background: 'var(--surface-2)', borderRadius: 1, width: 130 }}>
                              <div style={{ height: 6, width: `${(r.count / max) * 100}%`, background: 'var(--fg-3)', borderRadius: 1 }} />
                            </div>
                          </td>
                        </>
                      );
                    }}
                    empty="今日无活动"
                  />
                </TPanel>
              </div>
            </div>

            {/* E. recent errors */}
            <TPanel title="RECENT ERROR · LAST 20" meta="click row → trace detail" testId="recent-errors"
              action={<a style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--info)', cursor: 'pointer' }} onClick={() => nav('/data/logs/app?level=ERROR,CRITICAL')}>查看全部 ERROR →</a>}
              style={{ flex: 1, minHeight: 0, borderBottom: '1px solid var(--border-strong)' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
                <thead style={{ position: 'sticky', top: 0, background: 'var(--surface)', zIndex: 1 }}>
                  <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
                    {['time', 'lvl', 'src', 'event', 'message', 'route', 'actor', 'trace'].map((c) => (
                      <th key={c} style={{ textAlign: 'left', padding: '5px 10px', fontSize: 9, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {s.recent_errors.map((r) => (
                    <tr key={r.id} onClick={() => toTrace(r.trace_id)} style={{ borderBottom: '1px solid var(--border-faint)', height: 25, cursor: 'pointer' }}>
                      <td style={{ padding: '0 10px', color: 'var(--fg-2)' }}>{fmtTsTime(r.ts)}</td>
                      <td style={{ padding: '0 10px' }}><LvlChip lvl={r.level} mini /></td>
                      <td style={{ padding: '0 10px' }}><SrcMark src={r.source} /></td>
                      <td style={{ padding: '0 10px', color: 'var(--fg)' }}>{r.event}</td>
                      <td style={{ padding: '0 10px', color: 'var(--fg-2)', maxWidth: 460, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.message || r.exc_message || '—'}</td>
                      <td style={{ padding: '0 10px', color: 'var(--fg-3)', fontSize: 10.5 }}>{r.route || '—'}</td>
                      <td style={{ padding: '0 10px', color: r.actor === 'system' ? 'var(--info)' : 'var(--fg-2)' }}>{r.actor || '—'}</td>
                      <td style={{ padding: '0 10px' }}><TraceShort id={r.trace_id} /></td>
                    </tr>
                  ))}
                  {!s.recent_errors.length && (
                    <tr><td colSpan={8} style={{ padding: 24, textAlign: 'center', color: 'var(--fg-3)' }}>近期无 ERROR 🎉</td></tr>
                  )}
                </tbody>
              </table>
            </TPanel>

            {/* F. health row */}
            <div data-testid="health-chips" style={{ display: 'flex', alignItems: 'center', gap: 26, padding: '10px 18px', background: 'var(--surface-2)', fontFamily: 'var(--font-mono)', fontSize: 11, flexShrink: 0 }}>
              <span style={{ fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>SINK HEALTH</span>
              <span style={{ color: 'var(--fg-mute)' }}>│</span>
              <span><span className={`dot ${s.health.db_sink === 'ok' ? 'ok' : 'err'}`} /> db sink: <span style={{ color: s.health.db_sink === 'ok' ? 'var(--ok)' : 'var(--err)' }}>{s.health.db_sink}</span></span>
              <span><span className={`dot ${s.health.file_sink === 'ok' ? 'ok' : 'warn'}`} /> file sink: <span style={{ color: s.health.file_sink === 'ok' ? 'var(--ok)' : 'var(--warn)' }}>{s.health.file_sink}</span></span>
              <span><span className="dot info" /> sse clients: <span style={{ color: 'var(--fg)' }}>{s.health.sse_clients}</span></span>
              <span><span className="dot ok" /> async queue: <span style={{ color: 'var(--fg)' }}>{s.health.queue_size} / {fmtN(s.health.queue_max ?? 10000)}</span></span>
              {s.health.dropped > 0 && <span style={{ color: 'var(--err)' }}>dropped: {s.health.dropped}</span>}
              <span style={{ flex: 1 }} />
              <span style={{ color: 'var(--fg-3)' }}>retention: <span style={{ color: 'var(--fg-2)' }}>app={s.health.retention_days}d</span> · auto-refresh 60s</span>
            </div>
          </>
        )}
    </TerminalShell>
  );
}

function LegendRow() {
  return (
    <span style={{ display: 'inline-flex', gap: 10, fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', marginLeft: 8 }}>
      <span><span style={{ display: 'inline-block', width: 8, height: 8, background: 'var(--info)', opacity: 0.55, marginRight: 4, verticalAlign: 'middle' }} />info</span>
      <span><span style={{ display: 'inline-block', width: 8, height: 8, background: 'var(--warn)', marginRight: 4, verticalAlign: 'middle' }} />warn</span>
      <span><span style={{ display: 'inline-block', width: 8, height: 8, background: 'var(--err)', marginRight: 4, verticalAlign: 'middle' }} />err</span>
    </span>
  );
}

function MiniTable({ cols, rows, render, empty }) {
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
      <thead>
        <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
          {cols.map((c, i) => (
            <th key={i} style={{ textAlign: c === 'cnt' ? 'right' : 'left', padding: '5px 10px', fontSize: 9, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{c}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.length ? rows.map((r, i) => (
          <tr key={i} style={{ borderBottom: '1px solid var(--border-faint)', height: 26 }}>{render(r)}</tr>
        )) : <tr><td colSpan={cols.length} style={{ padding: 18, textAlign: 'center', color: 'var(--fg-3)' }}>{empty}</td></tr>}
      </tbody>
    </table>
  );
}
