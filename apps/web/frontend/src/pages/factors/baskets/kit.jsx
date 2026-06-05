// ─────────────────────────────────────────────────────────────────────────────
// kit.jsx — Factor-basket shared kit (因子层重构 §10.6). The unified status-badge
// system + banners, calibration chips, score bars, aging row, small form atoms.
// Ported from the Claude Design handoff (fl-shared.jsx) into the real terminal
// shell (CSS vars + components). EVERY basket page renders status via <Badge> —
// changing this file changes the whole subsystem.
// ─────────────────────────────────────────────────────────────────────────────
import React from 'react';

// ─── Status-badge definitions (§1.2 vocabulary, 11 groups) ───────────────────
export const BADGE_DEFS = {
  run: { title: '运行状态', where: '队列 / 运行历史 / 运行详情', items: [
    { v: '排队', tone: 'neutral', char: '◌' },
    { v: '运行中', tone: 'info', char: '◐', pulse: true },
    { v: '成功', tone: 'ok', char: '✓' },
    { v: '降级', tone: 'warn', char: '⚠' },
    { v: '部分成功', tone: 'warn', char: '◑' },
    { v: '失败', tone: 'err', char: '✕' },
    { v: '已取消', tone: 'neutral', char: '⊘', dim: true },
  ] },
  degrade: { title: '降级等级', where: '条目行 / 子任务行 / 详情头部', items: [
    { v: '无', tone: 'neutral', char: '—', dim: true },
    { v: '部分', tone: 'warn', char: '⚠' },
    { v: '严重', tone: 'err', char: '▲' },
  ] },
  settle: { title: '结算状态', where: '条目图表 / 总览提示行', items: [
    { v: '已结算', tone: 'ok', char: '●' },
    { v: '待结算', tone: 'neutral', char: '◌', outline: true },
  ] },
  stale: { title: '配置时效', where: '篮子卡片角标 / 详情横幅', items: [
    { v: '待重算', tone: 'warn', char: '⟳' },
  ] },
  tracking: { title: '追踪状态', where: '篮子卡片 / 详情头部', items: [
    { v: '开启', tone: 'ok', char: '●' },
    { v: '已暂停', tone: 'warn', char: '⏸' },
    { v: '关闭', tone: 'neutral', char: '○', dim: true },
  ] },
  wait: { title: '子任务等待', where: '相关性子任务专属', items: [
    { v: '等待全部评估完成', tone: 'info', char: '⋯', outline: true },
  ] },
  exclude: { title: '排除原因', where: '优选工作台 / 相关性内页', items: [
    { v: '失败', tone: 'err', char: '✕' },
    { v: '严重降级', tone: 'err', char: '▲' },
    { v: '样本不足', tone: 'warn', char: '◔' },
    { v: '指标缺失', tone: 'neutral', char: '∅' },
  ] },
  mode: { title: '运行模式', where: '运行历史 / 队列卡片', items: [
    { v: '全量', tone: 'info', char: '⊕' },
    { v: '增量', tone: 'neutral', char: '＋' },
    { v: '修复', tone: 'warn', char: '⟲' },
    { v: '仅相关性', tone: 'info', char: '◇', outline: true },
  ] },
  trigger: { title: '触发来源', where: '运行历史 / 队列卡片', items: [
    { v: '手动', tone: 'neutral', char: '✎' },
    { v: '自动追踪', tone: 'info', char: '↻' },
    { v: '配置变更', tone: 'warn', char: '✦' },
    { v: '数据修订', tone: 'warn', char: '⤓' },
  ] },
  selection: { title: '集合状态', where: '入选集合', items: [
    { v: '草稿', tone: 'neutral', char: '✎' },
    { v: '已定稿', tone: 'ok', char: '✓' },
    { v: '已归档', tone: 'neutral', char: '⊟', dim: true },
  ] },
  decision: { title: '决策标记', where: '剪枝结果 / 集合详情', items: [
    { v: '保留', tone: 'ok', char: '✓' },
    { v: '手动保留', tone: 'info', char: '✦' },
    { v: '因相关剔除', tone: 'warn', char: '⊝' },
    { v: '超上限剔除', tone: 'neutral', char: '⊘' },
  ] },
};

const TONE_COLOR = { ok: 'var(--ok)', warn: 'var(--warn)', err: 'var(--err)', info: 'var(--info)', neutral: 'var(--fg-3)' };
const TONE_BG = { ok: 'var(--ok-bg)', warn: 'var(--warn-bg)', err: 'var(--err-bg)', info: 'var(--info-bg)', neutral: 'var(--surface-2)' };

export function findBadge(group, value) {
  const g = BADGE_DEFS[group];
  if (!g) return null;
  return g.items.find((i) => i.v === value) || null;
}

