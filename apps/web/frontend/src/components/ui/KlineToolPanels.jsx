// Popover panels for the K-line workstation tool rail (整改方案 v1.0 §3.3–§3.4/§3.9).
// Everything here drives the KLineChart instance through the ref the <Kline />
// wrapper exposes — no chart logic is re-implemented.
import React from 'react';
import { t } from '../../lib/i18n';

// Built-in indicator names KLineChart ships with (整改方案 v1.0 §1.1.1).
export const MAIN_PANE_INDICATORS = ['MA', 'EMA', 'SMA', 'BBI', 'BOLL', 'SAR'];
export const SUB_PANE_INDICATORS = [
  'VOL', 'MACD', 'KDJ', 'RSI', 'WR', 'CCI', 'DMI',
  'BIAS', 'BRAR', 'CR', 'PSY', 'DMA', 'TRIX', 'OBV',
  'VR', 'MTM', 'EMV', 'AO', 'ROC', 'PVT',
];

// Built-in drawing overlays exposed in the palette (整改方案 v1.0 §1.1.2 / §3.4).
// A curated subset of KLineChart's overlay catalogue — the 12 most useful for
// price analysis (lines, channels, fibonacci, annotations/tags).
const DRAW_TOOLS = [
  { name: 'segment',                label: t('线段'),     icon: '╱' },
  { name: 'straightLine',           label: t('直线'),     icon: '─' },
  { name: 'rayLine',                label: t('射线'),     icon: '→' },
  { name: 'horizontalStraightLine', label: t('水平线'),   icon: '═' },
  { name: 'horizontalRayLine',      label: t('水平射线'), icon: '⇥' },
  { name: 'verticalStraightLine',   label: t('垂直线'),   icon: '║' },
  { name: 'priceLine',              label: t('价格线'),   icon: '$' },
  { name: 'priceChannelLine',       label: t('价格通道'), icon: '∥' },
  { name: 'parallelLine',           label: t('平行线'),   icon: '⫽' },
  { name: 'fibonacciLine',          label: t('斐波那契'), icon: 'φ' },
  { name: 'simpleAnnotation',       label: t('注解'),     icon: '✎' },
  { name: 'simpleTag',              label: t('标签'),     icon: '⚑' },
];

// Floating panel anchored to the left tool rail. `onClose` fires on backdrop click.
export function Popover({ onClose, top = 8, left = 48, width = 200, title, children, testId }) {
  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 60 }} />
      <div
        data-testid={testId}
        style={{
          position: 'absolute', top, left, width, zIndex: 61,
          background: 'var(--surface)', border: '1px solid var(--border-strong)',
          borderRadius: 5, boxShadow: '0 8px 28px rgba(20,18,14,0.22)',
          fontFamily: 'var(--font-mono)', fontSize: 11,
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {title && (
          <div style={{
            padding: '6px 10px', borderBottom: '1px solid var(--border)',
            fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em',
            display: 'flex', alignItems: 'center',
          }}>
            <span style={{ flex: 1 }}>{title}</span>
            <span style={{ cursor: 'pointer', color: 'var(--fg-3)' }} onClick={onClose}>✕</span>
          </div>
        )}
        <div style={{ padding: 10 }}>{children}</div>
      </div>
    </>
  );
}

// Indicator picker. `active` is the controlled list of active indicator names for
// this pane; toggling drives createIndicator / removeIndicator on the instance.
export function KlineIndicatorPanel({ pane, klineRef, active, onChange, onClose, top }) {
  const list = pane === 'main' ? MAIN_PANE_INDICATORS : SUB_PANE_INDICATORS;
  const toggle = (name) => {
    const k = klineRef.current;
    if (!k) return;
    if (active.includes(name)) {
      if (pane === 'main') {
        k.removeIndicator('candle_pane', name);
      } else {
        const all = k.listIndicators() || {};
        for (const [paneId, indMap] of Object.entries(all)) {
          const names = Array.isArray(indMap) ? indMap.map((x) => x.name) : Object.keys(indMap || {});
          if (paneId !== 'candle_pane' && names.includes(name)) { k.removeIndicator(paneId, name); break; }
        }
      }
      onChange(active.filter((x) => x !== name));
    } else {
      if (pane === 'main') k.createIndicator(name, true, { id: 'candle_pane' });
      else k.createIndicator(name);
      onChange([...active, name]);
    }
  };
  return (
    <Popover onClose={onClose} top={top} width={pane === 'main' ? 180 : 230}
      title={pane === 'main' ? t('主图指标') : t('副图指标')}
      testId={`kline-indicator-panel-${pane}`}>
      <div style={{ display: 'grid', gridTemplateColumns: pane === 'main' ? '1fr 1fr' : '1fr 1fr', gap: '3px 8px', maxHeight: 260, overflow: 'auto' }}>
        {list.map((name) => (
          <label key={name} style={{ display: 'flex', alignItems: 'center', gap: 5, cursor: 'pointer', color: active.includes(name) ? 'var(--fg)' : 'var(--fg-2)' }}>
            <input type="checkbox" checked={active.includes(name)} onChange={() => toggle(name)} />
            {name}
          </label>
        ))}
      </div>
    </Popover>
  );
}

// Drawing tool palette. Selecting a tool puts KLineChart into interactive draw
// mode (createOverlay with no points) — the user then clicks to place points.
export function KlineDrawPanel({ klineRef, onClose, top }) {
  const start = (name) => { klineRef.current?.createOverlay(name); onClose(); };
  const clearAll = () => { klineRef.current?.removeOverlay({}); };
  return (
    <Popover onClose={onClose} top={top} width={200} title={t('绘图工具')} testId="kline-draw-panel">
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4 }}>
        {DRAW_TOOLS.map((t) => (
          <button key={t.name} className="btn ghost sm" data-testid={`kline-draw-${t.name}`}
            style={{ height: 24, fontSize: 10.5, justifyContent: 'flex-start' }}
            onClick={() => start(t.name)}>
            <span style={{ width: 14, display: 'inline-block', textAlign: 'center' }}>{t.icon}</span>{t.label}
          </button>
        ))}
      </div>
      <hr style={{ border: 'none', borderTop: '1px solid var(--border-faint)', margin: '8px 0' }} />
      <button className="btn ghost sm" style={{ width: '100%', fontSize: 10.5, color: 'var(--err)' }} onClick={clearAll}>
        {t('清空所有画线')}
      </button>
    </Popover>
  );
}

// Export menu — PNG screenshot / CSV data.
export function KlineExportPanel({ onPng, onCsv, onClose, top }) {
  return (
    <Popover onClose={onClose} top={top} width={160} title={t('导出')} testId="kline-export-panel">
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <button className="btn ghost sm" data-testid="kline-export-png"
          style={{ fontSize: 11, justifyContent: 'flex-start' }}
          onClick={() => { onPng(); onClose(); }}>{t('🖼 PNG 截图')}</button>
        <button className="btn ghost sm" data-testid="kline-export-csv"
          style={{ fontSize: 11, justifyContent: 'flex-start' }}
          onClick={() => { onCsv(); onClose(); }}>{t('⊞ CSV 数据')}</button>
      </div>
    </Popover>
  );
}
