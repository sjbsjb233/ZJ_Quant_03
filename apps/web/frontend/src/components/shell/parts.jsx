// Terminal chrome: module strip, sidebar, sub-toolbar, page title bar, status bar.
import React from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../../lib/auth';
import { useTheme } from '../../lib/theme';
import { getLastTraceId } from '../../lib/logger';
import { useSystemHealth } from '../../lib/health';
import { DATA_NAV, FACTOR_NAV, MODULES, NavGlyph, VIZ_NAV } from './nav';

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
  const nav = module === 'viz' ? VIZ_NAV : module === 'factor' ? FACTOR_NAV : DATA_NAV;
  const moduleLabel =
    module === 'viz' ? '数据可视化' : module === 'factor' ? '因子管理' : '数据管理';
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
              {it.badge && (
                <span style={{
                  fontFamily: 'var(--font-mono)', fontSize: 8.5,
                  color: it.badge === 'NEW' ? 'var(--ok)' : 'var(--info)', letterSpacing: '0.05em',
                }}>{it.badge}</span>
              )}
              {!it.badge && it.hot && (
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 8.5, color: 'var(--info)', letterSpacing: '0.05em' }}>HOT</span>
              )}
              {!it.badge && !it.hot && it.warn && (
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

export function SubToolbar({ path = [], fkeys = [], right }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', padding: '0 14px', height: 30,
      background: 'var(--surface-2)', borderBottom: '1px solid var(--border)',
      fontFamily: 'var(--font-mono)', fontSize: 11, flexShrink: 0,
    }}>
      <div style={{ display: 'inline-flex', alignItems: 'center' }}>
        {path.map((p, i) => (
          <React.Fragment key={i}>
            <span style={{
              color: i === path.length - 1 ? 'var(--fg)' : 'var(--fg-3)',
              fontWeight: i === path.length - 1 ? 500 : 400,
              letterSpacing: i === path.length - 1 ? 0 : '0.04em',
              textTransform: i === path.length - 1 ? 'none' : 'uppercase',
            }}>{p}</span>
            {i < path.length - 1 && <span style={{ color: 'var(--fg-mute)', margin: '0 6px' }}>/</span>}
          </React.Fragment>
        ))}
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
          <span style={{ color: 'var(--ok)' }}>● live</span>
        </>
      )}
    </div>
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
        nav(`/data/logs/trace/${traceId}`);
      }}
      style={{ cursor: 'pointer', color: 'var(--info)' }}
    >
      trace: <span style={{ color: 'var(--fg-2)' }}>t_{short}</span>{copied ? ' ✓' : ' ⎘'}
    </span>
  );
}

// A single health point. state ∈ 'ok' | 'warn' | 'error' | undefined(加载/未知).
// data-testid / data-state are e2e hooks so assertions don't depend on copy.
function CompDot({ label, state, title }) {
  const cls = state === 'ok' ? 'ok' : state === 'warn' ? 'warn'
            : state === 'error' ? 'err' : 'running';
  const txt = state === 'ok' ? 'ok' : state === 'warn' ? 'degraded'
            : state === 'error' ? 'down' : '…';
  return (
    <span data-testid={`status-${label}`} data-state={state || 'loading'} title={title}>
      <span className={`dot ${cls}`} /> {label}: {txt}
    </span>
  );
}

export function StatusBar({ middle, right }) {
  // 状态栏数据源：聚合下游健康 (修复 A1 · 五容器拓扑)。
  const { data, isError, isLoading } = useSystemHealth();
  const c = data?.components || {};
  // 整个 /health/deps 失败 ⇒ 各点按 down 渲染 (红),不停在绿色或加载态。
  const fail = (s) => (isError ? 'error' : s);

  // factor 三态：api 不通=error;api 通但 worker stale/down=warn;都好=ok。
  const factorState = (() => {
    if (isError || c.factor?.state === 'error') return 'error';
    if (!c.factor) return undefined;
    const w = c.factor.worker;
    return (w === 'stale' || w === 'down') ? 'warn' : 'ok';
  })();

  // 右侧标签：cluster · env。加载中显 …,下游探测失败仍能拿到 (来自缓存/上次)。
  const label = isLoading ? '…'
    : `${data?.cluster || '—'}${data?.environment ? ' · ' + data.environment : ''}`;

  return (
    <footer data-testid="status-bar" style={{
      height: 22, background: 'var(--surface-2)', borderTop: '1px solid var(--border)',
      padding: '0 14px', display: 'flex', alignItems: 'center', gap: 14,
      fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', flexShrink: 0,
    }}>
      <CompDot label="db" state={fail(c.db?.state)} title="web-backend 元数据库 (审计 + app_log)" />
      <CompDot label="warehouse" state={fail(c.warehouse?.state)} title="parquet 数据湖 /data (所有数据读取)" />
      {/* keeper 在回滚态 (KEEPER_BASE 未设) 后端不返回该键 ⇒ 自动不显示这个点;
          但 /health/deps 整体失败 (isError) 时仍要把它按 down 渲染 (红),不能让它消失。 */}
      {(isError || c.keeper) && (
        <CompDot label="keeper" state={fail(c.keeper?.state)}
          title={c.keeper?.scheduler === false
            ? '唯一写者 · 调度已关闭 (ENABLE_SCHEDULER=false)'
            : '唯一写者 (所有写操作 + 定时调度)'} />
      )}
      <CompDot label="factor" state={factorState}
        title={`因子模块 — api: ${c.factor?.state || '—'} · worker: ${c.factor?.worker || '—'}`} />
      <span style={{ color: 'var(--fg-mute)' }}>│</span>
      <span data-testid="status-cluster">{label}</span>
      {middle && (<><span style={{ color: 'var(--fg-mute)' }}>│</span>{middle}</>)}
      <div style={{ flex: 1 }} />
      <TraceChip />
      {right || (
        <>
          <span className="kbd">?</span>
          <span style={{ color: 'var(--fg-mute)' }}>shortcuts</span>
        </>
      )}
    </footer>
  );
}
