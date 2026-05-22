// Terminal chrome: module strip, sidebar, sub-toolbar, page title bar, status bar.
import React from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../../lib/auth';
import { useTheme } from '../../lib/theme';
import { getLastTraceId } from '../../lib/logger';
import { DATA_NAV, MODULES, NavGlyph, VIZ_NAV } from './nav';

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
  const nav = module === 'viz' ? VIZ_NAV : DATA_NAV;
  return (
    <aside style={{
      width: 184, background: 'var(--surface-2)', borderRight: '1px solid var(--border)',
      display: 'flex', flexDirection: 'column', flexShrink: 0,
    }}>
      <div style={{
        padding: '12px 14px 8px', fontFamily: 'var(--font-mono)', fontSize: 10,
        color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.08em',
        borderBottom: '1px solid var(--border-faint)',
      }}>{module === 'viz' ? '数据可视化' : '数据管理'}</div>
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
              {it.hot && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 8.5, color: 'var(--info)', letterSpacing: '0.05em' }}>HOT</span>}
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

export function StatusBar({ middle, right }) {
  return (
    <footer style={{
      height: 22, background: 'var(--surface-2)', borderTop: '1px solid var(--border)',
      padding: '0 14px', display: 'flex', alignItems: 'center', gap: 14,
      fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', flexShrink: 0,
    }}>
      <span><span className="dot ok" /> db: ok</span>
      <span><span className="dot ok" /> warehouse: ok</span>
      <span style={{ color: 'var(--fg-mute)' }}>│</span>
      <span>cluster: prod-east-1</span>
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
