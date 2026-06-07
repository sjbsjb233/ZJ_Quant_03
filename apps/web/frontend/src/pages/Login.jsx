// Login — new "cosmic horizon" design (登陆页-新版), wired to the real /auth/login.
// Recreated pixel-for-pixel from the design handoff: full-bleed planet-rim background,
// left hero + feature row, right glass sign-in card. Auth behaviour is unchanged.
import React, { useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { useAuth } from '../lib/auth';
import { logger } from '../lib/logger';
import bgUrl from '../assets/login-bg.png';

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
    <>
      <style>{CSS}</style>
      <div className="login-stage" style={{ backgroundImage: `url(${bgUrl})` }}>
        <div className="login-page">

          {/* top bar */}
          <header className="login-topbar">
            <div className="login-brand">
              <span className="login-brand__mark">ZJ.QUANT</span>
              <span className="login-brand__tag">data ops platform</span>
            </div>
            <div className="login-env">
              <span className="login-pip" />
              <span>PROD</span>
            </div>
          </header>

          {/* body */}
          <div className="login-body">
            {/* left: hero + features */}
            <div className="login-left">
              <div className="login-hero-wrap">
                <div className="login-hero">
                  <span className="login-hero__mark">ZJ<span className="login-dot" /></span>
                  <span className="login-hero__tag">quant<span className="login-mini-dot" /></span>
                </div>
                <div className="login-hero__cn">多因子量化交易系统监控管理平台</div>
                <div className="login-hero__en">Data Ops Platform for Quant Trading</div>
              </div>

              <div className="login-features">
                {FEATURES.map((f) => (
                  <div className="login-feat" key={f.title}>
                    <span className="login-feat__icon">{f.icon}</span>
                    <div className="login-feat__title">{f.title}</div>
                    <div className="login-feat__sub">{f.sub}</div>
                  </div>
                ))}
              </div>
            </div>

            {/* right: login card */}
            <div className="login-right">
              <form className="login-card" autoComplete="off" onSubmit={submit}>
                <div className="login-card__head">
                  <span className="login-card__title"><span className="login-pip" />SIGN IN</span>
                  <span className="login-card__meta">AUTH · JWT</span>
                </div>

                <div className="login-field">
                  <label className="login-field__label" htmlFor="login-u">Username</label>
                  <input className="login-field__input" id="login-u" data-testid="login-username"
                    type="text" spellCheck={false} autoFocus
                    value={username} onChange={(e) => setUsername(e.target.value)} />
                </div>

                <div className="login-field">
                  <label className="login-field__label" htmlFor="login-p">Password</label>
                  <input className="login-field__input" id="login-p" data-testid="login-password"
                    type="password"
                    value={password} onChange={(e) => setPassword(e.target.value)} />
                </div>

                <button className="login-submit" type="submit" data-testid="login-submit" disabled={busy}>
                  {busy ? '...' : 'SIGN IN'} <span className="login-ret">↵</span>
                </button>

                {err && <div className="login-error">✕ {err}</div>}
              </form>
            </div>
          </div>

          {/* footer */}
          <footer className="login-footer">
            <span>© 2026 ZJ Quant · internal use only</span>
            <span>build · v0.2</span>
          </footer>

        </div>
      </div>
    </>
  );
}

const FEATURES = [
  {
    title: '实时监控', sub: '系统状态实时洞察',
    icon: (
      <svg width="24" height="24" viewBox="0 0 28 28" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M3 23h22" />
        <rect x="5" y="14" width="3.5" height="9" />
        <rect x="12" y="9" width="3.5" height="14" />
        <rect x="19" y="17" width="3.5" height="6" />
        <path d="M5 8l5-3 5 3 6-4" />
      </svg>
    ),
  },
  {
    title: '数据驱动', sub: '多维数据智能分析',
    icon: (
      <svg width="24" height="24" viewBox="0 0 28 28" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <circle cx="6" cy="20" r="2.2" />
        <circle cx="14" cy="9" r="2.2" />
        <circle cx="22" cy="16" r="2.2" />
        <circle cx="11" cy="22" r="1.4" />
        <path d="M7.6 18.6 12.4 10.6M15.6 10.4 20.4 14.8" />
      </svg>
    ),
  },
  {
    title: '策略管理', sub: '精细化策略全生命周期',
    icon: (
      <svg width="24" height="24" viewBox="0 0 28 28" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <rect x="4" y="5" width="9" height="7" rx="1" />
        <rect x="15" y="5" width="9" height="11" rx="1" />
        <rect x="4" y="14" width="9" height="9" rx="1" />
        <rect x="15" y="18" width="9" height="5" rx="1" />
      </svg>
    ),
  },
  {
    title: '稳定可靠', sub: '高可用架构保障运行',
    icon: (
      <svg width="24" height="24" viewBox="0 0 28 28" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M14 3l9 4.5v6c0 5.5-3.8 9.2-9 11-5.2-1.8-9-5.5-9-11v-6L14 3z" />
        <circle cx="14" cy="13" r="3.2" />
      </svg>
    ),
  },
];

