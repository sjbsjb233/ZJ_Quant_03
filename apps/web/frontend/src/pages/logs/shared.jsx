// Shared atoms for the log-centre pages (全链路日志方案 v2.0 §8.9).
// Ported from the design handoff (pages-logs-shared.jsx) into the project's
// component style; same terminal aesthetic as the rest of the shell.
import React from 'react';
import { absTime, relTime, dayBoundaryLabel } from '../../lib/format';

export const LEVELS = ['DEBUG', 'INFO', 'WARN', 'ERROR', 'CRITICAL'];

const LVL_META = {
  DEBUG: { tone: 'neutral', g: '·' },
  INFO: { tone: 'info', g: 'i' },
  WARN: { tone: 'warn', g: '!' },
  ERROR: { tone: 'err', g: '✕' },
  CRITICAL: { tone: 'err', g: '✕✕' },
};

// Level → coloured chip with a glyph (colour + glyph double-encode for a11y).
export function LvlChip({ lvl, mini }) {
  const m = LVL_META[lvl] || LVL_META.INFO;
  return (
    <span className={`chip ${m.tone}`} style={{
      fontSize: mini ? 9 : 10, height: mini ? 14 : 16, padding: mini ? '0 4px' : '0 5px',
      fontWeight: lvl === 'ERROR' || lvl === 'CRITICAL' ? 600 : 500,
    }}>
      <span style={{ opacity: 0.7 }}>{m.g}</span><span>{lvl}</span>
    </span>
  );
}

const SRC_META = {
  frontend: { c: 'var(--info)', g: 'F', label: 'frontend' },
  backend: { c: 'var(--fg-2)', g: 'B', label: 'backend' },
  // V3 §6.6: data-layer logs (web read console + data-keeper) = "data";
  // factor-api / factor-worker = "factor". (legacy "quant_data" kept as an alias)
  data: { c: 'var(--accent)', g: 'D', label: 'data' },
  quant_data: { c: 'var(--accent)', g: 'D', label: 'data' },
  factor: { c: 'var(--success, var(--accent))', g: 'X', label: 'factor' },
  // 监控 / 外部观察层 (可观测层重构方案 §7.7)。
  monitor: { c: 'var(--warn)', g: 'M', label: 'monitor' },
};

// Source mark — F (frontend) · B (backend) · D (data) · X (factor).
export function SrcMark({ src, full }) {
  const m = SRC_META[src] || SRC_META.backend;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontFamily: 'var(--font-mono)', fontSize: 10.5 }}>
      <span style={{
        width: 14, height: 14, background: 'var(--surface-2)', border: `1px solid ${m.c}`,
        color: m.c, fontWeight: 600, fontSize: 9, lineHeight: '12px',
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center', borderRadius: 2,
      }}>{m.g}</span>
      {full && <span style={{ color: 'var(--fg-2)' }}>{m.label}</span>}
    </span>
  );
}

// trace_id short code with a copy/jump affordance.
export function TraceShort({ id, onClick }) {
  if (!id) return <span style={{ color: 'var(--fg-mute)' }}>—</span>;
  const short = id.replace(/^t_/, '').slice(-6);
  return (
    <span onClick={onClick} style={{
      display: 'inline-flex', alignItems: 'center', gap: 4, fontFamily: 'var(--font-mono)',
      fontSize: 10.5, color: 'var(--info)', cursor: onClick ? 'pointer' : 'default',
    }}>
      <span style={{ color: 'var(--fg-mute)' }}>t_</span><span>{short}</span>
      <span style={{ color: 'var(--fg-mute)', fontSize: 9 }}>⎘</span>
    </span>
  );
}

// KPI cell used by LogsHub's top band.
export function KpiCell({ label, value, sub, tone, trend, accent, last }) {
  const col = { ok: 'var(--ok)', warn: 'var(--warn)', err: 'var(--err)', info: 'var(--info)' }[tone] || 'var(--fg)';
  return (
    <div style={{
      padding: '10px 16px', borderRight: last ? 'none' : '1px solid var(--border-faint)',
      display: 'flex', flexDirection: 'column', justifyContent: 'space-between', minHeight: 70,
      background: accent ? 'var(--err-bg)' : 'transparent',
    }}>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{label}</span>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span data-kpi style={{ fontFamily: 'var(--font-mono)', fontSize: 24, fontWeight: 500, color: col, fontVariantNumeric: 'tabular-nums', lineHeight: 1 }}>{value}</span>
        {trend && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: trend.startsWith('+') ? 'var(--err)' : trend.startsWith('-') ? 'var(--ok)' : 'var(--fg-3)' }}>{trend}</span>}
      </div>
      {sub && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>{sub}</span>}
    </div>
  );
}

