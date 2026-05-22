// Trace 联查 (TraceDetail) — /data/logs/trace/:traceId · 全链路溯源 (doc §8.7)
import React from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { TerminalShell, TPanel } from '../../components/shell';
import { Loading, ErrorState, Empty } from '../../components/ui';
import { endpoints } from '../../lib/api';
import { absTime } from '../../lib/format';
import { LvlChip, SrcMark, fmtTs, fmtTsTime } from './shared';

const SRC_COLOR = { frontend: 'var(--info)', backend: 'var(--fg-2)', quant_data: 'var(--accent)' };
const SRC_ORDER = ['frontend', 'backend', 'quant_data'];

function toMs(ts) {
  if (!ts) return 0;
  const s = String(ts).endsWith('Z') || String(ts).includes('+') ? ts : `${ts}Z`;
  return Date.parse(s) || 0;
}

export default function TraceDetail() {
  const { traceId } = useParams();
  const nav = useNavigate();
  const q = useQuery({ queryKey: ['trace', traceId], queryFn: () => endpoints.logsTrace(traceId) });
  const data = q.data;

  const startMs = data?.summary?.start_ts ? toMs(data.summary.start_ts) : 0;
  const totalMs = Math.max(1, data?.summary?.duration_ms || 0);
  const events = (data?.logs || []).map((l) => ({ ...l, off: toMs(l.ts) - startMs }));
  const failed = data && ((data.summary.by_level?.ERROR || 0) + (data.summary.by_level?.CRITICAL || 0)) > 0;

  function exportTrace() {
    endpoints.logsExport({ trace_id: traceId, format: 'jsonl' }).then((blob) => {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `trace-${traceId}.jsonl`; a.click();
      URL.revokeObjectURL(url);
    });
  }

  return (
    <TerminalShell
      module="data" current="logs"
      path={['data_mgmt/', 'logs/', `trace/${(traceId || '').slice(0, 12)}…`]}
      fkeys={[
        { k: 'F1', l: '日志中心', onClick: () => nav('/data/logs') },
        { k: 'F2', l: '应用日志', onClick: () => nav('/data/logs/app') },
        { k: 'F3', l: '前端日志', onClick: () => nav('/data/logs/client') },
        { k: 'F4', l: '实时流', onClick: () => nav('/data/logs/stream') },
        { k: 'esc', l: '返回', onClick: () => nav(-1) },
      ]}
      title="Trace 联查"
      subtitle="logs/trace · 一次请求的全链路 · 跨 frontend / backend / quant_data"
      badge={data && (failed
        ? <span className="chip err" style={{ fontSize: 10 }}>✕ HAS ERROR</span>
        : <span className="chip ok" style={{ fontSize: 10 }}>✓ OK</span>)}
      actions={<>
        <button className="btn ghost sm" onClick={() => navigator.clipboard?.writeText(traceId)}>⎘ trace_id</button>
        <button className="btn ghost sm" onClick={exportTrace}>↓ JSONL</button>
        <button className="btn ghost sm" onClick={() => nav(-1)}>← 返回</button>
      </>}
      statusMiddle={<span>trace: <span style={{ color: 'var(--info)' }}>{traceId}</span></span>}
    >
      {q.isLoading ? <Loading label="加载 trace…" />
        : q.error ? <ErrorState error={q.error} onRetry={q.refetch} />
        : !data || data.summary.total === 0 ? <Empty label="该 trace_id 没有任何日志" hint={traceId} />
        : (
          <>
            {/* A. summary */}
            <div style={{ padding: '14px 18px', background: 'var(--surface)', borderBottom: '1px solid var(--border-strong)', display: 'grid', gridTemplateColumns: '1.6fr 1fr 1fr 1.4fr 1.2fr 1fr', gap: 24, flexShrink: 0 }}>
              <Summary label="TRACE ID">
                <div style={{ fontSize: 13, color: 'var(--fg)', display: 'flex', alignItems: 'center', gap: 8, wordBreak: 'break-all' }}>
                  {traceId}<span style={{ color: 'var(--info)', cursor: 'pointer' }} onClick={() => navigator.clipboard?.writeText(traceId)}>⎘</span>
                </div>
                <Sub>started {data.summary.start_ts ? absTime(`${data.summary.start_ts}Z`) : '—'}</Sub>
              </Summary>
              <Summary label="TOTAL DURATION">
                <BigNum tone={failed ? 'err' : 'fg'}>{data.summary.duration_ms ?? 0}<span style={{ fontSize: 11, color: 'var(--fg-3)' }}> ms</span></BigNum>
              </Summary>
              <Summary label="EVENTS">
                <BigNum>{data.summary.total}</BigNum>
                <Sub>{new Set((data.logs || []).map((l) => l.source)).size} sources</Sub>
              </Summary>
              <Summary label="LEVEL DIST">
                <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 2 }}>
                  {Object.entries(data.summary.by_level).map(([lvl, n]) => (
                    <span key={lvl}><LvlChip lvl={lvl} mini /><span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', marginLeft: 2 }}>×{n}</span></span>
                  ))}
                </div>
              </Summary>
              <Summary label="ROUTE">
                <div style={{ fontSize: 12, color: 'var(--fg)' }}>{data.summary.method || ''} {data.summary.route || '—'}</div>
              </Summary>
              <Summary label="ACTOR">
                <div style={{ fontSize: 12, color: 'var(--fg)' }}>{data.summary.actor || '—'}</div>
              </Summary>
            </div>

            {/* B. timeline */}
            <TPanel title="TIMELINE" meta={`${totalMs}ms · by source`} style={{ flexShrink: 0 }}>
              <div className="scroll" style={{ padding: '14px 24px 16px', overflow: 'auto' }}>
                <TimelineViz events={events} totalMs={totalMs} />
              </div>
            </TPanel>

            {/* C + D */}
            <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1.6fr 1fr', minHeight: 0 }}>
              <TPanel title="EVENTS" meta={`${events.length} · ts asc`}>
                <div style={{ fontFamily: 'var(--font-mono)' }}>
                  {events.map((ev, i) => <TraceEventRow key={ev.id ?? i} ev={ev} onTrace={() => nav(`/data/logs/app?trace_id=${traceId}`)} />)}
                </div>
              </TPanel>
              <TPanel title="RELATED AUDIT" meta={`${data.audit.length} entries · same trace_id`} last>
                <div style={{ padding: 14 }}>
                  {data.audit.length === 0 ? (
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-3)' }}>本 trace 未产生审计记录。</div>
                  ) : data.audit.map((a) => (
                    <div key={a.id} style={{ border: '1px solid var(--border-strong)', borderRadius: 4, marginBottom: 12 }}>
                      <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--border)', background: 'var(--surface-2)', display: 'flex', alignItems: 'center', gap: 8, fontFamily: 'var(--font-mono)', fontSize: 11 }}>
                        <span className={`chip ${a.result_status === 'failed' ? 'err' : 'ok'}`} style={{ fontSize: 10, height: 16 }}>{a.result_status === 'failed' ? '✕ FAILED' : '✓ OK'}</span>
                        <span style={{ color: 'var(--fg)', fontWeight: 500 }}>{a.action}</span>
                        <span style={{ flex: 1 }} />
                        <span style={{ color: 'var(--fg-3)', fontSize: 10 }}>{fmtTs(a.occurred_at)}</span>
                      </div>
                      <div style={{ padding: 12, display: 'grid', gridTemplateColumns: '90px 1fr', gap: 4, fontFamily: 'var(--font-mono)', fontSize: 11 }}>
                        {[['actor', a.actor], ['target', a.target_id || '—'], ['ip', a.ip || '—'],
                          ...(a.error_message ? [['error', a.error_message]] : [])].map(([k, v]) => (
                          <React.Fragment key={k}>
                            <span style={{ color: 'var(--fg-3)' }}>{k}</span>
                            <span style={{ color: k === 'error' ? 'var(--err)' : 'var(--fg)', wordBreak: 'break-all' }}>{v}</span>
                          </React.Fragment>
                        ))}
                      </div>
                    </div>
                  ))}
                  <div style={{ marginTop: 8 }}>
                    <button className="btn ghost sm" style={{ fontSize: 10 }} onClick={() => nav('/data/audit')}>打开审计日志 →</button>
                  </div>
                </div>
              </TPanel>
            </div>
          </>
        )}
    </TerminalShell>
  );
}