function RedirectAfter({ nav, loc }) {
  React.useEffect(() => { nav(loc.state?.from || '/data/dashboard', { replace: true }); });
  return null;
}

const CSS = `
.login-stage {
  --l-fg: #f3efe7; --l-fg-2: #c8c2b3; --l-fg-3: #8e887a; --l-fg-4: #6a6457;
  --l-accent: #d4a974;
  --l-line: rgba(245, 240, 230, 0.10); --l-line-strong: rgba(245, 240, 230, 0.16);
  --l-font-sans: "Geist", -apple-system, BlinkMacSystemFont, system-ui, "PingFang SC", "Source Han Sans CN", sans-serif;
  --l-font-mono: "JetBrains Mono", "SF Mono", ui-monospace, Menlo, monospace;
  position: fixed; inset: 0;
  background: #0b0a07;
  background-size: cover; background-position: center; background-repeat: no-repeat;
  overflow: hidden;
  color: var(--l-fg);
  font-family: var(--l-font-sans);
  -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility;
}
.login-stage * { box-sizing: border-box; }
.login-stage::after {
  content: ""; position: absolute; inset: 0; pointer-events: none;
  background:
    radial-gradient(1200px 800px at 78% 46%, rgba(11,10,7,0.55), transparent 70%),
    linear-gradient(180deg, rgba(11,10,7,0.25), rgba(11,10,7,0.10) 30%, rgba(11,10,7,0.35));
}
.login-page {
  position: relative; z-index: 1; height: 100%;
  display: flex; flex-direction: column; padding: 26px 40px 22px;
}

/* top bar */
.login-topbar { display: flex; align-items: center; justify-content: space-between; }
.login-brand { display: flex; align-items: baseline; gap: 18px; white-space: nowrap; }
.login-brand__mark { font-family: var(--l-font-mono); font-weight: 700; font-size: 14px; letter-spacing: 0.10em; color: var(--l-fg); }
.login-brand__tag { font-family: var(--l-font-mono); font-size: 12px; letter-spacing: 0.10em; color: var(--l-fg-3); }
.login-env { display: inline-flex; align-items: center; gap: 8px; font-family: var(--l-font-mono); font-size: 12px; letter-spacing: 0.14em; color: var(--l-fg-2); }
.login-pip { width: 6px; height: 6px; border-radius: 50%; background: var(--l-accent); box-shadow: 0 0 6px 1px rgba(212,169,116,0.55); display: inline-block; }

/* body */
.login-body { flex: 1; display: flex; align-items: stretch; min-height: 0; }
.login-left { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.login-right { width: 540px; flex-shrink: 0; display: flex; align-items: center; justify-content: center; padding-right: 16px; }

/* hero */
.login-hero-wrap { flex: 1; display: flex; flex-direction: column; justify-content: center; align-items: center; text-align: center; padding-bottom: 10px; }
.login-hero { display: flex; flex-direction: column; align-items: center; }
.login-hero__mark { font-family: var(--l-font-sans); font-weight: 700; font-size: 108px; line-height: 0.92; letter-spacing: -0.04em; color: var(--l-fg); display: inline-flex; align-items: baseline; }
.login-hero__mark .login-dot { width: 13px; height: 13px; border-radius: 50%; background: var(--l-accent); margin: 0 0 13px 11px; display: inline-block; box-shadow: 0 0 12px 2px rgba(212,169,116,0.4); }
.login-hero__tag { font-family: var(--l-font-mono); font-weight: 500; font-size: 24px; letter-spacing: 0.42em; color: var(--l-accent); padding-left: 0.42em; margin-top: 12px; display: inline-flex; align-items: center; gap: 0.42em; }
.login-hero__tag .login-mini-dot { width: 4px; height: 4px; border-radius: 50%; background: var(--l-fg-4); display: inline-block; margin-left: 0.6em; }
.login-hero__cn { margin-top: 34px; font-size: 21px; font-weight: 400; letter-spacing: 0.14em; color: #d8d2c6; }
.login-hero__en { margin-top: 11px; font-family: var(--l-font-mono); font-size: 12px; letter-spacing: 0.28em; color: var(--l-fg-4); text-transform: uppercase; }

/* features */
.login-features { display: flex; align-items: flex-start; justify-content: flex-start; gap: 56px; padding: 0 0 6px 8px; }
.login-feat { display: flex; flex-direction: column; align-items: flex-start; gap: 11px; width: 116px; }
.login-feat__icon { color: #cfc8b8; opacity: 0.9; }
.login-feat__title { font-size: 14px; font-weight: 600; letter-spacing: 0.06em; color: var(--l-fg); }
.login-feat__sub { font-size: 11px; letter-spacing: 0.02em; color: var(--l-fg-3); line-height: 1.4; }

/* login card */
.login-card {
  width: 430px; border-radius: 8px; border: 1px solid var(--l-line-strong);
  background: linear-gradient(180deg, rgba(22,20,15,0.60), rgba(14,13,9,0.64));
  backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
  box-shadow: 0 24px 64px -24px rgba(0,0,0,0.6), inset 0 1px 0 rgba(245,240,230,0.03);
  padding: 26px 28px 26px;
}
.login-card__head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 24px; }
.login-card__title { display: inline-flex; align-items: center; gap: 10px; font-family: var(--l-font-mono); font-weight: 500; font-size: 12px; letter-spacing: 0.20em; color: var(--l-fg); white-space: nowrap; }
.login-card__meta { font-family: var(--l-font-mono); font-size: 11px; letter-spacing: 0.14em; color: var(--l-fg-4); white-space: nowrap; }

.login-field { margin-bottom: 16px; }
.login-field__label { display: block; font-family: var(--l-font-mono); font-size: 10px; letter-spacing: 0.16em; text-transform: uppercase; color: var(--l-fg-3); margin-bottom: 8px; }
.login-field__input {
  width: 100%; height: 42px; padding: 0 13px; border-radius: 6px;
  border: 1px solid var(--l-line-strong); background: rgba(245,240,230,0.03);
  color: var(--l-fg); font-family: var(--l-font-sans); font-size: 14px; letter-spacing: 0.01em;
  outline: none; transition: border-color 0.15s, background 0.15s;
}
.login-field__input::placeholder { color: var(--l-fg-4); }
.login-field__input:focus { border-color: rgba(212,169,116,0.5); background: rgba(245,240,230,0.05); }
input[type="password"].login-field__input { letter-spacing: 0.20em; font-size: 13px; }

.login-submit {
  width: 100%; height: 44px; margin-top: 6px; border: none; border-radius: 6px;
  background: #ece8e0; color: #14110c; font-family: var(--l-font-mono); font-weight: 600;
  font-size: 13px; letter-spacing: 0.20em; cursor: pointer;
  display: inline-flex; align-items: center; justify-content: center; gap: 10px;
  transition: background 0.15s, transform 0.08s;
}
.login-submit:hover { background: #ffffff; }
.login-submit:active { transform: translateY(1px); }
.login-submit:disabled { opacity: 0.6; cursor: default; }
.login-ret { font-size: 12px; color: #6a6457; }

.login-error {
  margin-top: 16px; padding: 9px 12px; border-radius: 6px;
  border: 1px solid rgba(212,116,96,0.45); background: rgba(212,116,96,0.10);
  font-family: var(--l-font-mono); font-size: 11px; letter-spacing: 0.04em; color: #e0a594;
}

/* footer */
.login-footer { display: flex; align-items: center; justify-content: space-between; font-family: var(--l-font-mono); font-size: 12px; letter-spacing: 0.06em; color: var(--l-fg-4); white-space: nowrap; }
`;
