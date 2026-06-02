// 可观测 · 共享词汇 + 原子 (port of the Claude Design handoff pages-observ-shared,
// 可观测层重构方案 §7). One unified marker set reused across the 8 tabs + Trace.
import React from 'react';

// ── §1.1 · five layers (identity colours, kept distinct from status colours) ──
export const OBS_LAYERS = {
  frontend: { code: '前', en: 'frontend', label: '前端', c: 'oklch(0.58 0.11 250)' },
  backend: { code: '后', en: 'backend', label: '后端', c: 'oklch(0.55 0.05 230)' },
  data: { code: '数', en: 'data', label: '数据', c: 'oklch(0.52 0.08 65)' },
  factor: { code: '因', en: 'factor', label: '因子', c: 'oklch(0.53 0.12 300)' },
  monitor: { code: '监', en: 'monitor', label: '监控', c: 'oklch(0.56 0.09 195)' },
};

export function ObsLayerMark({ layer, full, size = 15 }) {
  const m = OBS_LAYERS[layer] || OBS_LAYERS.backend;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: 'var(--font-mono)', fontSize: 10.5 }}>
      <span style={{
        width: size, height: size, flexShrink: 0,
        background: `color-mix(in oklch, ${m.c} 12%, var(--surface))`,
        border: `1px solid ${m.c}`, color: m.c,
        fontWeight: 600, fontSize: size <= 14 ? 8.5 : 9.5, lineHeight: `${size - 2}px`,
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center', borderRadius: 2,
      }}>{m.code}</span>
      {full && <span style={{ color: 'var(--fg-2)' }}>{m.label}</span>}
    </span>
  );
}

// ── §1.3 · layer health · 正常 / 降级 / 故障 ─────────────────────────────────
export const OBS_HEALTH = {
  ok: { tone: 'ok', word: '正常', g: '●', col: 'var(--ok)' },
  degraded: { tone: 'warn', word: '降级', g: '◐', col: 'var(--warn)' },
  down: { tone: 'err', word: '故障', g: '✕', col: 'var(--err)' },
};

export function ObsHealthChip({ state = 'ok', mini }) {
  const h = OBS_HEALTH[state] || OBS_HEALTH.ok;
  return (
    <span className={`chip ${h.tone}`} style={{ fontSize: mini ? 9.5 : 10.5, height: mini ? 15 : 17 }}>
      <span style={{ opacity: 0.75 }}>{h.g}</span>{h.word}
    </span>
  );
}

export function ObsHealthDot({ state = 'ok', label, pulse }) {
  const h = OBS_HEALTH[state] || OBS_HEALTH.ok;
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: 'var(--font-mono)', fontSize: 10.5 }}>
      <span className={`dot ${h.tone === 'ok' ? 'ok' : h.tone === 'warn' ? 'warn' : 'err'}${pulse && state !== 'ok' ? ' running' : ''}`}
        style={{ margin: 0, background: h.col }} />
      {label && <span style={{ color: 'var(--fg-2)' }}>{label}</span>}
      <span style={{ color: h.col, fontWeight: 500 }}>{h.word}</span>
    </span>
  );
}

// ── §1.4 · alert lifecycle · 触发中 / 已确认 / 已解决 ─────────────────────────
export const OBS_ALERT = {
  firing: { tone: 'err', word: '触发中', g: '▲' },
  acked: { tone: 'warn', word: '已确认', g: '◑' },
  resolved: { tone: 'neutral', word: '已解决', g: '✓' },
};

export function ObsAlertChip({ state = 'firing', mini }) {
  const a = OBS_ALERT[state] || OBS_ALERT.firing;
  return (
    <span className={`chip ${a.tone}`} style={{
      fontSize: mini ? 9.5 : 10.5, height: mini ? 15 : 17,
      opacity: state === 'resolved' ? 0.7 : 1,
      fontWeight: state === 'firing' ? 600 : 500,
    }}>
      <span style={{ opacity: 0.8 }}>{a.g}</span>{a.word}
    </span>
  );
}

// ── §1.2 · severity (alerts use info|warn|error|critical) ────────────────────
export const OBS_SEV = {
  info: { tone: 'info', g: 'i', label: 'INFO' },
  warn: { tone: 'warn', g: '!', label: 'WARN' },
  error: { tone: 'err', g: '✕', label: 'ERROR' },
  critical: { tone: 'err', g: '✕✕', label: 'CRIT' },
};

