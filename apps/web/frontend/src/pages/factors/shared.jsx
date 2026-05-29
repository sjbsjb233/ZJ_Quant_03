// Tiny shared bits used across the factor pages.
import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { endpoints } from '../../lib/api';

// Light label/tone helpers — keep tones in sync with the chip style classes.
export function tone(value, kind = 'icir') {
  if (value == null) return 'neutral';
  if (kind === 'icir') {
    if (value >= 2.0) return 'ok';
    if (value >= 1.5) return null;
    return 'warn';
  }
  if (kind === 'ic') {
    if (Math.abs(value) >= 0.04) return 'ok';
    if (Math.abs(value) < 0.02) return 'warn';
    return null;
  }
  if (kind === 'turn') {
    if (value > 0.4) return 'warn';
    if (value < 0.1) return 'ok';
    return null;
  }
  return null;
}

export const FACTOR_CATEGORIES = [
  { id: 'momentum', l: '动量', en: 'MOM' },
  { id: 'value', l: '价值', en: 'VAL' },
  { id: 'quality', l: '质量', en: 'QUA' },
  { id: 'vol', l: '波动率', en: 'VOL' },
  { id: 'growth', l: '成长', en: 'GRW' },
  { id: 'size', l: '规模', en: 'SIZ' },
  { id: 'flow', l: '资金流', en: 'FLW' },
  { id: 'uncategorized', l: '未分类', en: 'UNC' },
];

export function catLabel(id) {
  return FACTOR_CATEGORIES.find((c) => c.id === id)?.en || id?.toUpperCase() || '—';
}

export function fmtNum(v, digits = 3) {
  if (v == null || Number.isNaN(v)) return '—';
  return Number(v).toFixed(digits);
}
export function fmtPct(v, digits = 1) {
  if (v == null || Number.isNaN(v)) return '—';
  return (Number(v) * 100).toFixed(digits) + '%';
}

// All preset universe ids the user can reference but cannot delete.
export const PRESET_UNI = new Set([
  'preset:all', 'preset:hs300', 'preset:zz500', 'preset:zz800',
  'preset:zz1000', 'preset:sse50',
]);

// Shared loader for universes (used by the run-config drawer + correlation +
// library tracking toggle, all of which need the list).
export function useUniverses() {
  return useQuery({
    queryKey: ['factor.universes'],
    queryFn: () => endpoints.universes(),
    staleTime: 30_000,
  });
}

// Generic empty / loading helpers tuned for terminal-density.
export function MiniEmpty({ label = '暂无数据' }) {
  return (
    <div style={{
      padding: 32, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)',
      fontSize: 11, textAlign: 'center',
    }}>{label}</div>
  );
}

export function MiniLoading({ label = '加载中…' }) {
  return (
    <div style={{
      padding: 24, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)',
      fontSize: 11, textAlign: 'center', display: 'inline-flex', alignItems: 'center', gap: 8,
    }}><span className="dot running" />{label}</div>
  );
}