// A bar's local Date from its { hour } | { ts } field (app_log/UTC convention → +Z).
function barDate(d) {
  const v = d?.hour ?? d?.ts;
  if (v == null) return null;
  const s = typeof v === 'string' && !(v.endsWith('Z') || v.includes('+')) ? `${v}Z` : v;
  const t = new Date(s);
  return isNaN(t.getTime()) ? null : t;
}
const _pad2 = (n) => String(n).padStart(2, '0');
const _md = (t) => `${_pad2(t.getMonth() + 1)}-${_pad2(t.getDate())}`;

// Stacked-by-level histogram. data: [{ hour, info, warn, err }].
// When the bars carry a `hour`/`ts` stamp we also draw a time axis (§5.7):
// an HH:00 tick every 6 bars, a bold dashed midnight divider where the local
// calendar day rolls over, and a date-range caption — so bars map onto a day.
export function StackedBars({ data, width = 720, height = 130 }) {
  const max = Math.max(1, ...data.map((d) => (d.info || 0) + (d.warn || 0) + (d.err || 0)));
  const bw = data.length ? width / data.length : width;
  const gap = Math.max(1, bw * 0.18);
  const inner = bw - gap;

  const times = data.map(barDate);
  const hasTime = times.some(Boolean);
  const capH = hasTime ? 13 : 0;     // top: date-range caption
  const axisH = hasTime ? 14 : 0;    // bottom: HH:00 ticks
  const plotTop = capH;
  const plotH = Math.max(1, height - capH - axisH);
  const baseY = plotTop + plotH;

  // date-range caption (first → last bar's local day; collapse when single day)
  const firstT = times.find(Boolean);
  const lastT = [...times].reverse().find(Boolean);
  const caption = firstT && lastT
    ? (_md(firstT) === _md(lastT) ? _md(firstT) : `${_md(firstT)} → ${_md(lastT)}`)
    : null;

  return (
    <svg width={width} height={height} className="spark" style={{ display: 'block' }}>
      {[0.25, 0.5, 0.75].map((t) => (
        <line key={t} x1={0} x2={width} y1={plotTop + plotH * t} y2={plotTop + plotH * t} stroke="var(--border-faint)" strokeWidth={1} />
      ))}
      {data.map((d, i) => {
        const x = i * bw + gap / 2;
        const ie = (d.info || 0) / max * plotH;
        const we = (d.warn || 0) / max * plotH;
        const ee = (d.err || 0) / max * plotH;
        let y = baseY;
        const segs = [];
        if (d.info) { y -= ie; segs.push(<rect key="i" x={x} y={y} width={inner} height={ie} fill="var(--info)" opacity="0.55" />); }
        if (d.warn) { y -= we; segs.push(<rect key="w" x={x} y={y} width={inner} height={we} fill="var(--warn)" />); }
        if (d.err) { y -= ee; segs.push(<rect key="e" x={x} y={y} width={inner} height={ee} fill="var(--err)" />); }
        return <g key={i}>{segs}</g>;
      })}

      {/* §5.7 time axis — ticks, midnight divider, caption */}
      {hasTime && (
        <g>
          {caption && (
            <text x={2} y={9} fill="var(--fg-3)" fontSize={9} fontFamily="var(--font-mono)">{caption}</text>
          )}
          {times.map((t, i) => {
            if (!t) return null;
            const nodes = [];
            // hour tick label every 6 bars (first tick anchored left so it can't clip)
            if (i % 6 === 0) {
              const firstTick = i === 0;
              nodes.push(
                <text key={`t${i}`} x={firstTick ? 2 : i * bw + bw / 2} y={height - 3} textAnchor={firstTick ? 'start' : 'middle'}
                  fill="var(--fg-3)" fontSize={9} fontFamily="var(--font-mono)">{`${_pad2(t.getHours())}:00`}</text>,
              );
            }
            // bold dashed midnight divider when the local calendar day rolls over
            // (compare full Y-M-D, not day-of-month — safe for coarser buckets too)
            const prev = times[i - 1];
            if (prev && (prev.getFullYear() !== t.getFullYear() || prev.getMonth() !== t.getMonth() || prev.getDate() !== t.getDate())) {
              const dx = i * bw;
              nodes.push(<line key={`m${i}`} x1={dx} x2={dx} y1={plotTop} y2={baseY} stroke="var(--fg-3)" strokeWidth={1.5} strokeDasharray="3 2" opacity={0.8} />);
              nodes.push(
                <text key={`ml${i}`} x={dx + 2} y={plotTop + 9} fill="var(--fg-2)" fontSize={9} fontFamily="var(--font-mono)" fontWeight={600}>{_md(t)}</text>,
              );
            }
            return <g key={`ax${i}`}>{nodes}</g>;
          })}
        </g>
      )}
    </svg>
  );
}