function Summary({ label, children }) {
  return (
    <div>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>{label}</div>
      {children}
    </div>
  );
}
function BigNum({ children, tone }) {
  const col = tone === 'err' ? 'var(--err)' : 'var(--fg)';
  return <div style={{ fontFamily: 'var(--font-mono)', fontSize: 22, fontWeight: 500, color: col, fontVariantNumeric: 'tabular-nums' }}>{children}</div>;
}
function Sub({ children }) {
  return <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', marginTop: 4 }}>{children}</div>;
}

function TimelineViz({ events, totalMs }) {
  const width = 1400;
  const trackH = 28;
  const tracks = SRC_ORDER.map((id, i) => ({ id, y: 14 + i * trackH, c: SRC_COLOR[id] }));
  const H = 14 + 3 * trackH + 20;
  const xOf = (off) => (off / totalMs) * (width - 20) + 10;
  const yOf = (s) => (tracks.find((t) => t.id === s) || tracks[1]).y;
  return (
    <svg width={width} height={H} className="spark" style={{ display: 'block', overflow: 'visible' }}>
      {tracks.map((t) => (
        <g key={t.id}>
          <text x={0} y={t.y + 4} fontSize="9.5" fill="var(--fg-3)" fontFamily="var(--font-mono)">{t.id.toUpperCase()}</text>
          <line x1={70} x2={width} y1={t.y} y2={t.y} stroke="var(--border-faint)" strokeWidth={1} />
        </g>
      ))}
      {events.map((ev, i) => {
        if (i === 0) return null;
        const p = events[i - 1];
        return <line key={`l${i}`} x1={xOf(p.off) + 70} x2={xOf(ev.off) + 70} y1={yOf(p.source)} y2={yOf(ev.source)} stroke="var(--border-strong)" strokeWidth={1} strokeDasharray="2 2" opacity={0.6} />;
      })}
      {events.map((ev, i) => {
        const x = xOf(ev.off) + 70;
        const y = yOf(ev.source);
        const isErr = ev.level === 'ERROR' || ev.level === 'CRITICAL';
        const isWarn = ev.level === 'WARN';
        if (isErr) {
          return <g key={i}>
            <circle cx={x} cy={y} r={8} fill="var(--err)" opacity={0.2} />
            <rect x={x - 5} y={y - 5} width={10} height={10} fill="var(--err)" transform={`rotate(45 ${x} ${y})`} />
          </g>;
        }
        if (isWarn) return <rect key={i} x={x - 3.5} y={y - 3.5} width={7} height={7} fill="var(--warn)" />;
        return <circle key={i} cx={x} cy={y} r={3} fill={SRC_COLOR[ev.source] || 'var(--fg-2)'} />;
      })}
      {[0, 0.25, 0.5, 0.75, 1].map((t, i) => {
        const x = t * (width - 20) + 80;
        return <text key={i} x={x} y={H - 4} fontSize="9" fill="var(--fg-3)" fontFamily="var(--font-mono)" textAnchor="middle">+{Math.round(t * totalMs)}ms</text>;
      })}
    </svg>
  );
}

