// Terminal chrome: module strip, sidebar, sub-toolbar, page title bar, status bar.
import React from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../../lib/auth';
import { useTheme } from '../../lib/theme';
import { getLastTraceId } from '../../lib/logger';
import { useObsStatus } from '../../lib/obsStatus';
import { ObsRealtime } from '../../pages/observability/shared';
import { DATA_NAV, FACTOR_NAV, MODULES, NavGlyph, OBSERV_NAV, VIZ_NAV } from './nav';

export function ModuleStrip({ current, env = 'PROD' }) {
  const nav = useNavigate();
  const { user, logout } = useAuth();
  const { theme, toggle } = useTheme();
  const envColor = env === 'PROD' ? 'var(--err)' : env === 'STAGING' ? 'var(--warn)' : 'var(--info)';
  return (
    <div style={{
      height: 26, background: 'var(--surface-3)', borderBottom: '1px solid var(--border)',
      padding: '0 14px', display: 'flex', alignItems: 'center',
      fontFamily: 'var(--font-mono)', fontSize: 10.5, flexShrink: 0,
    }}>
      <span style={{ color: 'var(--fg)', fontWeight: 600, letterSpacing: '0.12em', marginRight: 14 }}>ZJ.QUANT</span>
      <div style={{ display: 'flex', alignItems: 'stretch', height: 26 }}>
        {MODULES.map((m) => {
          const isCur = m.id === current;
          return (
            <div key={m.id}
              onClick={() => m.enabled && nav(m.to)}
              title={m.enabled ? '' : `${m.label} · 计划 ${m.phase}`}
              style={{
                padding: '0 12px', display: 'inline-flex', alignItems: 'center', gap: 5,
                cursor: m.enabled ? 'pointer' : 'not-allowed',
                color: isCur ? 'var(--fg)' : m.enabled ? 'var(--fg-2)' : 'var(--fg-4)',
                background: isCur ? 'var(--surface)' : 'transparent',
                borderTop: isCur ? '1px solid var(--fg)' : '1px solid transparent',
                borderLeft: isCur ? '1px solid var(--border)' : 'none',
                borderRight: isCur ? '1px solid var(--border)' : 'none',
                fontWeight: isCur ? 500 : 400, letterSpacing: '0.04em', marginBottom: -1,
              }}>
              <span>{m.label}</span>
              {!m.enabled && <span style={{ fontSize: 9, color: 'var(--fg-mute)' }}>{m.phase}</span>}
            </div>
          );
        })}
      </div>
      <div style={{ flex: 1 }} />
      <span style={{ color: envColor, letterSpacing: '0.10em' }}>● {env}</span>
      <span style={{ color: 'var(--fg-mute)', margin: '0 12px' }}>│</span>
      <span style={{ color: 'var(--fg-3)' }}>{user?.username}@{user?.nickname || 'data'} · {user?.role}</span>
      <span style={{ color: 'var(--fg-mute)', margin: '0 12px' }}>│</span>
      <button className="btn ghost" onClick={toggle} title="切换主题" data-testid="topbar-theme-toggle"
        style={{ height: 18, padding: '0 6px', fontSize: 11 }}>{theme === 'dark' ? '☀' : '☾'}</button>
      <button className="btn ghost" onClick={logout} title="登出" data-testid="topbar-logout"
        style={{ height: 18, padding: '0 6px', fontSize: 10, color: 'var(--fg-3)' }}>登出</button>
      <span style={{ color: 'var(--fg-mute)', margin: '0 6px 0 6px' }}>│</span>
      <span className="kbd" style={{ fontSize: 9, height: 14, lineHeight: '14px' }}>⌘K</span>
    </div>
  );
}

