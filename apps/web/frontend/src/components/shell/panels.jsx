// Reusable page primitives: TPanel, FilterBar, FInput, FSelect, FChip, Stat.
import React from 'react';
import { toneColor } from '../../lib/format';

export function TPanel({ title, meta, action, children, noBorderRight, noBorderBottom, last, style, headerExtra, padding, testId }) {
  return (
    <section data-testid={testId} style={{
      borderRight: last || noBorderRight ? 'none' : '1px solid var(--border)',
      borderBottom: noBorderBottom ? 'none' : '1px solid var(--border)',
      display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0,
      background: 'var(--surface)', ...style,
    }}>
      {title && (
        <header style={{
          padding: '6px 12px', background: 'var(--surface-2)', borderBottom: '1px solid var(--border)',
          display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0, minHeight: 28,
        }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{title}</span>
          {meta && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)' }}>· {meta}</span>}
          {headerExtra}
          <div style={{ flex: 1 }} />
          {action}
        </header>
      )}
      <div className="scroll" style={{ flex: 1, minHeight: 0, padding: padding != null ? padding : 0 }}>{children}</div>
    </section>
  );
}

export function FilterBar({ children, applied = [], onClear }) {
  return (
    <div style={{
      padding: '8px 14px', background: 'var(--surface)', borderBottom: '1px solid var(--border)',
      display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', flexShrink: 0,
      fontFamily: 'var(--font-mono)', fontSize: 11,
    }}>
      <span style={{ fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>filters</span>
      <span style={{ color: 'var(--fg-mute)' }}>│</span>
      {children}
      {applied.length > 0 && (
        <>
          <span style={{ flex: 1 }} />
          <span style={{ color: 'var(--fg-3)', fontSize: 10 }}>{applied.length} active</span>
          <button className="btn ghost sm" onClick={onClear} style={{ height: 20, fontSize: 10 }}>clear</button>
        </>
      )}
    </div>
  );
}

export function FInput({ label, placeholder, value, onChange, width = 160, onEnter, testId }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
      {label && <span style={{ color: 'var(--fg-3)', fontSize: 10 }}>{label}:</span>}
      <input
        data-testid={testId}
        placeholder={placeholder}
        value={value ?? ''}
        onChange={(e) => onChange && onChange(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && onEnter) onEnter(e.target.value); }}
        style={{
          width, height: 22, padding: '0 7px', background: 'var(--surface-2)',
          border: '1px solid var(--border)', borderRadius: 3, fontSize: 11,
          fontFamily: 'var(--font-mono)', color: 'var(--fg)',
        }}
      />
    </span>
  );
}

export function FSelect({ label, value, options = [], onChange, width = 120, testId }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
      {label && <span style={{ color: 'var(--fg-3)', fontSize: 10 }}>{label}:</span>}
      <select
        data-testid={testId}
        value={value}
        onChange={(e) => onChange && onChange(e.target.value)}
        style={{
          height: 22, padding: '0 7px', background: 'var(--surface-2)',
          border: '1px solid var(--border)', borderRadius: 3, fontSize: 11,
          fontFamily: 'var(--font-mono)', color: 'var(--fg)', minWidth: width, cursor: 'pointer',
        }}>
        {options.map((o) => {
          const v = typeof o === 'string' ? o : o.value;
          const l = typeof o === 'string' ? o : o.label;
          return <option key={v} value={v}>{l}</option>;
        })}
      </select>
    </span>
  );
}

export function FChip({ children, tone = 'neutral', onX }) {
  return (
    <span className={`chip ${tone}`} style={{ height: 20, fontSize: 10.5 }}>
      {children}
      {onX && <span style={{ marginLeft: 4, color: 'var(--fg-3)', cursor: 'pointer' }} onClick={onX}>✕</span>}
    </span>
  );
}

export function Stat({ label, value, sub, tone, mono = true }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{label}</span>
      <span style={{ fontFamily: mono ? 'var(--font-mono)' : 'inherit', fontVariantNumeric: 'tabular-nums', fontSize: 14, fontWeight: 500, color: toneColor(tone) }}>{value}</span>
      {sub && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)' }}>{sub}</span>}
    </div>
  );
}

// A row of KPI cells with the dense terminal styling used across pages.
export function KpiStrip({ cells, height = 56, cols }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: `repeat(${cols || cells.length}, 1fr)`,
      background: 'var(--surface)', borderBottom: '1px solid var(--border-strong)', height, flexShrink: 0,
    }}>
      {cells.map((c, i) => (
        <div key={i} data-testid={c.testId} style={{
          padding: '6px 14px', borderRight: i < cells.length - 1 ? '1px solid var(--border-faint)' : 'none',
          display: 'flex', flexDirection: 'column', justifyContent: 'space-between',
        }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.05em', display: 'flex', alignItems: 'center', gap: 4 }}>
            {c.pulse && <span className="dot running" style={{ width: 5, height: 5 }} />}{c.label}
          </span>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 19, fontWeight: 500, color: toneColor(c.tone), fontVariantNumeric: 'tabular-nums', lineHeight: 1 }}>{c.value}</span>
            {c.sub && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>{c.sub}</span>}
          </div>
        </div>
      ))}
    </div>
  );
}
