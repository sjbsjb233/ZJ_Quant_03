// Login (dark terminal card) — wired to the real /auth/login.
import React, { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../lib/auth';
import { logger } from '../lib/logger';
import { ZjLogo } from '../components/ui/Logo';

export default function Login() {
  const { login, user } = useAuth();
  const nav = useNavigate();
  const loc = useLocation();
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('admin123');
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);

  if (user) return <RedirectAfter nav={nav} loc={loc} />;

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setErr(null);
    logger.userAction('ui.login.submit', { username });   // never log the password
    try {
      await login(username, password);
      nav(loc.state?.from || '/data/dashboard', { replace: true });
    } catch (e2) {
      setErr(e2.apiError?.message || '登录失败');
    } finally { setBusy(false); }
  };

  return (
    <div data-theme="dark" style={{
      width: '100%', height: '100%', background: 'var(--bg)', color: 'var(--fg)',
      display: 'flex', flexDirection: 'column', overflow: 'hidden', fontFamily: 'var(--font-mono)', fontSize: 12,
      backgroundImage: 'linear-gradient(rgba(255,250,240,0.025) 1px, transparent 1px), linear-gradient(90deg, rgba(255,250,240,0.025) 1px, transparent 1px)',
      backgroundSize: '40px 40px',
    }}>
      <div style={{ height: 28, padding: '0 14px', borderBottom: '1px solid var(--border)', background: 'var(--surface-2)', display: 'flex', alignItems: 'center', fontSize: 10.5 }}>
        <span style={{ color: 'var(--fg)', letterSpacing: '0.12em', fontWeight: 600 }}>ZJ.QUANT</span>
        <span style={{ color: 'var(--fg-mute)', margin: '0 12px' }}>│</span>
        <span style={{ color: 'var(--fg-3)' }}>data ops platform · v0.2</span>
        <div style={{ flex: 1 }} />
        <span style={{ color: 'var(--err)', letterSpacing: '0.10em' }}>● PROD</span>
      </div>

      <div style={{ flex: 1, display: 'grid', placeItems: 'center', padding: 40, minHeight: 0 }}>
        <form onSubmit={submit} style={{ width: 460 }}>
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', marginBottom: 32 }}>
            <ZjLogo size={64} inverse />
            <div style={{ fontSize: 11, color: 'var(--fg-3)', marginTop: 16, letterSpacing: '0.02em' }}>多因子量化交易系统监控管理平台</div>
          </div>

          <div style={{ background: 'var(--surface)', border: '1px solid var(--border-strong)', borderRadius: 4, padding: 24 }}>
            <div style={{ fontSize: 10.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.10em', marginBottom: 18, display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ color: 'var(--ok)' }}>●</span> SIGN IN<div style={{ flex: 1 }} /><span>auth · JWT</span>
            </div>

            <Field label="username">
              <input data-testid="login-username" value={username} onChange={(e) => setUsername(e.target.value)} autoFocus
                style={inputStyle} />
            </Field>
            <Field label="password">
              <input data-testid="login-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                style={inputStyle} />
            </Field>

            <button type="submit" data-testid="login-submit" disabled={busy} style={{
              width: '100%', height: 36, marginTop: 6, background: 'var(--fg)', color: 'var(--bg)', border: 'none',
              borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 500, letterSpacing: '0.05em',
              textTransform: 'uppercase', cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
              opacity: busy ? 0.6 : 1,
            }}>{busy ? '...' : 'SIGN IN'} <span>↵</span></button>

            {err && <div style={{ marginTop: 14, padding: '8px 10px', background: 'var(--err-bg)', border: '1px solid var(--err)', borderRadius: 3, fontSize: 10.5, color: 'var(--err)' }}>✕ {err}</div>}

            <div style={{ marginTop: 14, padding: '8px 10px', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3, fontSize: 10.5, color: 'var(--fg-3)' }}>
              demo · admin/admin123 · editor/editor123 · viewer/viewer123
            </div>
          </div>
          <div style={{ marginTop: 18, fontSize: 10, color: 'var(--fg-3)', display: 'flex', justifyContent: 'space-between' }}>
            <span>© 2026 ZJ Quant · internal use only</span>
            <span>build · v0.2</span>
          </div>
        </form>
      </div>
    </div>
  );
}

const inputStyle = {
  width: '100%', height: 34, padding: '0 12px', background: 'var(--surface-2)',
  border: '1px solid var(--border-strong)', borderRadius: 3, fontFamily: 'var(--font-mono)',
  fontSize: 13, color: 'var(--fg)', outline: 'none',
};

function Field({ label, children }) {
  return (
    <div style={{ marginBottom: 12 }}>
      <label style={{ fontSize: 10, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', display: 'block', marginBottom: 4 }}>{label}</label>
      {children}
    </div>
  );
}

function RedirectAfter({ nav, loc }) {
  React.useEffect(() => { nav(loc.state?.from || '/data/dashboard', { replace: true }); });
  return null;
}
