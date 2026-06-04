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

// Compact error state — surfaces the backend's error envelope (code + message)
// so a failed/unreachable backend reads as an ERROR, never as empty data.
export function MiniError({ error, onRetry, label = '加载失败' }) {
  const e = error?.apiError || {};
  return (
    <div style={{
      padding: 24, color: 'var(--err)', fontFamily: 'var(--font-mono)',
      fontSize: 11, textAlign: 'center',
    }}>
      <div>✕ {label}{e.code ? ` · ${e.code}` : ''}</div>
      <div style={{ color: 'var(--fg-2)', marginTop: 4 }}>
        {e.message || String(error?.message || error || '')}
      </div>
      {onRetry && (
        <button className="btn ghost sm" style={{ marginTop: 8 }} onClick={onRetry}>↻ 重试</button>
      )}
    </div>
  );
}

// ─── 受控降级展示 (设计 §11) ────────────────────────────────────────────────
// Severity → label + color. Keep in sync with the engine's catalogue (§14).
export const SEVERITY_META = {
  critical: { label: '严重', color: 'var(--err)', bg: 'var(--err-bg)' },
  high: { label: '高', color: 'var(--warn)', bg: 'var(--warn-bg)' },
  medium: { label: '中', color: 'var(--info)', bg: 'var(--info-bg)' },
  low: { label: '低', color: 'var(--fg-3)', bg: 'var(--surface-3)' },
};

// Map a run (status + degradation_level/count + error) → a display badge spec.
export function runStatusInfo(run) {
  if (!run) return { label: '—', color: 'var(--fg-3)', glyph: '·' };
  const st = run.status;
  const level = run.degradation_level || run.degradation?.level || 'none';
  const count = run.degradation_count ?? run.degradation?.count ?? 0;
  if (st === 'success') return { label: '成功', color: 'var(--ok)', glyph: '●' };
  if (st === 'degraded') {
    return level === 'severe'
      ? { label: `降级(严重) · ${count}`, color: 'var(--err)', glyph: '⚠', severe: true }
      : { label: `降级 · ${count}`, color: 'var(--warn)', glyph: '▲', degraded: true };
  }
  if (st === 'failed') {
    return run.error === 'preflight_blocked'
      ? { label: '前置校验未通过', color: 'var(--err)', glyph: '⊘', preflight: true }
      : { label: '失败', color: 'var(--err)', glyph: '✕' };
  }
  if (st === 'canceled') return { label: '已取消', color: 'var(--fg-3)', glyph: '⊘' };
  if (st === 'running') return { label: '运行中', color: 'var(--info)', glyph: '●' };
  if (st === 'queued' || st === 'pending') return { label: '排队', color: 'var(--fg-3)', glyph: '○' };
  return { label: st || '—', color: 'var(--fg-3)', glyph: '·' };
}

// Inline status chip — used in run pickers / history tables / queue list.
export function RunStatusBadge({ run, style }) {
  const info = runStatusInfo(run);
  return (
    <span data-testid="run-status-badge" data-status={run?.status}
      data-level={run?.degradation_level || run?.degradation?.level || 'none'}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 4,
        fontFamily: 'var(--font-mono)', fontSize: 10.5, color: info.color,
        ...(info.severe ? { background: 'var(--err-bg)', padding: '0 5px', borderRadius: 2 } : {}),
        ...style,
      }}>
      <span>{info.glyph}</span>{info.label}
    </span>
  );
}

// Coverage progress bar (evidence.coverage ∈ [0,1] vs threshold).
function CoverageBar({ coverage, threshold }) {
  if (coverage == null) return null;
  const pct = Math.max(0, Math.min(1, coverage)) * 100;
  return (
    <div style={{ marginTop: 4 }}>
      <div style={{ height: 5, background: 'var(--surface-3)', borderRadius: 3, position: 'relative', overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: pct >= (threshold ?? 0.95) * 100 ? 'var(--ok)' : 'var(--warn)' }} />
        {threshold != null && (
          <div style={{ position: 'absolute', top: 0, bottom: 0, left: `${threshold * 100}%`, width: 1, background: 'var(--fg-2)' }} />
        )}
      </div>
      <div style={{ fontSize: 9.5, color: 'var(--fg-3)', marginTop: 2, fontFamily: 'var(--font-mono)' }}>
        覆盖率 {(coverage * 100).toFixed(1)}%{threshold != null ? ` · 阈值 ${(threshold * 100).toFixed(0)}%` : ''}
      </div>
    </div>
  );
}

