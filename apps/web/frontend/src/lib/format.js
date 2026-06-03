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

// ── 时间戳「无日期」可辨识性 (§4) ──────────────────────────────────────────
// 两套时间戳约定并存:
//   • app_log / factor 任务 ts —— 朴素 UTC 或显式带 `Z`,需补 `Z` 后转本地墙钟
//     (与 logs/shared.fmtTs 一致);
//   • data / batch ts —— 全站(absTime 直接渲染)按本地墙钟原样显示,不做时区换算。
// 因此把「是否把朴素串视作 UTC」做成可配置项 (assumeUtc),既能让日志/因子页正确
// 转本地,又能让批次页与同页 absTime 渲染保持一致(不平移、hover tooltip 不矛盾)。
function _toLocalDate(value, assumeUtc = true) {
  if (value == null) return null;
  if (typeof value !== 'string') {
    const d = value instanceof Date ? value : new Date(value);
    return isNaN(d.getTime()) ? null : d;
  }
  const zoned = value.endsWith('Z') || value.includes('+');
  const d = new Date(zoned || !assumeUtc ? value : `${value}Z`);
  return isNaN(d.getTime()) ? null : d;
}

// 日历日差:今天=0,昨天=1 …(按本地时区)。now 可注入以便测试。
export function dayDiff(d, now = new Date()) {
  const a = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const b = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((b - a) / 864e5);
}

// 模式 B 拆分版:返回 { time, day } —— day 为 ''(今天)或最小日期前缀
// (昨 / MM-DD / YY-MM-DD)。供需要把日期放到副标题、避免主值过长换行的场景
// (如 KPI 条:value=time、sub 带 day)使用。
export function smartTsParts(value, { seconds = false, assumeUtc = true, now = new Date() } = {}) {
  const t = _toLocalDate(value, assumeUtc);
  if (!t) return { time: '—', day: '' };
  const p = (n) => String(n).padStart(2, '0');
  const time = seconds
    ? `${p(t.getHours())}:${p(t.getMinutes())}:${p(t.getSeconds())}`
    : `${p(t.getHours())}:${p(t.getMinutes())}`;
  const dd = dayDiff(t, now);
  let day = '';
  if (dd === 1) day = '昨';                                                   // 昨天
  else if (dd !== 0) day = t.getFullYear() === now.getFullYear()
    ? `${p(t.getMonth() + 1)}-${p(t.getDate())}`                              // 今年
    : `${String(t.getFullYear()).slice(2)}-${p(t.getMonth() + 1)}-${p(t.getDate())}`; // 跨年
  return { time, day };
}

// 模式 B:上下文前缀(孤立值用)。只在「非今天」时才显示日期前缀。
//   今天 → 17:44:17 / 17:44 ; 昨天 → 昨 14:33 ; 今年 → 06-01 14:33 ; 跨年 → 25-12-31 23:49
// seconds=true → HH:MM:SS,否则 HH:MM。assumeUtc 见 _toLocalDate 注释。
export function smartTs(value, opts) {
  const { time, day } = smartTsParts(value, opts);
  if (time === '—') return '—';
  return day ? `${day} ${time}` : time;
}

// 模式 A:供密集时序列表渲染判断「这一行是否跨入新的一天」。
// 返回该行应显示的日期标签(跨日时,如「06-02 · 昨天」)或 null(与上一行同日)。
// prevTs 为空(首行)时返回首行日期标签,作为列表顶部的日界锚点。
export function dayBoundaryLabel(prevTs, curTs, now = new Date()) {
  const cur = _toLocalDate(curTs);
  if (!cur) return null;
  const prev = _toLocalDate(prevTs);
  const sameDay = prev
    && prev.getFullYear() === cur.getFullYear()
    && prev.getMonth() === cur.getMonth()
    && prev.getDate() === cur.getDate();
  if (sameDay) return null;
  const dd = dayDiff(cur, now);
  const pad = (n) => String(n).padStart(2, '0');
  const md = `${pad(cur.getMonth() + 1)}-${pad(cur.getDate())}`;
  const rel = dd === 0 ? '今天' : dd === 1 ? '昨天' : dd > 1 ? `${dd} 天前` : '';
  return rel ? `${md} · ${rel}` : md;
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
