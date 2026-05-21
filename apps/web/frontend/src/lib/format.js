// Display formatters + status/freshness encodings shared by every page.

export function fmtN(n) {
  if (n === null || n === undefined) return '—';
  return Number(n).toLocaleString('en-US');
}

export function fmtCompact(n) {
  if (n === null || n === undefined) return '—';
  const a = Math.abs(n);
  if (a >= 1e9) return (n / 1e9).toFixed(2) + 'B';
  if (a >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (a >= 1e3) return (n / 1e3).toFixed(1) + 'K';
  return String(n);
}

export function fmtBytes(n) {
  if (n === null || n === undefined) return '—';
  if (n >= 1024 ** 3) return (n / 1024 ** 3).toFixed(1) + ' GB';
  if (n >= 1024 ** 2) return (n / 1024 ** 2).toFixed(1) + ' MB';
  if (n >= 1024) return (n / 1024).toFixed(1) + ' KB';
  return n + ' B';
}

export function fmtPct(v, digits = 1) {
  if (v === null || v === undefined) return '—';
  return (v * 100).toFixed(digits) + '%';
}

export function relTime(value) {
  if (value == null) return '—';
  const t = typeof value === 'string' ? new Date(value) : value;
  const sec = (Date.now() - t.getTime()) / 1000;
  if (sec < 0) return '刚刚';
  if (sec < 60) return `${Math.floor(sec)} 秒前`;
  if (sec < 3600) return `${Math.floor(sec / 60)} 分钟前`;
  if (sec < 86400) return `${Math.floor(sec / 3600)} 小时前`;
  if (sec < 86400 * 30) return `${Math.floor(sec / 86400)} 天前`;
  return t.toISOString().slice(0, 10);
}

export function absTime(value) {
  if (value == null) return '—';
  const t = typeof value === 'string' ? new Date(value) : value;
  const p = (n) => String(n).padStart(2, '0');
  return `${t.getFullYear()}-${p(t.getMonth() + 1)}-${p(t.getDate())} ${p(t.getHours())}:${p(t.getMinutes())}:${p(t.getSeconds())}`;
}

export function fmtDuration(sec) {
  if (sec === null || sec === undefined) return '—';
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m < 60) return `${m}m ${String(s).padStart(2, '0')}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${String(m % 60).padStart(2, '0')}m`;
}

// status -> { tone, sym, label } for batches/jobs (color + glyph dual-encode)
export function statusMeta(status) {
  const map = {
    success: { tone: 'ok', sym: '●', label: 'success' },
    failed: { tone: 'err', sym: '✕', label: 'failed' },
    running: { tone: 'info', sym: '◌', label: 'running' },
    invalidated: { tone: 'neutral', sym: '⊘', label: 'invalidated' },
    superseded: { tone: 'neutral', sym: '⊘', label: 'superseded' },
    pending: { tone: 'neutral', sym: '○', label: 'pending' },
    cancelled: { tone: 'neutral', sym: '⊘', label: 'cancelled' },
    partial: { tone: 'warn', sym: '◐', label: 'partial' },
    // field statuses
    extras: { tone: 'warn', sym: '◇', label: 'extras' },
    promoted: { tone: 'info', sym: '◆', label: 'promoted' },
    core: { tone: 'ok', sym: '●', label: 'core' },
    deprecated: { tone: 'neutral', sym: '⊘', label: 'deprecated' },
    // quality
    warn: { tone: 'warn', sym: '⚠', label: 'warn' },
    error: { tone: 'err', sym: '✕', label: 'error' },
    pass: { tone: 'ok', sym: '✓', label: 'pass' },
  };
  return map[status] || { tone: 'neutral', sym: '·', label: status || '—' };
}

// table freshness -> { tone, sym, label }
export function freshnessMeta(f) {
  const map = {
    ok: { tone: 'ok', sym: '●', label: 'FRESH' },
    warn: { tone: 'warn', sym: '⚠', label: 'STALE' },
    stale: { tone: 'warn', sym: '⚠', label: 'STALE' },
    critical: { tone: 'err', sym: '✕', label: 'CRITICAL' },
    info: { tone: 'neutral', sym: '◌', label: 'STATIC' },
  };
  return map[f] || map.info;
}

export const toneColor = (tone) =>
  ({ ok: 'var(--ok)', err: 'var(--err)', warn: 'var(--warn)', info: 'var(--info)', neutral: 'var(--fg-3)' }[tone] || 'var(--fg)');