export function TerminalSidebar({ module = 'data', current }) {
  const nav = module === 'viz' ? VIZ_NAV : module === 'factor' ? FACTOR_NAV
    : module === 'observ' ? OBSERV_NAV : DATA_NAV;
  const moduleLabel =
    module === 'viz' ? '数据可视化' : module === 'factor' ? '因子管理'
      : module === 'observ' ? '可观测' : '数据管理';
  // 总览 / 拓扑 在哨兵 SSE 实时在线时点亮 LIVE 徽标 (可观测层重构方案 §7A.7)。
  // shared singleton, so the extra subscription is free (StatusBar already uses it).
  const { source } = useObsStatus();
  const liveBadge = module === 'observ' && source === 'live';
  return (
    <aside style={{
      width: 184, background: 'var(--surface-2)', borderRight: '1px solid var(--border)',
      display: 'flex', flexDirection: 'column', flexShrink: 0,
    }}>
      <div style={{
        padding: '12px 14px 8px', fontFamily: 'var(--font-mono)', fontSize: 10,
        color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.08em',
        borderBottom: '1px solid var(--border-faint)',
      }}>{moduleLabel}</div>
      <nav style={{ padding: '6px', flex: 1 }}>
        {nav.map((it) => {
          const isCur = it.id === current;
          // dynamic LIVE badge on 总览/拓扑 when the sentinel SSE frame is live
          const badge = it.badge || (liveBadge && (it.id === 'overview' || it.id === 'topology') ? 'LIVE' : null);
          return (
            <Link key={it.id} to={it.to} data-testid={`sidebar-item-${it.id}`} style={{
              display: 'flex', alignItems: 'center', gap: 8, padding: '5px 8px', margin: '1px 0',
              borderRadius: 4, background: isCur ? 'var(--surface)' : 'transparent',
              color: isCur ? 'var(--fg)' : 'var(--fg-2)', fontSize: 12,
              fontWeight: isCur ? 500 : 400,
              boxShadow: isCur ? 'inset 0 0 0 1px var(--border)' : 'none', position: 'relative',
            }}>
              {isCur && <span style={{ position: 'absolute', left: -6, top: 6, bottom: 6, width: 2, background: 'var(--fg)' }} />}
              <NavGlyph id={it.id} />
              <span style={{ flex: 1 }}>{it.label}</span>
              {badge && (
                <span style={{
                  fontFamily: 'var(--font-mono)', fontSize: 8.5,
                  color: badge === 'NEW' ? 'var(--ok)' : 'var(--info)', letterSpacing: '0.05em',
                }}>{badge}</span>
              )}
              {!badge && it.hot && (
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 8.5, color: 'var(--info)', letterSpacing: '0.05em' }}>HOT</span>
              )}
              {!badge && !it.hot && it.warn && (
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 8.5, color: 'var(--warn)' }}>!</span>
              )}
            </Link>
          );
        })}
      </nav>
      <div style={{
        borderTop: '1px solid var(--border-faint)', padding: '8px 10px',
        fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)',
        display: 'flex', flexDirection: 'column', gap: 4,
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
          <span>命令面板</span><span className="kbd" style={{ fontSize: 9, height: 14, lineHeight: '14px' }}>⌘K</span>
        </div>
      </div>
    </aside>
  );
}

// Maps a breadcrumb label to the route it should jump to. Lets every parent
// crumb act like a path segment in a file explorer — click it to go up to that
// level — without each page having to wire its own targets. Intermediate crumbs
// follow the trailing-slash convention (e.g. 'library/'). A page can still pass
// an explicit { label, to } crumb to override or extend this map.
const CRUMB_ROUTES = {
  'data_mgmt/': '/data/dashboard',
  'factor_mgmt/': '/factors/library',
  'viz/': '/data/charts',
  'settings/': '/settings',
  'library/': '/factors/library',
  'batches/': '/data/batches',
  'tables/': '/data/tables',
  'sources/': '/data/sources',
  'logs/': '/data/logs',
  'kline/': '/data/charts/kline',
};

