// Shared primitives for the two new viz workbenches (市场全景 / 行业视图).
// Ported from the Claude Design handoff (pages-viz-shared.jsx): CoverageBadge,
// SnapshotTag / TimeseriesTag, the 5-state PanelState matrix, MiniToggle, the
// VizPanel chrome, plus the红涨/绿跌 series palette and a market-cap formatter.
// The fake-SVG charts from the prototype are intentionally NOT ported — the real
// pages feed live data into <EChart> instead.
import React from 'react';

// red 涨 / green 跌 (China convention), matching the design palette.
export const VIZ_SERIES = [
  'oklch(0.58 0.18 27)',   // red
  'oklch(0.55 0.13 248)',  // blue
  'oklch(0.6 0.13 145)',   // green
  'oklch(0.66 0.13 70)',   // amber
  'oklch(0.55 0.14 305)',  // violet
];
export const VIZ_UP = 'oklch(0.58 0.18 27)';    // 涨 = 红
export const VIZ_DOWN = 'oklch(0.6 0.13 145)';  // 跌 = 绿

// ── money / number formatters ────────────────────────────────────────────────
export function fmtCap(v) {
  if (v == null || Number.isNaN(v)) return '—';
  const a = Math.abs(v);
  if (a >= 1e12) return `¥${(v / 1e12).toFixed(2)}万亿`;
  if (a >= 1e8) return `¥${(v / 1e8).toFixed(v / 1e8 >= 100 ? 0 : 1)}亿`;
  if (a >= 1e4) return `¥${(v / 1e4).toFixed(0)}万`;
  return `¥${Number(v).toFixed(0)}`;
}

export function fmtNum(v, digits = 0) {
  if (v == null || Number.isNaN(v)) return '—';
  return Number(v).toLocaleString('en-US', { maximumFractionDigits: digits });
}

export function fmtPct(v, digits = 1) {
  if (v == null || Number.isNaN(v)) return '—';
  return `${Number(v).toFixed(digits)}%`;
}

// ── Data-coverage corner badge (§8.4: never silently画图覆盖不足) ───────────────
export function CoverageBadge({ covered = 0, total = 0, unit = '只', onViewMissing, testId = 'coverage-badge' }) {
  const partial = covered < total;
  const pct = total > 0 ? (covered / total) * 100 : 100;
  return (
    <span data-testid={testId} data-partial={partial} style={{
      display: 'inline-flex', alignItems: 'center', gap: 6,
      fontFamily: 'var(--font-mono)', fontSize: 9.5,
      padding: '1px 7px', borderRadius: 3,
      background: partial ? 'var(--warn-bg)' : 'transparent',
      border: `1px solid ${partial ? 'var(--warn)' : 'var(--border)'}`,
      color: partial ? 'var(--warn)' : 'var(--fg-3)',
      cursor: partial && onViewMissing ? 'pointer' : 'default',
    }} onClick={partial && onViewMissing ? onViewMissing : undefined}>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
        {partial ? <span>⚠ 覆盖不足</span> : <span style={{ color: 'var(--ok)' }}>● 覆盖完整</span>}
        <span style={{ color: partial ? 'var(--warn)' : 'var(--fg-2)' }}>
          {covered.toLocaleString()}
          <span style={{ color: 'var(--fg-mute)' }}> / {total.toLocaleString()} {unit}</span>
        </span>
        <span style={{ color: partial ? 'var(--warn)' : 'var(--fg-4)' }}>{pct.toFixed(pct < 99.95 ? 1 : 0)}%</span>
      </span>
      {partial && onViewMissing && <span style={{ textDecoration: 'underline', textUnderlineOffset: 2 }}>查看缺失 ↗</span>}
    </span>
  );
}

export function SnapshotTag({ date }) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 5,
      fontFamily: 'var(--font-mono)', fontSize: 9.5, padding: '1px 7px', borderRadius: 3,
      background: 'var(--info-bg)', color: 'var(--info)', border: '1px solid var(--info)',
    }}>
      <span>◷ 快照</span><span style={{ color: 'var(--fg)' }}>{date || '—'}</span>
    </span>
  );
}

export function TimeseriesTag() {
  return (
    <span style={{
      fontFamily: 'var(--font-mono)', fontSize: 9.5, padding: '1px 7px', borderRadius: 3,
      background: 'var(--surface-2)', color: 'var(--fg-3)', border: '1px solid var(--border)',
    }}>↔ 时序</span>
  );
}

// ── 5-state panel placeholder (§6: idle/loading/empty/error + partial via badge) ─
function Skeleton() {
  return (
    <div style={{ width: '70%', maxWidth: 220, display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 4 }}>
      {[0.9, 0.7, 0.55, 0.8].map((w, i) => (
        <div key={i} style={{
          height: 8, width: `${w * 100}%`, borderRadius: 3,
          background: 'linear-gradient(90deg, var(--surface-2) 25%, var(--surface-3) 50%, var(--surface-2) 75%)',
          backgroundSize: '200% 100%', animation: 'sk-shimmer 1.4s ease-in-out infinite',
        }} />
      ))}
    </div>
  );
}

