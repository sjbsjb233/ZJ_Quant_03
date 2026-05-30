// 实时日志流 (LogStream) — /data/logs/stream · tail -f over SSE (doc §8.6)
import React, { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { TerminalShell } from '../../components/shell';
import { logsStreamUrl } from '../../lib/api';
import { LvlChip, SrcMark, TraceShort, fmtTsTime } from './shared';

const SOURCES = ['backend', 'data', 'factor', 'frontend'];
const LEVEL_OPTIONS = [
  { value: 'DEBUG', label: '≥ DEBUG' },
  { value: 'INFO', label: '≥ INFO' },
  { value: 'WARN', label: '≥ WARN' },
  { value: 'ERROR', label: '≥ ERROR' },
];
const CAP = 2000;

export default function LogStream() {
  const nav = useNavigate();
  const [minLevel, setMinLevel] = useState('INFO');
  const [sources, setSources] = useState(new Set(SOURCES));
  const [lines, setLines] = useState([]);
  const [paused, setPaused] = useState(false);
  const [status, setStatus] = useState('connecting');   // connecting|live|error
  const [received, setReceived] = useState(0);
  const [follow, setFollow] = useState(true);
  const pausedRef = useRef(paused);
  const followRef = useRef(follow);
  const scrollRef = useRef(null);
  const esRef = useRef(null);
  pausedRef.current = paused;
  followRef.current = follow;

  // (re)connect when the server-side filters change
  useEffect(() => {
    const url = logsStreamUrl({ level: minLevel, source: [...sources].join(',') });
    const es = new EventSource(url);
    esRef.current = es;
    setStatus('connecting');
    es.onopen = () => setStatus('live');
    es.onerror = () => setStatus('error');
    es.onmessage = (e) => {
      if (!e.data) return;
      let rec;
      try { rec = JSON.parse(e.data); } catch { return; }
      setReceived((n) => n + 1);
      if (pausedRef.current) return;
      setLines((prev) => {
        const next = prev.length >= CAP ? prev.slice(prev.length - CAP + 1) : prev.slice();
        next.push({ ...rec, _k: `${rec.id ?? ''}-${Math.random()}` });
        return next;
      });
    };
    return () => es.close();
  }, [minLevel, sources]);

  // auto-follow to the bottom on new lines
  useEffect(() => {
    if (followRef.current && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [lines]);

  function onScroll() {
    const el = scrollRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    setFollow(atBottom);
  }

  function toggleSource(s) {
    setSources((prev) => {
      const next = new Set(prev);
      if (next.has(s)) next.delete(s); else next.add(s);
      return next.size ? next : prev;   // keep at least one
    });
  }

  const statusColor = status === 'live' ? 'var(--ok)' : status === 'error' ? 'var(--err)' : 'var(--warn)';

  return (
    <TerminalShell
      module="data" current="logs"
      path={['data_mgmt/', 'logs/', 'stream']}
      fkeys={[
        { k: 'F1', l: '日志中心', onClick: () => nav('/data/logs') },
        { k: 'F2', l: '应用日志', onClick: () => nav('/data/logs/app') },
        { k: 'F3', l: '前端日志', onClick: () => nav('/data/logs/client') },
        { k: 'space', l: paused ? '继续' : '暂停', onClick: () => setPaused((p) => !p), active: paused },
        { k: 'c', l: '清屏', onClick: () => setLines([]) },
      ]}
      title="实时日志流"
      subtitle={`logs/stream · SSE · tail -f live · 已接收 ${received.toLocaleString()} 条`}
      badge={<span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontFamily: 'var(--font-mono)', fontSize: 10.5, color: statusColor }}>
        <span className="dot running" style={{ background: statusColor }} /> {status === 'live' ? 'CONNECTED' : status.toUpperCase()}
      </span>}
      actions={<>
        <button className="btn ghost sm" onClick={() => setPaused((p) => !p)}>{paused ? '▶ 继续' : '⏸ 暂停'}</button>
        <button className="btn ghost sm" onClick={() => setLines([])}>⌫ 清屏</button>
        <button className="btn ghost sm" onClick={() => navigator.clipboard?.writeText(lines.map((l) => JSON.stringify(l)).join('\n'))}>⎘ 复制</button>
      </>}
      statusMiddle={<span>status: <span style={{ color: statusColor }}>{status}</span> · heartbeat 15s</span>}
    >
      {/* control row */}
      <div style={{ padding: '8px 14px', background: 'var(--surface)', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap', fontFamily: 'var(--font-mono)', fontSize: 11, flexShrink: 0 }}>
        <span style={{ color: 'var(--fg-3)', fontSize: 9.5, textTransform: 'uppercase', letterSpacing: '0.06em' }}>display</span>
        <span style={{ color: 'var(--fg-mute)' }}>│</span>
        <span style={{ color: 'var(--fg-3)' }}>level:</span>
        <select value={minLevel} onChange={(e) => setMinLevel(e.target.value)} style={{ height: 22, padding: '0 7px', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3, fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--fg)' }}>
          {LEVEL_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <span style={{ color: 'var(--fg-mute)' }}>│</span>
        <span style={{ color: 'var(--fg-3)' }}>source:</span>
        {SOURCES.map((s) => {
          const on = sources.has(s);
          return (
            <span key={s} onClick={() => toggleSource(s)} style={{
              display: 'inline-flex', alignItems: 'center', gap: 6, padding: '2px 7px',
              border: `1px solid ${on ? 'var(--border-strong)' : 'var(--border-faint)'}`, borderRadius: 3,
              fontSize: 10, color: on ? 'var(--fg)' : 'var(--fg-mute)', background: on ? 'var(--surface-2)' : 'transparent', cursor: 'pointer',
            }}><SrcMark src={s} /> {s}</span>
          );
        })}
        <span style={{ flex: 1 }} />
        <span style={{ color: follow ? 'var(--ok)' : 'var(--fg-3)', display: 'inline-flex', alignItems: 'center', gap: 6 }}>
          {follow && <span className="dot running" />} {follow ? 'auto-follow' : 'paused-scroll'}
        </span>
        <span style={{ color: 'var(--fg-3)' }}>· buffer: <span style={{ color: 'var(--fg)' }}>{lines.length}/{CAP}</span></span>
      </div>

      {/* stream body */}
      <div style={{ flex: 1, minHeight: 0, position: 'relative', background: 'var(--surface)', fontFamily: 'var(--font-mono)' }}>
        <div ref={scrollRef} onScroll={onScroll} className="scroll" style={{ height: '100%', overflow: 'auto', padding: '8px 14px', fontSize: 11.5, lineHeight: 1.55 }}>
          {lines.map((r) => <StreamLine key={r._k} r={r} onTrace={() => r.trace_id && nav(`/data/logs/trace/${r.trace_id}`)} />)}
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 4, color: 'var(--fg-mute)', fontSize: 11 }}>
            <span style={{ display: 'inline-block', width: 8, height: 14, background: 'var(--fg-3)', animation: 'pulse 1.6s ease-in-out infinite' }} />
            <span>{paused ? 'paused — incoming dropped' : 'waiting for next event…'}</span>
          </div>
        </div>
        {!follow && (
          <div onClick={() => { setFollow(true); if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight; }}
            style={{ position: 'absolute', right: 20, bottom: 20, padding: '6px 10px', background: 'var(--fg)', color: 'var(--bg)', borderRadius: 12, fontFamily: 'var(--font-mono)', fontSize: 10.5, fontWeight: 500, cursor: 'pointer', boxShadow: '0 4px 12px rgba(0,0,0,0.18)' }}>↓ 跟随最新</div>
        )}
      </div>

      <div style={{ padding: '6px 14px', background: 'var(--surface-2)', borderTop: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 16, fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', flexShrink: 0 }}>
        <span>received: <span style={{ color: 'var(--fg)' }}>{received.toLocaleString()}</span> this session</span>
        <span>· rendered: <span style={{ color: 'var(--fg)' }}>{lines.length}</span> ({CAP} cap)</span>
        <span style={{ flex: 1 }} />
        <span><span className="kbd" style={{ fontSize: 9, height: 14, lineHeight: '14px' }}>space</span> pause · <span className="kbd" style={{ fontSize: 9, height: 14, lineHeight: '14px' }}>c</span> clear</span>
      </div>
    </TerminalShell>
  );
}

function StreamLine({ r, onTrace }) {
  const errish = r.level === 'ERROR' || r.level === 'CRITICAL';
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '80px 78px 26px 200px 1fr 90px', gap: 10, padding: '1px 0',
      borderLeft: errish ? '2px solid var(--err)' : r.level === 'WARN' ? '2px solid var(--warn)' : '2px solid transparent',
      paddingLeft: 8, marginLeft: -8, background: errish ? 'rgba(220, 60, 30, 0.04)' : 'transparent', whiteSpace: 'nowrap',
    }}>
      <span style={{ color: 'var(--fg-3)', fontSize: 10.5 }}>{fmtTsTime(r.ts)}</span>
      <span><LvlChip lvl={r.level} mini /></span>
      <span><SrcMark src={r.source} /></span>
      <span style={{ color: errish ? 'var(--fg)' : 'var(--fg-2)', fontWeight: errish ? 500 : 400, overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.event}</span>
      <span style={{ color: errish ? 'var(--fg)' : 'var(--fg-2)', overflow: 'hidden', textOverflow: 'ellipsis' }}>{r.message || r.exc_message || r.route || ''}</span>
      <span>{r.trace_id ? <TraceShort id={r.trace_id} onClick={onTrace} /> : <span style={{ color: 'var(--fg-mute)' }}>—</span>}</span>
    </div>
  );
}
