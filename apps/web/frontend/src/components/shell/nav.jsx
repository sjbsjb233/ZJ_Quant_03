// Navigation config + glyphs for the terminal shell (mirrors the design handoff).
import React from 'react';

export const MODULES = [
  { id: 'data', label: '数据管理', en: 'DATA', enabled: true, to: '/data/dashboard' },
  { id: 'viz', label: '数据可视化', en: 'VIZ', enabled: true, to: '/data/charts' },
  { id: 'factor', label: '因子管理', en: 'FACTOR', enabled: false, phase: 'Q3', to: '/factors' },
  { id: 'strategy', label: '策略管理', en: 'STRATEGY', enabled: false, phase: 'Q4', to: '/strategies' },
  { id: 'live', label: '实盘交易', en: 'LIVE', enabled: false, phase: '2027', to: '/monitor' },
];

export const DATA_NAV = [
  { id: 'overview', label: '总览', to: '/data/dashboard', hot: true },
  { id: 'batches', label: '批次台账', to: '/data/batches' },
  { id: 'sources', label: '数据源', to: '/data/sources' },
  { id: 'fields', label: '字段注册表', to: '/data/fields' },
  { id: 'tables', label: '表浏览', to: '/data/tables' },
  { id: 'quality', label: '数据质量', to: '/data/quality', warn: true },
  { id: 'maintenance', label: '维护操作', to: '/data/maintenance' },
  { id: 'audit', label: '审计日志', to: '/data/audit' },
  { id: 'logs', label: '日志中心', to: '/data/logs', warn: true },
];

export const VIZ_NAV = [
  { id: 'center', label: '可视化中心', to: '/data/charts' },
  { id: 'kline', label: 'K 线工作台', to: '/data/charts/kline', hot: true },
  { id: 'fundamentals', label: '财务时间序列', to: '/data/charts/fundamentals' },
  { id: 'explorer', label: '通用图表浏览器', to: '/data/charts/explorer' },
];

export function NavGlyph({ id }) {
  const p = { width: 13, height: 13, viewBox: '0 0 16 16', fill: 'none', stroke: 'currentColor', strokeWidth: 1.4, strokeLinecap: 'round', strokeLinejoin: 'round' };
  const G = {
    overview: <svg {...p}><rect x="2" y="2" width="5" height="5" /><rect x="9" y="2" width="5" height="5" /><rect x="2" y="9" width="5" height="5" /><rect x="9" y="9" width="5" height="5" /></svg>,
    batches: <svg {...p}><path d="M2 4h12M2 8h12M2 12h12" /></svg>,
    sources: <svg {...p}><circle cx="4" cy="4" r="2" /><circle cx="12" cy="4" r="2" /><circle cx="8" cy="12" r="2" /><path d="M5.5 5.5L7 10.5M10.5 5.5L9 10.5" /></svg>,
    fields: <svg {...p}><path d="M2 3h12v3H2zM2 7h12v3H2zM2 11h12v3H2z" /></svg>,
    tables: <svg {...p}><rect x="2" y="2" width="12" height="12" /><path d="M2 6h12M2 10h12M6 2v12M10 2v12" /></svg>,
    quality: <svg {...p}><path d="M8 1l6 3v5c0 3.5-3 6-6 6s-6-2.5-6-6V4z" /><path d="M5.5 8l2 2 3-3.5" /></svg>,
    maintenance: <svg {...p}><path d="M3 13l5-5M7 4l5 5M5 3l3 3M11 9l3 3" /></svg>,
    audit: <svg {...p}><path d="M3 2h7l3 3v9H3z" /><path d="M5 6h6M5 9h6M5 12h4" /></svg>,
    logs: <svg {...p}><path d="M2 3h12M2 6h12M2 9h8M2 12h5" /><circle cx="13" cy="12" r="2" /></svg>,
    center: <svg {...p}><circle cx="8" cy="8" r="6" /><path d="M8 2v12M2 8h12" /></svg>,
    kline: <svg {...p}><path d="M3 12V7M3 5V3M8 13V9M8 7V4M13 11V6M13 4V2" /></svg>,
    fundamentals: <svg {...p}><path d="M2 12L6 8L9 10L14 4" /><circle cx="6" cy="8" r="1" /><circle cx="9" cy="10" r="1" /><circle cx="14" cy="4" r="1" /></svg>,
    explorer: <svg {...p}><rect x="2" y="2" width="5" height="5" /><circle cx="11.5" cy="4.5" r="2.5" /><path d="M2 9h5v5H2z" /><path d="M9 9l5 5M14 9l-5 5" /></svg>,
  };
  return G[id] || <svg {...p}><circle cx="8" cy="8" r="5.5" strokeDasharray="2 2" /></svg>;
}