function TraceEventRow({ ev, onTrace }) {
  const errish = ev.level === 'ERROR' || ev.level === 'CRITICAL';
  const [open, setOpen] = React.useState(errish);
  return (
    <div style={{
      borderBottom: '1px solid var(--border-faint)', background: errish ? 'rgba(220, 60, 30, 0.04)' : 'transparent',
      borderLeft: errish ? '2px solid var(--err)' : ev.level === 'WARN' ? '2px solid var(--warn)' : '2px solid transparent',
    }}>
      <div onClick={() => setOpen((o) => !o)} style={{ display: 'grid', gridTemplateColumns: '70px 78px 26px 200px 1fr 64px', gap: 10, padding: '4px 14px', alignItems: 'center', fontSize: 11, cursor: 'pointer' }}>
        <span style={{ color: 'var(--fg-3)', fontSize: 10.5 }}>+{ev.off}ms</span>
        <span><LvlChip lvl={ev.level} mini /></span>
        <span><SrcMark src={ev.source} /></span>
        <span style={{ color: errish ? 'var(--fg)' : 'var(--fg-2)', fontWeight: errish ? 500 : 400, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ev.event}</span>
        <span style={{ color: 'var(--fg-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ev.message || ev.exc_message || ev.route || ''}</span>
        <span style={{ textAlign: 'right', color: ev.duration_ms > 1000 ? 'var(--warn)' : 'var(--fg-3)', fontSize: 10.5 }}>{ev.duration_ms != null ? `${ev.duration_ms}ms` : ''}</span>
      </div>
      {open && (ev.extra || ev.exc_type) && (
        <div style={{ padding: '0 14px 8px 92px' }}>
          {ev.exc_type && (
            <div style={{ background: 'var(--err-bg)', border: '1px solid var(--err)', borderRadius: 3, padding: 8, fontSize: 10.5, marginBottom: 6 }}>
              <span style={{ color: 'var(--err)', fontWeight: 600 }}>{ev.exc_type}</span>: <span style={{ color: 'var(--fg)' }}>{ev.exc_message}</span>
              {ev.stack && <pre style={{ color: 'var(--fg-2)', whiteSpace: 'pre-wrap', fontSize: 10, margin: '6px 0 0' }}>{ev.stack}</pre>}
            </div>
          )}
          {ev.extra && <pre style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3, padding: 8, fontSize: 10, color: 'var(--fg-2)', whiteSpace: 'pre-wrap', margin: 0 }}>{JSON.stringify(ev.extra, null, 2)}</pre>}
        </div>
      )}
    </div>
  );
}
