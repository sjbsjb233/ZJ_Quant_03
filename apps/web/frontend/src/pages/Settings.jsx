import React, { useState } from 'react';
import { TerminalShell, TPanel } from '../components/shell';
import { absTime } from '../lib/format';
import { useAuth } from '../lib/auth';
import { useTheme } from '../lib/theme';

export default function Settings() {
  const [tab, setTab] = useState('profile');
  const { user } = useAuth();
  const { theme, setTheme, toggle } = useTheme();

  const u = user || {};

  const TABS = [
    { id: 'profile', l: '个人信息' },
    { id: 'prefs', l: '偏好设置' },
    { id: 'notif', l: '通知设置' },
  ];

  // Avatar initials from nickname or username
  const initials = (u.nickname || u.username || '?')
    .split('').filter((_, i) => i < 2).join('').toUpperCase();

  return (
    <TerminalShell
      module="data"
      current="overview"
      path={['settings/', 'profile']}
      fkeys={[
        { k: 'F1', l: '个人' },
        { k: 'F3', l: '偏好' },
        { k: 'F4', l: '通知' },
      ]}
      title="我的设置"
      subtitle={`${u.username || '—'} · ${u.role || '—'} · joined ${u.created_at ? u.created_at.slice(0, 10) : '—'}`}
    >
      {/* Tab bar */}
      <div style={{
        height: 30, padding: '0 14px',
        background: 'var(--surface-2)', borderBottom: '1px solid var(--border)',
        display: 'flex', alignItems: 'flex-end', flexShrink: 0,
      }}>
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)} style={{
            height: 30, padding: '0 14px',
            background: tab === t.id ? 'var(--surface)' : 'transparent',
            border: 'none',
            borderTop: tab === t.id ? '1px solid var(--fg)' : '1px solid transparent',
            borderLeft: tab === t.id ? '1px solid var(--border)' : 'none',
            borderRight: tab === t.id ? '1px solid var(--border)' : 'none',
            fontFamily: 'var(--font-mono)', fontSize: 11,
            color: tab === t.id ? 'var(--fg)' : 'var(--fg-3)',
            cursor: 'pointer', marginBottom: -1,
          }}>{t.l}</button>
        ))}
      </div>

      <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1fr 1fr', minHeight: 0 }}>
        {tab === 'profile' && <ProfileTab user={u} initials={initials} />}
        {tab === 'prefs' && <PrefsTab theme={theme} setTheme={setTheme} toggle={toggle} />}
        {tab === 'notif' && <NotifTab />}
      </div>
    </TerminalShell>
  );
}

