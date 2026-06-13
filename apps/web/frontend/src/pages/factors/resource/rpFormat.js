// rpFormat.js — 资源画像 number formats + 阶段色板 (设计交付 §5.3 + 阶段色板).
// Ported from the Claude Design handoff (rp-data.js / rp-shared.jsx) into a
// dependency-free ES module (no window globals).

// ── stage palette (shared across timeline bands / stage rows / flame) ────────
export const STAGE_COLORS = {
  load:      [0.55, 0.09, 250],
  preflight: [0.55, 0.09, 160],
  compute:   [0.63, 0.10, 70],
  write:     [0.52, 0.09, 320],
  eval:      [0.55, 0.09, 205],
};
export const STAGE_ORDER = ['load', 'preflight', 'compute', 'write', 'eval'];

export function rpCol(id, a) {
  const c = STAGE_COLORS[id];
  if (!c) return 'var(--fg-3)';
  return `oklch(${c[0]} ${c[1]} ${c[2]}${a != null ? ' / ' + a : ''})`;
}

// ── number formats (§5.3) ────────────────────────────────────────────────────
export function rpThousand(n) {
  return Math.round(n || 0).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}
export function rpMB(v) {
  v = v || 0;
  return v >= 10000 ? `${(v / 1024).toFixed(1)} GB` : `${rpThousand(v)} MB`;
}
export function rpMBd(v) {
  return (v < 0 ? '−' : '+') + rpMB(Math.abs(v || 0));
}
export function rpMMSS(s) {
  s = Math.max(0, Math.round(s || 0));
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
}
export function rpMidTrunc(s, max) {
  if (!s || s.length <= max) return s || '';
  const a = Math.ceil((max - 1) * 0.6);
  const b = (max - 1) - a;
  return s.slice(0, a) + '…' + s.slice(s.length - b);
}