// Unified badge. <Badge group="run" value="成功" />
export function Badge({ group, value, sm, label }) {
  const d = findBadge(group, value);
  if (!d) {
    return <span style={chipStyle('neutral', false, sm)}>{value}</span>;
  }
  return (
    <span style={{ ...chipStyle(d.tone, d.outline, sm), opacity: d.dim ? 0.7 : 1 }}>
      <span style={{ display: 'inline-flex', alignItems: 'center' }}>
        {d.pulse
          ? <span style={{ width: 5, height: 5, borderRadius: '50%', background: TONE_COLOR[d.tone], display: 'inline-block', animation: 'pulse 1.4s infinite' }} />
          : <span style={{ fontSize: sm ? 9 : 10, lineHeight: 1 }}>{d.char}</span>}
      </span>
      <span>{label || value}</span>
    </span>
  );
}

function chipStyle(tone, outline, sm) {
  return {
    display: 'inline-flex', alignItems: 'center', gap: 4, height: sm ? 15 : 17,
    padding: '0 6px', borderRadius: 3, fontFamily: 'var(--font-mono)',
    fontSize: sm ? 9.5 : 10.5, whiteSpace: 'nowrap',
    color: TONE_COLOR[tone] || 'var(--fg-2)',
    background: outline ? 'transparent' : (TONE_BG[tone] || 'var(--surface-2)'),
    border: `1px solid ${outline ? (TONE_COLOR[tone] || 'var(--border)') : 'transparent'}`,
  };
}

// ─── Hover popover ───────────────────────────────────────────────────────────
export function Hover({ content, children, w = 240, align = 'left' }) {
  const [open, setOpen] = React.useState(false);
  return (
    <span style={{ position: 'relative', display: 'inline-flex' }}
      onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      {children}
      {open && (
        <div style={{
          position: 'absolute', top: '100%', [align]: 0, marginTop: 5, zIndex: 60, width: w,
          background: 'var(--surface)', border: '1px solid var(--border-strong)', borderRadius: 5,
          boxShadow: '0 8px 28px rgba(20,18,14,0.16)', padding: 10,
          fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-2)', cursor: 'default',
        }}>{content}</div>
      )}
    </span>
  );
}

