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

// ═════════════════════════════════════════════════════════════════════════════
// 改版新增 (可观测层重构方案 §7A) · 跨页面复用元素 — port of pages-observ-shared
// ═════════════════════════════════════════════════════════════════════════════

// ── threshold helpers (沿用现有健康三态色) ───────────────────────────────────
export function resTone(pct, warnAt = 75, dangerAt = 90) {
  return pct >= dangerAt ? 'err' : pct >= warnAt ? 'warn' : 'ok';
}
export function toneColor(t) {
  return t === 'err' ? 'var(--err)' : t === 'warn' ? 'var(--warn)' : t === 'ok' ? 'var(--ok)'
    : t === 'info' ? 'var(--info)' : 'var(--fg)';
}

// ── §1·三类资源 (新) · 磁盘 / CPU / 崩溃转储 — 横切资源, 不是「第六个层」 ────────
export const OBS_RES = {
  disk: { label: '磁盘', en: 'disk' },
  cpu: { label: 'CPU', en: 'cpu' },
  dump: { label: '崩溃转储', en: 'core-dump' },
};
export function ResGlyph({ res, size = 13 }) {
  const p = { width: size, height: size, viewBox: '0 0 16 16', fill: 'none', stroke: 'currentColor', strokeWidth: 1.4, strokeLinecap: 'round', strokeLinejoin: 'round' };
  const G = {
    disk: <svg {...p}><ellipse cx="8" cy="3.6" rx="5" ry="2" /><path d="M3 3.6v8.8c0 1.1 2.2 2 5 2s5-.9 5-2V3.6" /><path d="M3 8c0 1.1 2.2 2 5 2s5-.9 5-2" /></svg>,
    cpu: <svg {...p}><rect x="4.2" y="4.2" width="7.6" height="7.6" rx="1" /><rect x="6.4" y="6.4" width="3.2" height="3.2" rx="0.4" /><path d="M6.4 4.2V2M9.6 4.2V2M6.4 13.8v-2M9.6 13.8v-2M4.2 6.4H2M4.2 9.6H2M13.8 6.4h-2M13.8 9.6h-2" /></svg>,
    dump: <svg {...p}><rect x="3" y="3" width="10" height="10" rx="1" /><path d="M9 3l-1.6 4.2 2.6.7-2 5.1" /></svg>,
  };
  return G[res] || <svg {...p}><circle cx="8" cy="8" r="5" /></svg>;
}
// labeled resource mark + threshold tone (告警行 / 拓扑 / 总览 复用)
export function ObsResMark({ res, tone, full, size = 13, style }) {
  const r = OBS_RES[res] || OBS_RES.disk;
  const col = tone ? toneColor(tone) : 'var(--fg-2)';
  return (
    <span title={r.label} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: col, fontFamily: 'var(--font-mono)', fontSize: 10.5, ...style }}>
      <ResGlyph res={res} size={size} />
      {full && <span style={{ color: tone ? col : 'var(--fg-2)' }}>{r.label}</span>}
    </span>
  );
}

// ── §3·横向资源水位条 (磁盘水位 / CPU 占用) ──────────────────────────────────
export function ObsMeter({ pct = 0, warnAt = 75, dangerAt = 90, label, sub, mount, killed, width }) {
  const tone = killed ? 'err' : resTone(pct, warnAt, dangerAt);
  const col = toneColor(tone);
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontFamily: 'var(--font-mono)', fontSize: 11, width }}>
      {label && <span style={{ color: 'var(--fg-2)', width: 92, flexShrink: 0 }}>{label}{mount && <span style={{ color: 'var(--fg-4)' }}> {mount}</span>}</span>}
      <div style={{ flex: 1, height: 8, background: 'var(--surface-3)', borderRadius: 2, position: 'relative', overflow: 'hidden', minWidth: 60 }}>
        <div style={{ position: 'absolute', inset: '0 auto 0 0', width: `${killed ? 0 : Math.min(pct, 100)}%`, background: col, transition: 'width 0.4s' }} />
        <div style={{ position: 'absolute', top: 0, bottom: 0, left: `${dangerAt}%`, width: 1, background: 'var(--border-strong)' }} />
      </div>
      <span style={{ color: killed ? 'var(--err)' : col, fontWeight: tone === 'ok' ? 500 : 600, fontVariantNumeric: 'tabular-nums', width: 46, textAlign: 'right', flexShrink: 0 }}>{killed ? 'killed' : `${pct}%`}</span>
      {sub && <span style={{ color: 'var(--fg-3)', fontSize: 10, width: 86, textAlign: 'right', flexShrink: 0 }}>{sub}</span>}
    </div>
  );
}

// ── §3.4·检测方标签 (新) · 外层监控(哨兵) / 内部监控(后端) ───────────────────
// external = 哨兵 (obs-sentinel, docker.sock+statvfs 直采); internal = 后端评估器 (日志派生)。
export function ObsDetector({ kind = 'internal', mini }) {
  const ext = kind === 'external' || kind === 'sentinel';
  return (
    <span title={ext ? '外层监控 · 哨兵 (obs-sentinel) 经 docker.sock + statvfs 直采' : '内部监控 · 后端评估器 · 日志派生'} style={{
      display: 'inline-flex', alignItems: 'center', gap: 3,
      padding: mini ? '0 4px' : '0 6px', height: mini ? 14 : 16, borderRadius: 3,
      fontFamily: 'var(--font-mono)', fontSize: mini ? 9 : 9.5, fontWeight: 500, whiteSpace: 'nowrap',
      border: `1px solid ${ext ? 'var(--info)' : 'var(--border-strong)'}`,
      background: ext ? 'var(--info-bg)' : 'transparent',
      color: ext ? 'var(--info)' : 'var(--fg-3)',
    }}>
      <span style={{ fontSize: mini ? 8 : 9, opacity: 0.85 }}>{ext ? '⊚' : '·'}</span>
      {ext ? '外层' : '内部'}{!mini && '监控'}
    </span>
  );
}