export function ObsSev({ sev, mini }) {
  const m = OBS_SEV[sev] || OBS_SEV.info;
  return (
    <span className={`chip ${m.tone}`} style={{
      fontSize: mini ? 9 : 10, height: mini ? 14 : 16, padding: mini ? '0 4px' : '0 5px',
      fontWeight: sev === 'error' || sev === 'critical' ? 600 : 500,
    }}>
      <span style={{ opacity: 0.7 }}>{m.g}</span><span>{m.label}</span>
    </span>
  );
}

// ── §1.5 · job / task status ─────────────────────────────────────────────────
export const OBS_JOB = {
  pending: { tone: 'neutral', word: '排队', g: '◷' },
  running: { tone: 'info', word: '运行中', g: '◌', pulse: true },
  success: { tone: 'ok', word: '成功', g: '✓' },
  failed: { tone: 'err', word: '失败', g: '✕' },
  timeout: { tone: 'err', word: '超时', g: '⧖' },
  cancelled: { tone: 'neutral', word: '已取消', g: '⊘' },
};

export function ObsJobChip({ state = 'success', mini }) {
  const j = OBS_JOB[state] || OBS_JOB.success;
  return (
    <span className={`chip ${j.tone}`} style={{
      fontSize: mini ? 9.5 : 10.5, height: mini ? 15 : 17,
      fontWeight: j.tone === 'err' ? 600 : 500,
    }}>
      <span style={{ opacity: 0.8 }}>{j.g}</span>{j.word}
    </span>
  );
}

// ── small gauge ring (memory / occupancy) ────────────────────────────────────
export function ObsRing({ pct = 0, size = 34, warnAt = 70, dangerAt = 85, label }) {
  const r = (size - 5) / 2; const cx = size / 2; const cy = size / 2; const C = 2 * Math.PI * r;
  const col = pct >= dangerAt ? 'var(--err)' : pct >= warnAt ? 'var(--warn)' : 'var(--ok)';
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <svg width={size} height={size} style={{ display: 'block', transform: 'rotate(-90deg)' }}>
        <circle cx={cx} cy={cy} r={r} fill="none" stroke="var(--surface-3)" strokeWidth={3} />
        <circle cx={cx} cy={cy} r={r} fill="none" stroke={col} strokeWidth={3}
          strokeDasharray={`${(C * pct) / 100} ${C}`} strokeLinecap="round" />
      </svg>
      <span style={{ fontFamily: 'var(--font-mono)', fontVariantNumeric: 'tabular-nums', fontSize: 11, color: col, fontWeight: 500 }}>
        {pct}%{label && <span style={{ color: 'var(--fg-3)', fontWeight: 400, marginLeft: 2 }}>{label}</span>}
      </span>
    </span>
  );
}

// ── live "跳动" indicator ─────────────────────────────────────────────────────
export function ObsLive({ children }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <span style={{ width: 5, height: 5, borderRadius: '50%', background: 'var(--ok)', animation: 'pulse 1.6s ease-in-out infinite' }} />
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 8.5, color: 'var(--fg-4)', letterSpacing: '0.06em' }}>{children || 'LIVE'}</span>
    </span>
  );
}

// ── clickable / drill-down affordance ────────────────────────────────────────
export function ObsDrill({ children, onClick }) {
  return (
    <span onClick={onClick} style={{ color: 'var(--info)', fontFamily: 'var(--font-mono)', fontSize: 10, cursor: 'pointer', whiteSpace: 'nowrap' }}>
      {children} →
    </span>
  );
}

// ── small section label ──────────────────────────────────────────────────────
export function ObsKap({ children, style }) {
  return <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', ...style }}>{children}</span>;
}

// ── helpers ──────────────────────────────────────────────────────────────────
export function memPct(usage, limit) {
  if (typeof usage === 'number' && typeof limit === 'number' && limit > 0) {
    return Math.round((100 * usage) / limit);
  }
  return null;
}

export function fmtUptime(seconds) {
  if (seconds == null) return '—';
  const s = Math.max(0, Math.floor(seconds));
  const d = Math.floor(s / 86400); const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}d ${String(h).padStart(2, '0')}h`;
  if (h > 0) return `${h}h ${String(m).padStart(2, '0')}m`;
  return `${m}m`;
}

export function agoFrom(ts) {
  if (!ts) return '—';
  const s = String(ts).endsWith('Z') || String(ts).includes('+') ? ts : `${ts}Z`;
  const ms = Date.now() - (Date.parse(s) || Date.now());
  if (ms < 0) return '刚刚';
  const sec = Math.floor(ms / 1000);
  if (sec < 60) return `${sec}s 前`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m 前`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h 前`;
  return `${Math.floor(sec / 86400)}d 前`;
}