export function PanelState({ kind, title, hint, actionLabel, onAction, testId }) {
  const cfg = {
    idle: { icon: '◌', color: 'var(--fg-4)', t: title || '未选择', h: hint || '先在上方选择条件以加载图表' },
    loading: { icon: '◴', color: 'var(--info)', t: title || '加载中', h: hint || '正在取数…' },
    empty: { icon: '∅', color: 'var(--fg-3)', t: title || '空数据', h: hint || '该条件下无数据' },
    error: { icon: '⚠', color: 'var(--err)', t: title || '取数失败', h: hint || '后端返回错误,请重试' },
  }[kind] || {};
  return (
    <div data-testid={testId || `panel-state-${kind}`} data-kind={kind} style={{
      height: '100%', width: '100%', minHeight: 80,
      display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
      gap: 6, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)', textAlign: 'center', padding: 16,
    }}>
      {kind === 'loading'
        ? <Skeleton />
        : <span style={{ fontSize: 22, color: cfg.color }}>{cfg.icon}</span>}
      <span style={{ fontSize: 11.5, color: kind === 'error' ? 'var(--err)' : 'var(--fg-2)', fontWeight: 500 }}>{cfg.t}</span>
      <span style={{ fontSize: 10, color: 'var(--fg-3)', maxWidth: 240, lineHeight: 1.5 }}>{cfg.h}</span>
      {(actionLabel || kind === 'error') && (
        <button className="btn ghost sm" onClick={onAction}
          style={{ marginTop: 4, fontSize: 10.5, borderColor: 'var(--border-strong)' }}>
          {actionLabel || '↻ 重试'}
        </button>
      )}
    </div>
  );
}

// ── tiny inline segmented toggle (总市值/流通市值, 分布/排名 …) ────────────────
export function MiniToggle({ options, active, onChange, height = 18, testIdPrefix }) {
  return (
    <div style={{ display: 'inline-flex', border: '1px solid var(--border-strong)', borderRadius: 3, overflow: 'hidden' }}>
      {options.map((o, i, a) => {
        const v = typeof o === 'string' ? o : o.value;
        const l = typeof o === 'string' ? o : o.label;
        return (
          <button key={v} onClick={() => onChange && onChange(v)}
            data-testid={testIdPrefix ? `${testIdPrefix}-${v}` : undefined}
            style={{
            height, padding: '0 7px',
            background: v === active ? 'var(--fg)' : 'transparent',
            color: v === active ? 'var(--bg)' : 'var(--fg-2)',
            border: 'none', borderRight: i < a.length - 1 ? '1px solid var(--border-strong)' : 'none',
            fontFamily: 'var(--font-mono)', fontSize: 9.5, cursor: 'pointer',
          }}>{l}</button>
        );
      })}
    </div>
  );
}

// ── segmented range buttons (1M/3M/6M/1Y/3Y/ALL …) ───────────────────────────
export function RangeButtons({ options, active, onChange, height = 26, testIdPrefix }) {
  return (
    <div style={{ display: 'inline-flex', border: '1px solid var(--border-strong)', borderRadius: 3, overflow: 'hidden' }}>
      {options.map((p, i, a) => (
        <button key={p} onClick={() => onChange(p)}
          data-testid={testIdPrefix ? `${testIdPrefix}-${p}` : undefined}
          style={{
          height, padding: '0 9px',
          background: p === active ? 'var(--fg)' : 'transparent',
          color: p === active ? 'var(--bg)' : 'var(--fg-2)',
          border: 'none', borderRight: i < a.length - 1 ? '1px solid var(--border-strong)' : 'none',
          fontFamily: 'var(--font-mono)', fontSize: 10.5, cursor: 'pointer',
        }}>{p}</button>
      ))}
    </div>
  );
}

// ── panel chrome shared by both pages ────────────────────────────────────────
// header: ⓝ TITLE en [tag] … [controls] [badge]; then the body (an EChart or a
// table); then an optional footer (hover / latest summary line).
export function VizPanel({ n, title, en, tag, badge, controls, footer, minHeight, flex, style, bodyStyle, children, testId }) {
  return (
    <section data-testid={testId} style={{
      background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 4,
      display: 'flex', flexDirection: 'column', minHeight: minHeight ?? 252, flex,
      minWidth: 0, ...style,
    }}>
      <header style={{
        padding: '6px 10px', borderBottom: '1px solid var(--border)',
        display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0, minHeight: 30,
      }}>
        {n && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--fg-3)' }}>{n}</span>}
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.04em' }}>{title}</span>
        {en && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-4)' }}>{en}</span>}
        {tag && <span data-testid={testId ? `${testId}-tag` : undefined}>{tag}</span>}
        <div style={{ flex: 1 }} />
        {controls}
        {badge}
      </header>
      <div style={{ flex: 1, minHeight: 0, position: 'relative', display: 'flex', flexDirection: 'column', ...bodyStyle }}>
        {children}
      </div>
      {footer != null && (
        <div style={{
          padding: '4px 10px', borderTop: '1px solid var(--border-faint)', flexShrink: 0,
          fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)',
          display: 'flex', alignItems: 'center', gap: 6, minHeight: 22,
        }}>
          <span style={{ color: 'var(--fg-4)' }}>⌖</span>
          <span style={{ color: 'var(--fg-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{footer}</span>
        </div>
      )}
    </section>
  );
}

// Decide which 5-state placeholder (if any) a panel should show.
export function panelStateOf(query) {
  if (!query) return null;
  if (query.isLoading || query.isPending) return 'loading';
  if (query.error) return 'error';
  return null;
}
