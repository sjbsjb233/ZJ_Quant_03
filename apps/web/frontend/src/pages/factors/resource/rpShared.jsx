// rpShared.jsx — 资源画像 shared atoms: badges, hover/tooltips, panel shells,
// skeleton + empty-state templates (设计交付 §5 共享组件与内容规则).
// Ported from the Claude Design handoff (rp-shared.jsx).
import React from 'react';

// ── tooltip copy (§5.4 全文) ─────────────────────────────────────────────────
export const RP_TIPS = {
  exact: '该任务运行期间进程内没有其他任务，内存数字可精确归因于本次运行。',
  shared: (n) => `运行期间最多有 ${n} 个任务并发，内存为进程整体读数，无法精确拆分到单个任务；CPU 时间不受影响，始终精确。`,
  baseline: '同一因子同配置（或同任务类）历史运行的中位数 / 90 分位水平。',
  norm: '按本次处理的数据规模（百万行）折算，便于不同股票池、不同窗口之间公平比较。',
  admission: '调度器对本任务的放行/延迟时间点；延迟通常意味着当时系统资源紧张。',
  flame: '横向宽度代表函数耗时占比，纵向是调用层级；点击帧可下钻，面包屑返回。',
  profiling: '剖析模式有约 3–5% 的性能开销；只影响耗时，不影响因子数值结果。',
};

// ── hover popover ────────────────────────────────────────────────────────────
export function RPHover({ tip, w = 270, align = 'left', children, up }) {
  const [open, setOpen] = React.useState(false);
  return (
    <span style={{ position: 'relative', display: 'inline-flex' }}
      onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      {children}
      {open && (
        <div style={{
          position: 'absolute', [up ? 'bottom' : 'top']: '100%', [align]: 0,
          [up ? 'marginBottom' : 'marginTop']: 6, zIndex: 80, width: w,
          background: 'var(--surface)', border: '1px solid var(--border-strong)',
          borderRadius: 5, boxShadow: '0 8px 28px rgba(20,18,14,0.16)', padding: '8px 11px',
          fontSize: 11, lineHeight: 1.65, color: 'var(--fg-2)', cursor: 'default',
          fontFamily: 'var(--font-sans)', whiteSpace: 'normal',
        }}>{tip}</div>
      )}
    </span>
  );
}

export function RPInfoDot({ tip, w }) {
  return (
    <RPHover tip={tip} w={w}>
      <span style={{ color: 'var(--fg-4)', cursor: 'help', fontSize: 10, fontFamily: 'var(--font-mono)' }}>ⓘ</span>
    </RPHover>
  );
}

// ── 归因等级徽章 (§5.1) ──────────────────────────────────────────────────────
export function RPAttrBadge({ level, n = 3, sm, noTip }) {
  const shared = level !== 'exact';
  const chip = (
    <span className={shared ? 'chip neutral' : 'chip outline'} style={{ height: sm ? 16 : 18, fontSize: sm ? 9.5 : 10.5, cursor: 'help', gap: 5 }}>
      <span style={{ width: 6, height: 6, borderRadius: '50%', background: shared ? 'var(--fg-4)' : 'var(--ok)' }}></span>
      <span>归因 {shared ? `shared-${n}` : 'exact'}</span>
    </span>
  );
  if (noTip) return chip;
  return <RPHover tip={shared ? RP_TIPS.shared(n) : RP_TIPS.exact} w={290}>{chip}</RPHover>;
}

// ── 性能徽章 (§5.2 五态) ─────────────────────────────────────────────────────
export const RP_PERF = {
  normal:   { tone: 'ok',      char: '●', l: '正常' },
  high:     { tone: 'warn',    char: '▲', l: '偏高' },
  abnormal: { tone: 'err',     char: '▲', l: '异常' },
  low:      { tone: 'err',     char: '▽', l: '异常偏低', outline: true },
  building: { tone: 'neutral', char: '◌', l: '基线建立中' },
};
export function RPPerfBadge({ state, n = 3, sm }) {
  const d = RP_PERF[state] || RP_PERF.normal;
  const label = state === 'building' ? `${d.l}（${n}/5）` : d.l;
  return (
    <span className={`chip ${d.tone}${d.outline ? ' outline' : ''}`}
      style={{ height: sm ? 16 : 18, fontSize: sm ? 9.5 : 10.5, gap: 4, ...(d.outline ? { borderColor: 'var(--err)', color: 'var(--err)' } : {}) }}>
      <span style={{ fontSize: sm ? 9 : 10, lineHeight: 1 }}>{d.char}</span><span>{label}</span>
    </span>
  );
}

// ── 终态 (页面二甘特/成本表 五态) ────────────────────────────────────────────
export const RP_TERM = {
  success:   { tone: 'ok',      char: '✓', l: '成功' },
  degraded:  { tone: 'warn',    char: '⚠', l: '降级' },
  blocked:   { tone: 'warn',    char: '⊘', l: '阻断', outline: true },
  failed:    { tone: 'err',     char: '✕', l: '失败' },
  cancelled: { tone: 'neutral', char: '⊘', l: '已取消', dim: true },
};
export function RPTermChip({ state, sm }) {
  const d = RP_TERM[state] || RP_TERM.success;
  return (
    <span className={`chip ${d.tone}${d.outline ? ' outline' : ''}`}
      style={{ height: sm ? 15 : 17, fontSize: sm ? 9.5 : 10.5, gap: 4, opacity: d.dim ? 0.7 : 1, ...(d.outline ? { borderColor: 'var(--warn)', color: 'var(--warn)' } : {}) }}>
      <span style={{ fontSize: sm ? 9 : 10, lineHeight: 1 }}>{d.char}</span><span>{d.l}</span>
    </span>
  );
}

// ── panel shell (matches existing SectionHead/card language) ─────────────────
export function RPPanel({ title, meta, right, children, style, flat }) {
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'visible', ...style }}>
      {title && (
        <header style={{
          padding: '6px 14px', background: 'var(--surface-2)', borderBottom: flat ? 'none' : '1px solid var(--border)',
          borderRadius: '6px 6px 0 0', display: 'flex', alignItems: 'center', gap: 10,
        }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{title}</span>
          {meta && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)' }}>· {meta}</span>}
          <div style={{ flex: 1 }}></div>
          {right}
        </header>
      )}
      {children}
    </div>
  );
}

// ── skeleton + empty-state templates ─────────────────────────────────────────
export function RPSk({ w = '100%', h = 12, r = 3, style }) {
  return (
    <div style={{
      width: w, height: h, borderRadius: r,
      background: 'linear-gradient(90deg, var(--surface-3) 25%, var(--surface-2) 50%, var(--surface-3) 75%)',
      backgroundSize: '200% 100%', animation: 'sk-shimmer 1.4s linear infinite', ...style,
    }}></div>
  );
}
export function RPEmpty({ icon = '◇', title, desc, action, pad = 36, running }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 10, padding: pad, textAlign: 'center' }}>
      <div style={{ width: 40, height: 40, borderRadius: 9, border: '1px dashed var(--border-strong)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--fg-4)', fontSize: 18 }}>
        {running ? <span className="dot running" style={{ margin: 0 }}></span> : icon}
      </div>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5, color: 'var(--fg)', fontWeight: 500 }}>{title}</div>
      {desc && <div style={{ fontSize: 11, color: 'var(--fg-3)', maxWidth: 400, lineHeight: 1.6 }}>{desc}</div>}
      {action}
    </div>
  );
}