export function SubToolbar({ path = [], fkeys = [], right }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', padding: '0 14px', height: 30,
      background: 'var(--surface-2)', borderBottom: '1px solid var(--border)',
      fontFamily: 'var(--font-mono)', fontSize: 11, flexShrink: 0,
    }}>
      <div style={{ display: 'inline-flex', alignItems: 'center' }}>
        {path.map((p, i) => {
          // A crumb may be a plain string or { label, to }. Parent crumbs become
          // clickable when a target is known (explicit `to`, or the label is in
          // CRUMB_ROUTES), jumping straight to that level — like the path bar in
          // a file explorer. The last crumb is the current page, never a link.
          const isLast = i === path.length - 1;
          const label = typeof p === 'string' ? p : p.label;
          const to = (typeof p === 'string' ? null : p.to) ?? CRUMB_ROUTES[label] ?? null;
          const clickable = !isLast && !!to;
          // Strip a label's own trailing slash; the "/" separator below draws the
          // divider, so keeping both would render a redundant double slash. The
          // raw `label` (with slash) is still used for the CRUMB_ROUTES lookup.
          const display = label.replace(/\/+$/, '');
          // Shared typography. Crumb COLOR is intentionally left to CSS for
          // clickable crumbs so `.crumb-link:hover` can recolor them (an inline
          // color would win over the hover rule).
          const typo = {
            fontWeight: isLast ? 500 : 400,
            letterSpacing: isLast ? 0 : '0.04em',
            textTransform: isLast ? 'none' : 'uppercase',
          };
          return (
            <React.Fragment key={i}>
              {clickable ? (
                // A real <a> via react-router's <Link>: keyboard-accessible,
                // open-in-new-tab / copy-link, and no inline color so hover applies.
                <Link to={to} className="crumb-link" style={typo}>{display}</Link>
              ) : (
                <span style={{ color: isLast ? 'var(--fg)' : 'var(--fg-3)', ...typo }}>{display}</span>
              )}
              {!isLast && <span style={{ color: 'var(--fg-mute)', margin: '0 6px' }}>/</span>}
            </React.Fragment>
          );
        })}
      </div>
      {fkeys.length > 0 && (
        <>
          <span style={{ color: 'var(--fg-mute)', margin: '0 12px' }}>│</span>
          {fkeys.map((b, i) => (
            <span key={i} onClick={b.onClick}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 4, marginRight: 14, color: b.active ? 'var(--fg)' : 'var(--fg-3)', cursor: b.onClick ? 'pointer' : 'default', fontWeight: b.active ? 500 : 400 }}>
              <span className="kbd">{b.k}</span>
              <span style={{ color: b.active ? 'var(--fg)' : 'var(--fg-2)' }}>{b.l}</span>
            </span>
          ))}
        </>
      )}
      <div style={{ flex: 1 }} />
      {right || (
        <>
          <span style={{ color: 'var(--fg-3)' }}>tz: <span style={{ color: 'var(--fg)' }}>Asia/Shanghai</span></span>
          <span style={{ color: 'var(--fg-mute)', margin: '0 10px' }}>│</span>
          <SystemStatusPill />
        </>
      )}
    </div>
  );
}

// 右上角系统状态标识 (可观测层重构方案 §7.5 / §7A.0②): 双源合流裁决 (哨兵存活/基建优先,
// 后端叠加告警明细)。按「最差层级状态」渲染 live / degraded / alert 三态,点进可观测页。
// 两路皆断时整体置 down 并提示「实时观测中断」,绝不伪绿。
export function SystemStatusPill() {
  const nav = useNavigate();
  const { status, firing, source } = useObsStatus();
  const map = {
    ok: { c: 'var(--ok)', txt: '正常', en: source === 'delayed' ? '轮询' : 'live' },
    degraded: { c: 'var(--warn)', txt: '降级', en: firing ? `· ${firing}` : '' },
    down: { c: 'var(--err)', txt: source === 'stale' ? '观测中断' : '故障', en: firing ? `· ${firing}` : '' },
  };
  const m = map[status] || map.ok;
  const to = status === 'ok' ? '/observ/overview' : '/observ/alerts';
  const title = source === 'stale' ? '实时观测中断 · 哨兵与后端均不可达 · 点击进入可观测'
    : source === 'delayed' ? '系统状态 (SSE 断, 轮询回退) · 点击进入可观测'
      : '系统状态 (哨兵实时裁决) · 点击进入可观测';
  return (
    <span data-testid="system-status-pill" data-state={status} data-source={source}
      onClick={() => nav(to)} title={title}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer',
        padding: '1px 9px', borderRadius: 10, lineHeight: '15px',
        border: '1px solid ' + (status === 'ok' ? 'var(--border)' : m.c),
        background: status === 'ok' ? 'transparent' : `color-mix(in oklch, ${m.c} 12%, transparent)`,
        color: m.c, fontWeight: 500,
      }}>
      <span style={{
        width: 6, height: 6, borderRadius: '50%', background: m.c,
        animation: status === 'down' ? 'pulse 1.3s ease-in-out infinite' : 'none',
      }} />
      <span>{m.txt}</span>
      {m.en && <span style={{ color: status === 'ok' ? 'var(--fg-3)' : m.c, fontWeight: 400 }}>{m.en}</span>}
    </span>
  );
}