// One expandable row in the degradation table.
function DegradationRow({ ev, blocked }) {
  const [open, setOpen] = React.useState(false);
  const meta = SEVERITY_META[ev.severity] || SEVERITY_META.low;
  const e = ev.evidence || {};
  return (
    <>
      <tr data-testid={`degradation-row-${ev.code}`} onClick={() => setOpen((o) => !o)}
        style={{ borderBottom: '1px solid var(--border-faint)', cursor: 'pointer', height: 30 }}>
        <td style={{ padding: '4px 12px', color: 'var(--fg)' }}>
          {ev.title}
          <span style={{ color: 'var(--fg-4)', fontFamily: 'var(--font-mono)', fontSize: 9.5, marginLeft: 6 }}>{ev.code}</span>
        </td>
        <td style={{ padding: '4px 12px' }}>
          <span style={{ color: meta.color, background: meta.bg, padding: '1px 6px', borderRadius: 2, fontSize: 9.5, fontFamily: 'var(--font-mono)' }}>
            {blocked ? '阻断' : meta.label}
          </span>
        </td>
        <td style={{ padding: '4px 12px', color: 'var(--fg-2)', fontSize: 10.5 }}>{ev.reason}</td>
        <td style={{ padding: '4px 12px', color: 'var(--fg-2)', fontSize: 10.5, minWidth: 160 }}>
          {ev.impact}
          {e.coverage != null && <CoverageBar coverage={e.coverage} threshold={e.threshold} />}
        </td>
        <td style={{ padding: '4px 12px', color: 'var(--fg-3)', fontSize: 10, textAlign: 'right' }}>
          {e.affected_symbols != null ? `${e.affected_symbols} 标的` : ''}
          {e.affected_dates ? ` / ${e.affected_dates} 日` : ''}
        </td>
        <td style={{ padding: '4px 12px', color: 'var(--info)', fontSize: 10 }}>{ev.suggested_action}</td>
      </tr>
      {open && (
        <tr style={{ background: 'var(--surface-2)' }}>
          <td colSpan={6} style={{ padding: '8px 16px', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-2)' }}>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div><span style={{ color: 'var(--fg-3)' }}>期望: </span>{fmtEvidence(e.expected)}</div>
              <div><span style={{ color: 'var(--fg-3)' }}>实际: </span>{fmtEvidence(e.actual)}</div>
            </div>
            {Array.isArray(e.sample) && e.sample.length > 0 && (
              <div style={{ marginTop: 6 }}>
                <span style={{ color: 'var(--fg-3)' }}>受影响样本: </span>{e.sample.join(', ')}
                {e.affected_symbols > e.sample.length ? ' …' : ''}
              </div>
            )}
            {ev.config_key && <div style={{ marginTop: 4, color: 'var(--fg-3)' }}>config_key: {ev.config_key} · stage: {ev.stage} · policy: {ev.policy || '—'}</div>}
          </td>
        </tr>
      )}
    </>
  );
}

function fmtEvidence(v) {
  if (v == null) return '—';
  if (typeof v === 'object') return JSON.stringify(v);
  return String(v);
}

// The "数据降级报告" card (设计 §11.2). `run` is the GET /runs/{id} payload —
// uses run.preflight for failed-by-preflight, run.degradation.events otherwise.
export function DegradationReport({ run, embed }) {
  if (!run) return null;
  const isPreflightBlocked = run.status === 'failed' && run.error === 'preflight_blocked';
  const blocked = isPreflightBlocked ? (run.preflight || []) : [];
  const events = run.degradation?.events || [];
  const rows = isPreflightBlocked
    ? blocked.map((b) => ({ ...b, code: b.check_id }))
    : events;
  if (rows.length === 0) return null;

  const title = isPreflightBlocked ? '前置校验未通过' : '数据降级报告';
  const level = run.degradation?.level || (run.has_critical ? 'severe' : 'partial');
  const accent = isPreflightBlocked || level === 'severe' ? 'var(--err)' : 'var(--warn)';

  return (
    <div data-testid="degradation-report" style={{ border: `1px solid ${accent}`, borderRadius: 4, overflow: 'hidden', margin: embed ? 0 : '12px 16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 14px', background: 'var(--surface-2)', borderBottom: `1px solid ${accent}` }}>
        <span style={{ color: accent, fontWeight: 600, fontSize: 12 }}>{isPreflightBlocked ? '⊘' : '⚠'} {title}</span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>
          {isPreflightBlocked ? `${blocked.length} 项阻断` : `${rows.length} 项降级 · ${level === 'severe' ? '含严重' : '一般'}`}
        </span>
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
            {['降级项', '级别', '触发原因', '影响', '范围', '建议'].map((c, i) => (
              <th key={i} style={{ textAlign: i === 4 ? 'right' : 'left', padding: '6px 12px', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', fontFamily: 'var(--font-mono)' }}>{c}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((ev, i) => <DegradationRow key={i} ev={ev} blocked={isPreflightBlocked} />)}
        </tbody>
      </table>
    </div>
  );
}