export function fmtTs(ts) {
  if (!ts) return '—';
  // app_log ts is naive UTC; show local wall time, full ts on hover
  const s = String(ts).endsWith('Z') || String(ts).includes('+') ? ts : `${ts}Z`;
  return absTime(s);
}

export function fmtTsTime(ts) {
  const full = fmtTs(ts);
  return full === '—' ? '—' : full.slice(11); // HH:MM:SS
}

export function relTimeOf(ts) {
  if (!ts) return '—';
  const s = String(ts).endsWith('Z') || String(ts).includes('+') ? ts : `${ts}Z`;
  return relTime(s);
}

// 模式 A 的日界分隔行 (§4)。占整行宽度,放在跨日的两行之间。
export function DayDivider({ label, colSpan }) {
  return (
    <tr aria-hidden="true" data-testid="day-divider">
      <td colSpan={colSpan} style={{
        padding: '2px 10px', fontFamily: 'var(--font-mono)', fontSize: 10.5,
        color: 'var(--fg-3)', background: 'var(--surface-2)',
        borderTop: '1px solid var(--border-faint)', borderBottom: '1px solid var(--border-faint)',
      }}>── {label} {'─'.repeat(10)}</td>
    </tr>
  );
}

// The dense list table shared by AppLogs + ClientLogs.
export function LogTable({ rows, selectedId, onSelect, emptyLabel = '无匹配日志' }) {
  if (!rows.length) {
    return <div style={{ padding: 40, textAlign: 'center', color: 'var(--fg-3)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>{emptyLabel}</div>;
  }
  const head = ['time', 'lvl', 'src', 'logger', 'event', 'route', 'actor', 'dur', 'trace', ''];
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
      <thead style={{ position: 'sticky', top: 0, background: 'var(--surface)', zIndex: 1 }}>
        <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
          {head.map((c, i) => (
            <th key={i} style={{
              textAlign: c === 'dur' ? 'right' : 'left', padding: '5px 10px', fontSize: 9,
              color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase',
              letterSpacing: '0.05em', whiteSpace: 'nowrap',
            }}>{c}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r, i) => {
          const sel = r.id === selectedId;
          const errish = r.level === 'ERROR' || r.level === 'CRITICAL';
          const divider = dayBoundaryLabel(rows[i - 1]?.ts, r.ts);
          return (
            <React.Fragment key={r.id ?? i}>
              {divider && <DayDivider label={divider} colSpan={head.length} />}
              <tr onClick={() => onSelect?.(r)} data-testid="log-row" style={{
                height: 24, cursor: 'pointer', borderBottom: '1px solid var(--border-faint)',
                background: sel ? 'var(--info-bg)' : (i % 2 === 0 ? 'transparent' : 'rgba(0,0,0,0.012)'),
              }}>
                <td title={fmtTs(r.ts)} style={{ padding: '0 10px', color: 'var(--fg-3)', fontSize: 10.5, whiteSpace: 'nowrap' }}>{fmtTsTime(r.ts)}</td>
                <td style={{ padding: '0 10px' }}><LvlChip lvl={r.level} mini /></td>
                <td style={{ padding: '0 10px' }}><SrcMark src={r.source} /></td>
                <td style={{ padding: '0 10px', color: 'var(--fg-3)', fontSize: 10.5, whiteSpace: 'nowrap' }}>{r.logger}</td>
                <td style={{ padding: '0 10px', color: errish ? 'var(--fg)' : 'var(--fg-2)', fontWeight: errish ? 500 : 400 }}>{r.event}</td>
                <td style={{ padding: '0 10px', color: 'var(--fg-3)', fontSize: 10.5, maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.route || '—'}</td>
                <td style={{ padding: '0 10px', color: r.actor === 'system' ? 'var(--info)' : 'var(--fg-2)' }}>{r.actor || '—'}</td>
                <td style={{ padding: '0 10px', textAlign: 'right', color: r.duration_ms > 1000 ? 'var(--warn)' : 'var(--fg-2)', fontVariantNumeric: 'tabular-nums' }}>{r.duration_ms != null ? `${r.duration_ms}ms` : '—'}</td>
                <td style={{ padding: '0 10px' }}><TraceShort id={r.trace_id} /></td>
                <td style={{ padding: '0 10px', textAlign: 'right', color: 'var(--info)', fontSize: 10 }}>{sel ? '◀' : 'trace →'}</td>
              </tr>
            </React.Fragment>
          );
        })}
      </tbody>
    </table>
  );
}

// Detail panel body shared by AppLogs + ClientLogs (KV + message + extra + exception).
export function LogDetail({ row, onTrace }) {
  if (!row) {
    return <div style={{ padding: 40, textAlign: 'center', color: 'var(--fg-3)', fontSize: 11, fontFamily: 'var(--font-mono)' }}>← 点击左侧行查看详情</div>;
  }
  const kv = [
    ['id', row.id],
    ['ts', fmtTs(row.ts)],
    ['logger', row.logger],
    ['trace_id', row.trace_id || '—'],
    ['request_id', row.request_id || '—'],
    ['session_id', row.session_id || '—'],
    ['actor', row.actor || '—'],
    ['route', row.route ? `${row.method || ''} ${row.route}`.trim() : '—'],
    ['status_code', row.status_code ?? '—'],
    ['duration_ms', row.duration_ms != null ? `${row.duration_ms}` : '—'],
    ['host', row.host || '—'],
  ];
  return (
    <div style={{ padding: '14px 16px', fontFamily: 'var(--font-mono)', fontSize: 11.5, height: '100%', overflow: 'auto' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
        <LvlChip lvl={row.level} /><SrcMark src={row.source} full />
        <span style={{ color: 'var(--fg)', fontWeight: 500 }}>{row.event}</span>
      </div>
      <SectionLabel>BASIC</SectionLabel>
      <div style={{ display: 'grid', gridTemplateColumns: '110px 1fr', gap: 4, fontSize: 11 }}>
        {kv.map(([k, v]) => (
          <React.Fragment key={k}>
            <span style={{ color: 'var(--fg-3)' }}>{k}</span>
            <span style={{ color: 'var(--fg)', wordBreak: 'break-all' }}>{String(v)}</span>
          </React.Fragment>
        ))}
      </div>
      {row.message && (<>
        <SectionLabel>MESSAGE</SectionLabel>
        <div style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3, padding: 10, color: 'var(--fg)' }}>{row.message}</div>
      </>)}
      {row.extra && (<>
        <SectionLabel>EXTRA · JSON</SectionLabel>
        <pre style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3, padding: 10, fontSize: 10.5, whiteSpace: 'pre-wrap', wordBreak: 'break-all', color: 'var(--fg-2)', margin: 0 }}>{JSON.stringify(row.extra, null, 2)}</pre>
      </>)}
      {row.exc_type && (<>
        <div style={{ fontSize: 9.5, color: 'var(--err)', textTransform: 'uppercase', letterSpacing: '0.06em', margin: '14px 0 6px' }}>EXCEPTION</div>
        <div style={{ background: 'var(--err-bg)', border: '1px solid var(--err)', borderRadius: 3, padding: 10, fontSize: 10.5 }}>
          <div style={{ display: 'grid', gridTemplateColumns: '110px 1fr', gap: 4, marginBottom: 8 }}>
            <span style={{ color: 'var(--fg-3)' }}>exc_type</span><span style={{ color: 'var(--err)', fontWeight: 600 }}>{row.exc_type}</span>
            <span style={{ color: 'var(--fg-3)' }}>exc_message</span><span style={{ color: 'var(--fg)' }}>{row.exc_message}</span>
          </div>
          {row.stack && <pre style={{ color: 'var(--fg-2)', whiteSpace: 'pre-wrap', fontSize: 10, lineHeight: 1.55, margin: 0 }}>{row.stack}</pre>}
        </div>
      </>)}
      <div style={{ display: 'flex', gap: 6, marginTop: 18, flexWrap: 'wrap' }}>
        {row.trace_id && <button className="btn sm" style={{ background: 'var(--fg)', color: 'var(--bg)', borderColor: 'var(--fg)' }} onClick={() => onTrace?.(row.trace_id)}>查看本 trace 链路 →</button>}
        <button className="btn ghost sm" onClick={() => navigator.clipboard?.writeText(JSON.stringify(row, null, 2))}>⎘ 复制完整 JSON</button>
        {row.trace_id && <button className="btn ghost sm" onClick={() => navigator.clipboard?.writeText(row.trace_id)}>⎘ trace_id</button>}
      </div>
    </div>
  );
}

function SectionLabel({ children }) {
  return <div style={{ fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', margin: '14px 0 6px' }}>{children}</div>;
}

// Detail header strip used above LogDetail in the split views.
export function DetailHeader({ label, meta }) {
  return (
    <div style={{
      padding: '6px 12px', background: 'var(--surface-2)', borderBottom: '1px solid var(--border)',
      display: 'flex', alignItems: 'center', gap: 10, height: 28, fontFamily: 'var(--font-mono)',
      fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em', flexShrink: 0,
    }}>
      <span style={{ color: 'var(--fg)', fontWeight: 500 }}>{label}</span>
      {meta && <span style={{ color: 'var(--fg-3)' }}>· {meta}</span>}
    </div>
  );
}