// ── §3.1·实时状态指示 (新) · 实时 / 延迟 / 中断 ──────────────────────────────
export const OBS_RT = {
  live: { c: 'var(--ok)', txt: '实时', en: 'LIVE', pulse: true },
  delayed: { c: 'var(--warn)', txt: '延迟', en: '', pulse: false },
  stale: { c: 'var(--err)', txt: '中断', en: 'STALE', pulse: false },
};
export function ObsRealtime({ state = 'live', detail, mini }) {
  const m = OBS_RT[state] || OBS_RT.live;
  return (
    <span title={`实时状态 · ${m.txt}`} style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      padding: mini ? '1px 7px' : '2px 9px', borderRadius: 11, lineHeight: '15px',
      border: `1px solid ${state === 'live' ? 'var(--border)' : m.c}`,
      background: state === 'live' ? 'transparent' : `color-mix(in oklch, ${m.c} 12%, transparent)`,
      color: m.c, fontFamily: 'var(--font-mono)', fontSize: mini ? 10 : 10.5, fontWeight: 500,
    }}>
      <span style={{
        width: state === 'stale' ? 7 : 6, height: state === 'stale' ? 7 : 6, flexShrink: 0,
        borderRadius: state === 'stale' ? 1 : '50%', background: state === 'stale' ? 'transparent' : m.c,
        border: state === 'stale' ? `1.5px solid ${m.c}` : 'none',
        animation: m.pulse ? 'pulse 1.6s ease-in-out infinite' : 'none',
      }} />
      <span>{m.txt}</span>
      {(detail || m.en) && <span style={{ color: state === 'live' ? 'var(--fg-4)' : m.c, fontWeight: 400, fontSize: mini ? 9 : 9.5, letterSpacing: '0.04em' }}>{detail || m.en}</span>}
    </span>
  );
}

// ── §3.2·「核心服务不可达」降级横幅 (新) ─────────────────────────────────────
export function ObsDegradeBanner({ scope = 'overview' }) {
  const note = scope === 'topology'
    ? '以下为各节点实时存活态(简版地图)· 容器明细 / 历史告警暂不可用'
    : '以下为各层实时存活与资源状态 · 告警历史 / 日志 / 任务暂不可用';
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12, padding: '9px 16px',
      background: 'var(--err-bg)', borderBottom: '1px solid var(--err)',
      fontFamily: 'var(--font-mono)', fontSize: 11.5, flexShrink: 0,
    }}>
      <span style={{
        width: 20, height: 20, borderRadius: '50%', flexShrink: 0,
        background: 'var(--err)', color: 'var(--surface)',
        display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 12, fontWeight: 700,
        animation: 'pulse 1.6s ease-in-out infinite',
      }}>✕</span>
      <span style={{ color: 'var(--err)', fontWeight: 600 }}>后端不可达</span>
      <span style={{ color: 'var(--fg-mute)' }}>│</span>
      <span style={{ color: 'var(--fg-2)' }}>{note}</span>
      <span style={{ flex: 1 }} />
      <ObsRealtime state="delayed" detail="后端故障" mini />
    </div>
  );
}

// ── §6·空态 (新) · 后端故障 / 无数据 ─────────────────────────────────────────
export function ObsEmptyState({ kind = 'offline', title, sub, hint, onGoOverview }) {
  const offline = kind === 'offline';
  const col = offline ? 'var(--err)' : 'var(--fg-3)';
  return (
    <div style={{
      flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center', gap: 14, padding: 40,
      background: 'var(--bg)', fontFamily: 'var(--font-mono)', textAlign: 'center',
    }}>
      <div style={{
        width: 56, height: 56, borderRadius: '50%',
        border: `1.5px solid ${offline ? 'var(--err)' : 'var(--border-strong)'}`,
        background: offline ? 'var(--err-bg)' : 'var(--surface-2)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 26, color: col,
      }}>{offline ? '✕' : '∅'}</div>
      <div style={{ fontSize: 15, color: 'var(--fg)', fontWeight: 600 }}>{title || (offline ? '后端不可达 · 本页内容暂不可用' : '当前无数据')}</div>
      <div style={{ fontSize: 11.5, color: 'var(--fg-3)', maxWidth: 380, lineHeight: 1.6 }}>
        {sub || (offline ? '本页内容依赖后端历史数据。后端恢复后将自动加载。' : '没有匹配当前筛选条件的记录。')}
      </div>
      {offline && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 2 }}>
          <span style={{ fontSize: 11, color: 'var(--fg-2)' }}>{hint || '系统实时状态请见'}</span>
          <button className="btn sm" style={{ height: 24 }} onClick={onGoOverview}>进入「总览」→</button>
        </div>
      )}
    </div>
  );
}

// ── 占位骨架 (加载中) — §6 各面板「加载中」态 ────────────────────────────────
export function ObsSkeleton({ rows = 3, w = '100%', h = 12, style }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, ...style }}>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} style={{
          height: h, width: typeof w === 'function' ? w(i) : w, borderRadius: 3,
          background: 'linear-gradient(90deg, var(--surface-2) 25%, var(--surface-3) 37%, var(--surface-2) 63%)',
          backgroundSize: '200% 100%', animation: 'sk-shimmer 1.4s ease-in-out infinite',
        }} />
      ))}
    </div>
  );
}