export function PageTitleBar({ title, subtitle, actions, badge }) {
  return (
    <div style={{
      padding: '12px 18px', background: 'var(--surface)', borderBottom: '1px solid var(--border)',
      display: 'flex', alignItems: 'flex-end', gap: 14, flexShrink: 0,
    }}>
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <h1 style={{ margin: 0, fontFamily: 'var(--font-mono)', fontSize: 17, fontWeight: 500, letterSpacing: '-0.005em', color: 'var(--fg)' }}>{title}</h1>
          {badge}
        </div>
        {subtitle && <div style={{ marginTop: 3, fontSize: 11, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)' }}>{subtitle}</div>}
      </div>
      <div style={{ flex: 1 }} />
      {actions && <div style={{ display: 'flex', gap: 6 }}>{actions}</div>}
    </div>
  );
}

// Footer trace indicator (doc §8.8.2): shows the most recent API trace_id,
// admin-only; click to copy + jump to the trace detail in the log centre.
function TraceChip() {
  const { user } = useAuth();
  const nav = useNavigate();
  const [traceId, setTraceId] = React.useState(getLastTraceId());
  const [copied, setCopied] = React.useState(false);
  React.useEffect(() => {
    const on = (e) => setTraceId(e.detail);
    window.addEventListener('zjq:trace', on);
    return () => window.removeEventListener('zjq:trace', on);
  }, []);
  if (!user || user.role !== 'admin' || !traceId) return null;
  const short = traceId.replace(/^t_/, '').slice(-8);
  return (
    <span
      data-testid="statusbar-trace"
      title={`${traceId} · 点击进入日志中心`}
      onClick={() => {
        navigator.clipboard?.writeText(traceId);
        setCopied(true);
        setTimeout(() => setCopied(false), 800);
        nav(`/observ/trace/${traceId}`);
      }}
      style={{ cursor: 'pointer', color: 'var(--info)' }}
    >
      trace: <span style={{ color: 'var(--fg-2)' }}>t_{short}</span>{copied ? ' ✓' : ' ⎘'}
    </span>
  );
}

// A single health point. state ∈ 'ok' | 'warn' | 'error' | undefined(加载/未知).
// data-testid / data-state are e2e hooks so assertions don't depend on copy.
function CompDot({ label, state, title, pulse }) {
  const cls = state === 'ok' ? 'ok' : state === 'warn' ? 'warn'
            : state === 'error' ? 'err' : 'running';
  const txt = state === 'ok' ? 'ok' : state === 'warn' ? 'degraded'
            : state === 'error' ? 'down' : '…';
  return (
    <span data-testid={`status-${label}`} data-state={state || 'loading'} title={title}>
      <span className={`dot ${cls}`} style={pulse ? { animation: 'pulse 1.4s ease-in-out infinite' } : undefined} /> {label}: {txt}
    </span>
  );
}

export function StatusBar({ middle, right }) {
  const nav = useNavigate();
  // 状态栏数据源：双源合流 (可观测层重构方案 §7.2/§7A.0②) —— 首选哨兵 SSE 实时帧 (存活/
  // 基建真相, 绕开 backend), 回退 /monitor/health 轮询。某层 reachable=false 立即染红,
  // backend 在线时叠加告警明细。两路皆断 ⇒ 整体 down + 「实时观测中断」, 绝不伪绿。
  const { layers, health, firing, source, sentinelOnline } = useObsStatus();
  const comp = health?.components || {};
  const compState = (s) => (source === 'stale' ? 'error' : s);

  // hover 即见根因线索 (reachable / cpu / uptime / 重启 / 上次成功)。
  const reach = (k) => (layers[k]?.reachable === false ? '不可达' : layers[k]?.reachable === true ? '可达' : '—');
  const beTip = (() => {
    const b = layers.backend || {};
    return `后端 (web-backend) — 探测 ${reach('backend')}${b.cpu_pct != null ? ` · cpu ${b.cpu_pct}%` : ''}`;
  })();
  const dataTip = (() => {
    const d = layers.data || {}; const hd = d.health || {};
    const bits = [`探测 ${reach('data')}`];
    if (hd.uptime_s != null) bits.push(`uptime ${Math.round(hd.uptime_s / 60)}m`);
    if (d.restart_count) bits.push(`重启 ×${d.restart_count}`);
    if (d.oom_killed) bits.push('OOM');
    if (hd.last_job_success_at) bits.push(`上次成功 ${String(hd.last_job_success_at).slice(0, 16)}`);
    if (d.cpu_pct != null) bits.push(`cpu ${d.cpu_pct}%`);
    if (d.mem_pct != null) bits.push(`mem ${d.mem_pct}%`);
    return `数据层 (data-keeper) ${bits.join(' · ')}`;
  })();
  const monTip = `监控层 — 哨兵: ${sentinelOnline ? '在线' : '离线/未上线'} · 实时: ${source === 'live' ? '哨兵 SSE' : source === 'delayed' ? '轮询回退' : '中断'}`;

  const label = `${health?.cluster || '—'}${health?.environment ? ` · ${health.environment}` : ''}`;

  return (
    <footer data-testid="status-bar" style={{
      height: 22, background: 'var(--surface-2)', borderTop: '1px solid var(--border)',
      padding: '0 14px', display: 'flex', alignItems: 'center', gap: 13,
      fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', flexShrink: 0,
    }}>
      {/* 5 层存活/裁决点 (哨兵真相 + 后端叠加) */}
      <CompDot label="frontend" state={layers.frontend?.state} title={`前端 — 探测 ${reach('frontend')}`} />
      <CompDot label="backend" state={layers.backend?.state} title={beTip} pulse={layers.backend?.reachable === false} />
      <CompDot label="data" state={layers.data?.state} title={dataTip} pulse={layers.data?.reachable === false} />
      <CompDot label="factor" state={layers.factor?.state}
        title={`因子层 — 探测 ${reach('factor')}${layers.factor?.cpu_pct != null ? ` · cpu ${layers.factor.cpu_pct}%` : ''} · mem ${layers.factor?.mem_pct ?? '—'}%`} />
      <CompDot label="monitor" state={layers.monitor?.state} title={monTip} />
      <span style={{ color: 'var(--fg-mute)' }}>│</span>
      {/* 子组件 (元数据库 / 数据湖) */}
      <CompDot label="db" state={compState(comp.db?.state)} title="web-backend 元数据库 (审计 + app_log)" />
      <CompDot label="warehouse" state={compState(comp.warehouse?.state)} title="parquet 数据湖 /data (所有数据读取)" />
      <span style={{ color: 'var(--fg-mute)' }}>│</span>
      <span data-testid="status-cluster">{label}</span>
      {middle && (<><span style={{ color: 'var(--fg-mute)' }}>│</span>{middle}</>)}
      <div style={{ flex: 1 }} />
      {firing > 0 && (
        <span data-testid="statusbar-alerts" onClick={() => nav('/observ/alerts')}
          title={`${firing} 条触发中告警 · 点击查看`}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 5, cursor: 'pointer',
            padding: '0 8px', borderRadius: 8, lineHeight: '14px',
            background: 'var(--err-bg)', border: '1px solid var(--err)', color: 'var(--err)', fontWeight: 600,
          }}>⚠ {firing}</span>
      )}
      <TraceChip />
      <span style={{ color: 'var(--fg-mute)' }}>│</span>
      <ObsRealtime state={source} mini />
      {right || (
        <>
          <span className="kbd">?</span>
          <span style={{ color: 'var(--fg-mute)' }}>shortcuts</span>
        </>
      )}
    </footer>
  );
}