function ProfileTab({ user: u, initials }) {
  // Derive avatar background from username for consistency
  const avatarBg = 'oklch(0.65 0.06 60)';

  const fields = [
    { k: 'username', v: u.username || '—', readonly: true },
    { k: 'email', v: u.email || '—', note: '改后需邮件验证' },
    { k: 'nickname', v: u.nickname || '—' },
    { k: 'role', v: u.role || '—', readonly: true, note: '需联系管理员变更' },
    { k: 'joined', v: u.created_at ? u.created_at.slice(0, 10) : '—', readonly: true },
  ];

  return (
    <TPanel title="PROFILE">
      <div style={{ padding: 18, fontFamily: 'var(--font-mono)' }}>
        {/* Avatar + identity row */}
        <div style={{ display: 'flex', gap: 18, marginBottom: 22, alignItems: 'flex-start' }}>
          <div style={{
            width: 76, height: 76, borderRadius: 4,
            background: avatarBg, color: '#fff',
            display: 'grid', placeItems: 'center',
            fontSize: 32, fontWeight: 600, flexShrink: 0,
          }}>{initials}</div>
          <div style={{ flex: 1 }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 16, fontWeight: 500, color: 'var(--fg)' }}>
              {u.nickname || u.username || '—'}
            </div>
            <div style={{ fontSize: 11, color: 'var(--fg-3)', marginTop: 2 }}>{u.email || '—'}</div>
            <div style={{ marginTop: 8, display: 'flex', gap: 4, flexWrap: 'wrap', alignItems: 'center' }}>
              {u.role && (
                <span className={`chip ${u.role === 'admin' ? 'ok' : u.role === 'editor' ? 'info' : 'neutral'}`} style={{ fontSize: 10 }}>
                  {u.role}
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Fields */}
        {fields.map(f => (
          <div key={f.k} style={{ marginBottom: 10 }}>
            <label style={{ fontSize: 10, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', display: 'block', marginBottom: 3 }}>{f.k}</label>
            <div style={{
              height: 28, padding: '0 10px',
              background: f.readonly ? 'var(--surface-2)' : 'var(--surface)',
              border: '1px solid var(--border)', borderRadius: 3,
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              fontSize: 12, color: f.readonly ? 'var(--fg-3)' : 'var(--fg)',
            }}>
              <span>{f.v}</span>
              {f.note && <span style={{ fontSize: 9.5, color: 'var(--fg-4)' }}>{f.note}</span>}
            </div>
          </div>
        ))}

        <div style={{ marginTop: 14, padding: '8px 10px', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3, fontSize: 10.5, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)' }}>
          个人信息修改功能待上线 · 如需变更请联系管理员
        </div>
      </div>
    </TPanel>
  );
}

function PrefsTab({ theme, setTheme, toggle }) {
  const THEME_OPTS = [
    { value: 'light', label: '浅色 · light' },
    { value: 'dark', label: '深色 · dark' },
  ];

  const DISPLAY_PREFS = [
    { k: '默认页面', v: '总览 Dashboard' },
    { k: '表格密度', v: '紧凑 · compact' },
    { k: '时间显示', v: '相对时间 + hover 绝对' },
    { k: '数字格式', v: '千分位 + 紧凑 (K/M/B)' },
    { k: 'K线涨跌色', v: '中国 (红涨 / 绿跌)' },
    { k: '语言', v: '简体中文' },
    { k: '快捷键', v: '启用 vim-like 导航' },
  ];

  return (
    <>
      <TPanel title="PREFERENCES">
        <div style={{ padding: 14, fontFamily: 'var(--font-mono)' }}>
          {/* Theme toggle – functional */}
          <div style={{
            padding: '10px 0 12px', borderBottom: '2px solid var(--border-strong)', marginBottom: 12,
          }}>
            <div style={{ fontSize: 10, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>主题</div>
            <div style={{ display: 'flex', gap: 8 }}>
              {THEME_OPTS.map(opt => (
                <button
                  key={opt.value}
                  onClick={() => setTheme(opt.value)}
                  style={{
                    height: 28, padding: '0 14px',
                    background: theme === opt.value ? 'var(--fg)' : 'var(--surface-2)',
                    color: theme === opt.value ? 'var(--bg)' : 'var(--fg-2)',
                    border: `1px solid ${theme === opt.value ? 'var(--fg)' : 'var(--border)'}`,
                    borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 11, cursor: 'pointer',
                  }}
                >
                  {opt.label}
                </button>
              ))}
              <button
                onClick={toggle}
                style={{
                  height: 28, padding: '0 12px',
                  background: 'transparent', color: 'var(--info)',
                  border: '1px solid var(--border)', borderRadius: 3,
                  fontFamily: 'var(--font-mono)', fontSize: 10.5, cursor: 'pointer',
                }}
              >
                切换 ⇄
              </button>
            </div>
            <div style={{ marginTop: 6, fontSize: 10, color: 'var(--fg-3)' }}>
              当前: <span style={{ color: 'var(--fg)' }}>{theme === 'dark' ? '深色' : '浅色'}</span> · 自动保存到本地
            </div>
          </div>

          {/* Display-only preferences */}
          {DISPLAY_PREFS.map(s => (
            <div key={s.k} style={{
              padding: '8px 0', borderBottom: '1px solid var(--border-faint)',
              display: 'flex', alignItems: 'center', gap: 10,
            }}>
              <span style={{ flex: 1, color: 'var(--fg-2)', fontSize: 11.5 }}>{s.k}</span>
              <div style={{
                height: 26, padding: '0 10px',
                background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3,
                display: 'flex', alignItems: 'center', gap: 8, color: 'var(--fg)', fontSize: 11,
              }}>
                {s.v} <span style={{ color: 'var(--fg-3)' }}>▾</span>
              </div>
            </div>
          ))}

          <div style={{ marginTop: 14, fontSize: 10.5, color: 'var(--fg-3)' }}>
            除主题外，其余偏好设置功能待上线
          </div>
        </div>
      </TPanel>

      <TPanel title="KEYBOARD SHORTCUTS" meta="vim-like" last>
        <div style={{ padding: 14, fontFamily: 'var(--font-mono)', fontSize: 11 }}>
          {[
            ['g d', '跳转到 Dashboard'],
            ['g b', '跳转到 Batches 台账'],
            ['g q', '跳转到 Quality 质量'],
            ['g m', '跳转到 Maintenance'],
            ['/', '全局搜索'],
            ['?', '显示快捷键帮助'],
            ['F1–F5', '功能键（页内操作）'],
          ].map(([k, l]) => (
            <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '5px 0', borderBottom: '1px solid var(--border-faint)' }}>
              <kbd style={{
                fontFamily: 'var(--font-mono)', fontSize: 10.5,
                padding: '1px 6px', background: 'var(--surface-2)',
                border: '1px solid var(--border-strong)', borderRadius: 3,
                color: 'var(--fg)', minWidth: 40, textAlign: 'center',
              }}>{k}</kbd>
              <span style={{ color: 'var(--fg-2)', fontSize: 11 }}>{l}</span>
            </div>
          ))}
        </div>
      </TPanel>
    </>
  );
}

function NotifTab() {
  const rows = [
    { l: '批次失败',               site: true,  email: true,  slack: false },
    { l: '批次质量告警',           site: true,  email: false, slack: false },
    { l: '字段迁移完成 / 失败',    site: true,  email: true,  slack: false },
    { l: '维护任务完成 / 失败',    site: true,  email: true,  slack: false },
    { l: '别人作废了我的批次',     site: true,  email: true,  slack: true },
    { l: '权限变更',               site: true,  email: true,  slack: false },
  ];

  return (
    <>
      <TPanel title="NOTIFICATION MATRIX" meta="6 channels">
        <div style={{ padding: 14, fontFamily: 'var(--font-mono)' }}>
          {/* Header row */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '4px 0 8px', borderBottom: '1px solid var(--border-strong)', marginBottom: 4 }}>
            <span style={{ flex: 1, fontSize: 10, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>事件</span>
            {['站内', '邮件', 'Slack'].map(ch => (
              <span key={ch} style={{ fontSize: 10, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', width: 48, textAlign: 'center' }}>{ch}</span>
            ))}
          </div>

          {rows.map(n => (
            <div key={n.l} style={{
              padding: '7px 0', borderBottom: '1px solid var(--border-faint)',
              display: 'flex', alignItems: 'center', gap: 14,
            }}>
              <span style={{ flex: 1, color: 'var(--fg-2)', fontSize: 11.5 }}>{n.l}</span>
              {[n.site, n.email, n.slack].map((on, i) => (
                <span key={i} style={{
                  width: 48, textAlign: 'center',
                  fontSize: 13, color: on ? 'var(--ok)' : 'var(--fg-4)',
                  fontFamily: 'var(--font-mono)',
                }}>
                  {on ? '✓' : '○'}
                </span>
              ))}
            </div>
          ))}

          <div style={{ marginTop: 14, fontSize: 10.5, color: 'var(--fg-3)' }}>
            通知渠道配置功能待上线 · 如需修改请联系管理员
          </div>
        </div>
      </TPanel>

      <TPanel title="NOTIFICATION DELIVERY" meta="last 7 days" last>
        <div style={{ padding: 14, fontFamily: 'var(--font-mono)', fontSize: 11 }}>
          {[
            { ts: '2026-05-21 09:32', ch: '站内', evt: '批次质量告警', ok: true },
            { ts: '2026-05-21 03:01', ch: '邮件', evt: '维护任务完成', ok: true },
            { ts: '2026-05-20 22:51', ch: '站内', evt: '别人作废了我的批次', ok: true },
            { ts: '2026-05-20 22:51', ch: 'Slack', evt: '别人作废了我的批次', ok: false },
            { ts: '2026-05-20 09:14', ch: '站内', evt: '批次质量告警', ok: true },
            { ts: '2026-05-19 03:00', ch: '邮件', evt: '维护任务完成', ok: true },
          ].map((r, i) => (
            <div key={i} style={{
              display: 'flex', alignItems: 'center', gap: 10, padding: '5px 0',
              borderBottom: '1px solid var(--border-faint)', fontSize: 10.5,
            }}>
              <span style={{ color: r.ok ? 'var(--ok)' : 'var(--err)', fontSize: 11 }}>{r.ok ? '✓' : '✕'}</span>
              <span style={{ color: 'var(--fg-3)', whiteSpace: 'nowrap', fontSize: 10 }}>{r.ts}</span>
              <span className="chip neutral" style={{ fontSize: 9, height: 14 }}>{r.ch}</span>
              <span style={{ flex: 1, color: 'var(--fg-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.evt}</span>
            </div>
          ))}
        </div>
      </TPanel>
    </>
  );
}