// ─── Calibration (口径) chips + hover detail. Accepts a calibration summary
// object {universe_id, window, rebalance, primary_horizon, ...} (§10.5). ───────
export function calibrItems(c) {
  if (!c) return [];
  return [
    ['股票池', c.universe_id], ['时间窗口', c.window], ['调仓频率', c.rebalance],
    ['主周期', c.primary_horizon != null ? `${c.primary_horizon} 日` : null],
  ].filter(([, v]) => v != null);
}
export function CalibrChips({ calibration, compact }) {
  const items = calibrItems(calibration);
  if (!items.length) return <span style={{ color: 'var(--fg-mute)', fontFamily: 'var(--font-mono)', fontSize: 10 }}>—</span>;
  const detail = (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
      <div style={{ fontSize: 9, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>口径摘要</div>
      {items.map(([k, v]) => (
        <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
          <span style={{ color: 'var(--fg-3)' }}>{k}</span><span style={{ color: 'var(--fg)' }}>{v}</span>
        </div>
      ))}
    </div>
  );
  return (
    <Hover content={detail} w={220}>
      <span style={{ display: 'inline-flex', gap: 5, flexWrap: 'wrap', alignItems: 'center' }}>
        {items.slice(0, compact ? 2 : 4).map(([k, v]) => (
          <span key={k} style={{ ...chipStyle('neutral', false, true), color: 'var(--fg-2)' }}>{v}</span>
        ))}
        {compact && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-4)' }}>口径 ⓘ</span>}
      </span>
    </Hover>
  );
}

export function ParamChips({ params }) {
  const keys = Object.keys(params || {});
  if (!keys.length) return <span style={{ color: 'var(--fg-mute)', fontFamily: 'var(--font-mono)', fontSize: 10 }}>—</span>;
  return (
    <span style={{ display: 'inline-flex', gap: 4, flexWrap: 'wrap' }}>
      {keys.map((k) => (
        <span key={k} style={{ ...chipStyle('neutral', true, true), padding: '0 5px' }}>{k} {String(params[k])}</span>
      ))}
    </span>
  );
}

// ─── Score contribution bar (§11.4 hover) ────────────────────────────────────
export function ScoreBar({ detail }) {
  // detail: {metric: {x, z, w, contrib}}
  const rows = Object.entries(detail || {}).map(([metric, d]) => ({ metric, ...d }));
  const total = rows.reduce((s, c) => s + Math.max(0, c.contrib || 0), 0) || 1;
  const palette = ['#5a8a34', '#3478a0', '#a89234', '#5a4ea0', '#cc7a34', '#7a7468'];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 8, width: 280 }}>
      <div style={{ fontSize: 9, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>得分构成 (z·权重)</div>
      <div style={{ display: 'flex', height: 8, borderRadius: 3, overflow: 'hidden', border: '1px solid var(--border)' }}>
        {rows.map((c, i) => (
          <div key={c.metric} style={{ width: `${Math.max(0, c.contrib || 0) / total * 100}%`, background: palette[i % palette.length] }} />
        ))}
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 10 }}>
        <thead><tr style={{ color: 'var(--fg-3)' }}>
          {['指标', '原值', '标准分', '权重', '贡献'].map((h, i) => (
            <th key={h} style={{ textAlign: i === 0 ? 'left' : 'right', padding: '2px 4px', fontWeight: 500 }}>{h}</th>
          ))}
        </tr></thead>
        <tbody>
          {rows.map((c, i) => (
            <tr key={c.metric}>
              <td style={{ padding: '2px 4px', color: 'var(--fg-2)' }}>
                <span style={{ display: 'inline-block', width: 7, height: 7, borderRadius: 2, background: palette[i % palette.length], marginRight: 5 }} />
                {c.metric}
              </td>
              <td style={{ textAlign: 'right', padding: '2px 4px', color: 'var(--fg-3)' }}>{fmtNum(c.x, 3)}</td>
              <td style={{ textAlign: 'right', padding: '2px 4px', color: 'var(--fg-2)' }}>{fmtNum(c.z, 2)}</td>
              <td style={{ textAlign: 'right', padding: '2px 4px', color: 'var(--fg-3)' }}>{fmtNum(c.w, 2)}</td>
              <td style={{ textAlign: 'right', padding: '2px 4px', color: 'var(--fg)', fontWeight: 500 }}>{fmtNum(c.contrib, 3)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ─── Banners ─────────────────────────────────────────────────────────────────
export function StaleBanner({ stale, onRerun, busy }) {
  if (!stale || !stale.is) return null;
  return (
    <div style={{ padding: '8px 18px', background: 'var(--warn-bg)', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
      <Badge group="stale" value="待重算" />
      <span style={{ fontSize: 11.5, color: 'var(--fg-2)' }}>
        配置已修订至 <b style={{ color: 'var(--fg)' }}>rev {stale.toRev}</b>，现有结果基于 rev {stale.fromRev}，需重新运行
        <span style={{ color: 'var(--fg-3)', marginLeft: 8, fontFamily: 'var(--font-mono)', fontSize: 10.5 }}>影响范围：{stale.scope}</span>
        {stale.reason && <span style={{ color: 'var(--fg-3)', marginLeft: 8 }}>· {stale.reason}</span>}
      </span>
      <div style={{ flex: 1 }} />
      {onRerun && <button className="btn primary sm" disabled={busy} onClick={onRerun}>{busy ? '提交中…' : '一键重跑'}</button>}
    </div>
  );
}

export function LightHint({ tone = 'info', children, action }) {
  const col = tone === 'warn' ? 'var(--warn)' : tone === 'err' ? 'var(--err)' : 'var(--info)';
  const bg = tone === 'warn' ? 'var(--warn-bg)' : tone === 'err' ? 'var(--err-bg)' : 'var(--info-bg)';
  return (
    <div style={{ padding: '6px 12px', background: bg, border: `1px solid ${col}`, borderRadius: 4, display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, color: 'var(--fg-2)' }}>
      <span style={{ color: col }}>ⓘ</span>
      <span style={{ flex: 1 }}>{children}</span>
      {action}
    </div>
  );
}

// settle hint row (§7.2): conclusion stops at as_of.
export function Aging({ asOf, pending }) {
  const p = pending && pending.length ? pending[0] : null;
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-2)' }}>
      <span style={{ color: 'var(--fg-3)' }}>as_of</span>
      <span style={{ color: 'var(--fg)', fontWeight: 500 }}>{asOf || '—'}</span>
      {p ? (
        <>
          <span style={{ color: 'var(--fg-mute)' }}>·</span>
          <Badge group="settle" value="待结算" sm />
          <span style={{ color: 'var(--fg-3)' }}>调仓日 {p.date}（预计 {p.eta} 结算）</span>
        </>
      ) : <span style={{ color: 'var(--fg-3)' }}>· 全部已结算</span>}
    </div>
  );
}

// ─── Small atoms ─────────────────────────────────────────────────────────────
export function Segmented({ options, value, onChange }) {
  return (
    <div style={{ display: 'inline-flex', border: '1px solid var(--border-strong)', borderRadius: 4, overflow: 'hidden' }}>
      {options.map((o, i) => {
        const v = typeof o === 'string' ? o : o.v;
        const l = typeof o === 'string' ? o : o.l;
        return (
          <button key={v} onClick={() => onChange(v)} style={{
            height: 24, padding: '0 11px', border: 'none', borderLeft: i ? '1px solid var(--border)' : 'none',
            background: value === v ? 'var(--fg)' : 'var(--surface)', color: value === v ? 'var(--bg)' : 'var(--fg-2)',
            fontFamily: 'var(--font-mono)', fontSize: 11, cursor: 'pointer',
          }}>{l}</button>
        );
      })}
    </div>
  );
}

export function Toggle({ on, onClick, disabled }) {
  return (
    <span onClick={disabled ? undefined : onClick} style={{
      width: 30, height: 16, borderRadius: 8, background: on ? 'var(--ok)' : 'var(--surface-3)',
      border: '1px solid', borderColor: on ? 'var(--ok)' : 'var(--border)', position: 'relative',
      display: 'inline-block', cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.4 : 1, transition: 'background .15s',
    }}>
      <span style={{ position: 'absolute', top: 1, left: on ? 15 : 1, width: 12, height: 12, borderRadius: '50%', background: '#fff', transition: 'left .15s' }} />
    </span>
  );
}

export function SectionHead({ title, meta, right }) {
  return (
    <header style={{ padding: '6px 14px', background: 'var(--surface-2)', borderBottom: '1px solid var(--border)', borderTop: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10 }}>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{title}</span>
      {meta && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)' }}>· {meta}</span>}
      <div style={{ flex: 1 }} />
      {right}
    </header>
  );
}

export function Tabs({ tabs, active, onChange }) {
  return (
    <div style={{ height: 30, padding: '0 14px', background: 'var(--surface)', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'flex-end', gap: 0, flexShrink: 0 }}>
      {tabs.map((t) => (
        <button key={t.id} onClick={() => onChange(t.id)} style={{
          height: 30, padding: '0 14px', background: active === t.id ? 'var(--surface-2)' : 'transparent',
          border: 'none', borderTop: active === t.id ? '2px solid var(--fg)' : '2px solid transparent',
          marginBottom: -1, fontFamily: 'var(--font-mono)', fontSize: 11.5,
          color: active === t.id ? 'var(--fg)' : 'var(--fg-3)', fontWeight: active === t.id ? 500 : 400, cursor: 'pointer',
          display: 'inline-flex', alignItems: 'center', gap: 6,
        }}>
          {t.label}
          {t.count != null && <span style={{ fontSize: 9.5, color: 'var(--fg-4)' }}>{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

// KPI strip in the basket mono style (distinct from shell KpiStrip).
export function KPIStrip({ items, cols }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cols || items.length}, 1fr)`, background: 'var(--surface)', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
      {items.map((c, i) => (
        <div key={i} style={{ padding: '8px 14px', borderRight: i < items.length - 1 ? '1px solid var(--border-faint)' : 'none', display: 'flex', flexDirection: 'column', gap: 4 }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{c.l}</span>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 7 }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 19, fontWeight: 500, fontVariantNumeric: 'tabular-nums', lineHeight: 1, color: c.t ? (TONE_COLOR[c.t] || 'var(--fg)') : 'var(--fg)' }}>{c.v}</span>
            {c.sub && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>{c.sub}</span>}
          </div>
        </div>
      ))}
    </div>
  );
}

export function EmptyState({ icon, title, desc, action }) {
  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12, padding: 40, textAlign: 'center' }}>
      <div style={{ width: 44, height: 44, borderRadius: 10, border: '1px dashed var(--border-strong)', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--fg-4)', fontSize: 20 }}>{icon || '◇'}</div>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, color: 'var(--fg)', fontWeight: 500 }}>{title}</div>
      <div style={{ fontSize: 11.5, color: 'var(--fg-3)', maxWidth: 360, lineHeight: 1.6 }}>{desc}</div>
      {action}
    </div>
  );
}

// ─── Format + status helpers ─────────────────────────────────────────────────
export function fmtNum(v, digits = 3) {
  if (v == null || v === '' || Number.isNaN(Number(v))) return '—';
  return Number(v).toFixed(digits);
}
export function fmtPct(v, digits = 1) {
  if (v == null || Number.isNaN(Number(v))) return '—';
  return `${(Number(v) * 100).toFixed(digits)}%`;
}
export function tone(value, kind) {
  if (value == null) return 'neutral';
  const v = Math.abs(Number(value));
  if (kind === 'icir') return v >= 0.5 ? 'ok' : v >= 0.3 ? 'warn' : 'neutral';
  if (kind === 'ic') return v >= 0.03 ? 'ok' : v >= 0.02 ? 'warn' : 'neutral';
  return 'neutral';
}
// Map a raw run/degrade status to the badge group+value.
export const RUN_GROUP = 'run';
export function degradeValue(level) {
  return level === 'severe' ? '严重' : level === 'partial' ? '部分' : '无';
}
