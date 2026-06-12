// Login — "cosmic horizon" design, dynamic edition (登陆页-动态版), wired to the real /auth/login.
// Recreated from the design handoff: full-bleed planet-rim background with restrained motion —
// staggered entrance choreography, twinkling-star + orbital-particle canvas, a breathing planet
// glow, subtle mouse parallax, a live CST clock and a one-time card sheen. All motion honours
// prefers-reduced-motion. Auth behaviour is unchanged.
import React, { useEffect, useRef, useState } from 'react';
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
  const [clock, setClock] = useState('--:--:-- CST');

  const bgRef = useRef(null);
  const heroRef = useRef(null);
  const canvasRef = useRef(null);

  // ── live clock (local time, labelled CST) ──
  useEffect(() => {
    const pad = (n) => (n < 10 ? '0' + n : '' + n);
    const tick = () => {
      const d = new Date();
      setClock(`${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())} CST`);
    };
    tick();
    const id = setInterval(tick, 1000);
    return () => clearInterval(id);
  }, []);

  // ── background canvas: twinkling stars + slow orbital particles + mouse parallax ──
  useEffect(() => {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const canvas = canvasRef.current;
    const bg = bgRef.current;
    const hero = heroRef.current;
    if (!canvas) return undefined;
    const ctx = canvas.getContext('2d');

    let W = 0, H = 0;
    const DPR = Math.min(window.devicePixelRatio || 1, 2);
    const resize = () => {
      W = window.innerWidth; H = window.innerHeight;
      canvas.width = W * DPR;
      canvas.height = H * DPR;
      ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
    };
    resize();
    window.addEventListener('resize', resize);

    // stars
    const N = 110;
    const stars = [];
    for (let i = 0; i < N; i++) {
      stars.push({
        x: Math.random(), y: Math.random(),
        r: 0.4 + Math.random() * 0.9,
        a: 0.10 + Math.random() * 0.45,
        sp: 0.3 + Math.random() * 0.9,
        ph: Math.random() * Math.PI * 2,
        drift: 0.0008 + Math.random() * 0.0018,
      });
    }

    // orbital particles — faint glowing points gliding on wide ellipses, echoing the artwork's rings
    const orbits = [
      { cx: 0.30, cy: 1.06, rx: 0.55, ry: 0.36, rot: -0.24, period: 90,  ph: 1.2, size: 2.0 },
      { cx: 0.08, cy: 0.40, rx: 0.36, ry: 0.21, rot: -0.55, period: 130, ph: 4.0, size: 1.6 },
      { cx: 0.30, cy: 1.06, rx: 0.55, ry: 0.36, rot: -0.24, period: 90,  ph: 4.3, size: 1.3 },
    ];
    const orbitPoint = (o, t) => {
      const ang = (t / o.period) * Math.PI * 2 + o.ph;
      const ex = Math.cos(ang) * o.rx * W;
      const ey = Math.sin(ang) * o.ry * H;
      const cos = Math.cos(o.rot), sin = Math.sin(o.rot);
      return { x: o.cx * W + ex * cos - ey * sin, y: o.cy * H + ex * sin + ey * cos };
    };

    // lerped mouse parallax
    let tx = 0, ty = 0, cx = 0, cy = 0;
    const onMove = (e) => {
      tx = e.clientX / window.innerWidth - 0.5;
      ty = e.clientY / window.innerHeight - 0.5;
    };
    if (!reduced) window.addEventListener('mousemove', onMove);

    const start = performance.now();
    let raf = 0;
    const frame = (now) => {
      const t = (now - start) / 1000;

      cx += (tx - cx) * 0.04;
      cy += (ty - cy) * 0.04;
      if (bg) bg.style.transform = `translate(${-cx * 14}px,${-cy * 10}px)`;
      if (hero) hero.style.transform = `translate(${cx * 6}px,${cy * 4}px)`;

      ctx.clearRect(0, 0, W, H);

      for (let i = 0; i < N; i++) {
        const s = stars[i];
        const x = ((s.x + t * s.drift * 0.01) % 1) * W - cx * 8 * s.r;
        const y = s.y * H - cy * 6 * s.r;
        const tw = 0.55 + 0.45 * Math.sin(t * s.sp + s.ph);
        ctx.globalAlpha = s.a * tw;
        ctx.fillStyle = '#f3efe7';
        ctx.beginPath();
        ctx.arc(x, y, s.r, 0, Math.PI * 2);
        ctx.fill();
      }

      for (let k = 0; k < orbits.length; k++) {
        const o = orbits[k];
        const p = orbitPoint(o, t);
        for (let j = 1; j <= 14; j++) {
          const q = orbitPoint(o, t - j * 0.22);
          ctx.globalAlpha = 0.10 * (1 - j / 14);
          ctx.fillStyle = '#e7c08a';
          ctx.beginPath();
          ctx.arc(q.x, q.y, o.size * (1 - j / 18), 0, Math.PI * 2);
          ctx.fill();
        }
        const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, o.size * 7);
        g.addColorStop(0, 'rgba(231,192,138,0.85)');
        g.addColorStop(0.35, 'rgba(212,169,116,0.25)');
        g.addColorStop(1, 'rgba(212,169,116,0)');
        ctx.globalAlpha = 1;
        ctx.fillStyle = g;
        ctx.beginPath();
        ctx.arc(p.x, p.y, o.size * 7, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = '#f0d4a8';
        ctx.beginPath();
        ctx.arc(p.x, p.y, o.size, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.globalAlpha = 1;

      if (!reduced) raf = requestAnimationFrame(frame);
    };

    if (reduced) frame(start);
    else raf = requestAnimationFrame(frame);

    return () => {
      window.removeEventListener('resize', resize);
      window.removeEventListener('mousemove', onMove);
      if (raf) cancelAnimationFrame(raf);
    };
  }, []);

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
      <div className="login-stage">
        {/* background layers */}
        <div className="login-stage__bg" ref={bgRef} style={{ backgroundImage: `url(${bgUrl})` }} />
        <div className="login-stage__limb" />
        <canvas className="login-stage__canvas" ref={canvasRef} />
        <div className="login-stage__veil" />

        <div className="login-page">

          {/* top bar */}
          <header className="login-topbar login-rise" style={{ '--d': '0.15s' }}>
            <div className="login-brand">
              <span className="login-brand__mark">ZJ.QUANT</span>
              <span className="login-brand__tag">data ops platform</span>
            </div>
            <div className="login-envgroup">
              <span className="login-clock">{clock}</span>
              <div className="login-env">
                <span className="login-pip" />
                <span>PROD</span>
              </div>
            </div>
          </header>

          {/* body */}
          <div className="login-body">
            {/* left: hero + features */}
            <div className="login-left">
              <div className="login-hero-wrap" ref={heroRef}>
                <div className="login-hero">
                  <span className="login-hero__mark login-rise" style={{ '--d': '0.35s' }}>
                    ZJ<span className="login-dot" />
                  </span>
                  <span className="login-hero__tag" aria-label="quant">
                    {'quant'.split('').map((ch, i) => (
                      <span className="login-ch" key={i} style={{ animationDelay: `${0.55 + i * 0.07}s` }}>{ch}</span>
                    ))}
                    <span className="login-mini-dot login-ch" style={{ animationDelay: `${0.55 + 5 * 0.07}s` }} />
                  </span>
                </div>
                <div className="login-hero__cn login-rise" style={{ '--d': '0.85s' }}>多因子量化交易系统监控管理平台</div>
                <div className="login-hero__en login-rise" style={{ '--d': '1.00s' }}>Data Ops Platform for Quant Trading</div>
              </div>

              <div className="login-features">
                {FEATURES.map((f, i) => (
                  <div className="login-feat login-rise" key={f.title} style={{ '--d': `${1.15 + i * 0.10}s` }}>
                    <span className="login-feat__icon">{f.icon}</span>
                    <div className="login-feat__title">{f.title}</div>
                    <div className="login-feat__sub">{f.sub}</div>
                  </div>
                ))}
              </div>
            </div>

            {/* right: login card */}
            <div className="login-right">
              <form className="login-card login-rise" style={{ '--d': '0.55s' }} autoComplete="off" onSubmit={submit}>
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
          <footer className="login-footer login-rise" style={{ '--d': '1.55s' }}>
            <span>© 2026 ZJ Quant · internal use only</span>
            <span>build · v0.3</span>
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
  --l-ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  position: fixed; inset: 0;
  background: #0b0a07;
  overflow: hidden;
  color: var(--l-fg);
  font-family: var(--l-font-sans);
  -webkit-font-smoothing: antialiased; text-rendering: optimizeLegibility;
}
.login-stage * { box-sizing: border-box; }

/* background layers */
.login-stage__bg {
  position: absolute; inset: -24px;            /* bleed so parallax never shows edges */
  background-size: cover; background-position: center; background-repeat: no-repeat;
  will-change: transform;
  animation: login-bg-in 2.4s var(--l-ease-out) both;
}
@keyframes login-bg-in {
  from { opacity: 0; transform: scale(1.025); }
  to   { opacity: 1; transform: scale(1); }
}
.login-stage__limb {
  position: absolute; left: -10%; bottom: -28%; width: 75%; height: 80%;
  background: radial-gradient(closest-side at 48% 52%,
    rgba(228, 178, 118, 0.16), rgba(212, 169, 116, 0.07) 42%, transparent 72%);
  mix-blend-mode: screen; pointer-events: none;
  animation: login-limb-breathe 9s ease-in-out infinite;
}
@keyframes login-limb-breathe {
  0%, 100% { opacity: 0.55; transform: scale(1); }
  50%      { opacity: 1;    transform: scale(1.03); }
}
.login-stage__canvas { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
.login-stage__veil {
  position: absolute; inset: 0; pointer-events: none;
  background:
    radial-gradient(1200px 800px at 78% 46%, rgba(11,10,7,0.55), transparent 70%),
    linear-gradient(180deg, rgba(11,10,7,0.25), rgba(11,10,7,0.10) 30%, rgba(11,10,7,0.35));
}

.login-page {
  position: relative; z-index: 1; height: 100%;
  display: flex; flex-direction: column; padding: 26px 40px 22px;
}

/* entrance choreography */
@media (prefers-reduced-motion: no-preference) {
  .login-rise {
    opacity: 0; transform: translateY(14px);
    animation: login-rise 1.1s var(--l-ease-out) forwards;
    animation-delay: var(--d, 0s);
  }
  @keyframes login-rise { to { opacity: 1; transform: translateY(0); } }
  .login-hero__tag .login-ch {
    opacity: 0; transform: translateY(6px);
    animation: login-rise 0.9s var(--l-ease-out) forwards;
  }
}

/* top bar */
.login-topbar { display: flex; align-items: center; justify-content: space-between; }
.login-brand { display: flex; align-items: baseline; gap: 18px; white-space: nowrap; }
.login-brand__mark { font-family: var(--l-font-mono); font-weight: 700; font-size: 14px; letter-spacing: 0.10em; color: var(--l-fg); }
.login-brand__tag { font-family: var(--l-font-mono); font-size: 12px; letter-spacing: 0.10em; color: var(--l-fg-3); }
.login-envgroup { display: flex; align-items: center; gap: 26px; }
.login-clock { font-family: var(--l-font-mono); font-size: 12px; letter-spacing: 0.14em; color: var(--l-fg-4); font-variant-numeric: tabular-nums; }
.login-env { display: inline-flex; align-items: center; gap: 8px; font-family: var(--l-font-mono); font-size: 12px; letter-spacing: 0.14em; color: var(--l-fg-2); }
.login-pip { width: 6px; height: 6px; border-radius: 50%; background: var(--l-accent); box-shadow: 0 0 6px 1px rgba(212,169,116,0.55); display: inline-block; }
@media (prefers-reduced-motion: no-preference) {
  .login-pip { animation: login-pip-pulse 3.6s ease-in-out infinite; }
  @keyframes login-pip-pulse {
    0%, 100% { box-shadow: 0 0 5px 1px rgba(212,169,116,0.35); }
    50%      { box-shadow: 0 0 9px 2px rgba(212,169,116,0.70); }
  }
}

/* body */
.login-body { flex: 1; display: flex; align-items: stretch; min-height: 0; }
.login-left { flex: 1; display: flex; flex-direction: column; min-width: 0; }
.login-right { width: 540px; flex-shrink: 0; display: flex; align-items: center; justify-content: center; padding-right: 16px; }

/* hero */
.login-hero-wrap { flex: 1; display: flex; flex-direction: column; justify-content: center; align-items: center; text-align: center; padding-bottom: 10px; will-change: transform; }
.login-hero { display: flex; flex-direction: column; align-items: center; }
.login-hero__mark { font-family: var(--l-font-sans); font-weight: 700; font-size: 108px; line-height: 0.92; letter-spacing: -0.04em; color: var(--l-fg); display: inline-flex; align-items: baseline; }
.login-hero__mark .login-dot { width: 13px; height: 13px; border-radius: 50%; background: var(--l-accent); margin: 0 0 13px 11px; display: inline-block; box-shadow: 0 0 12px 2px rgba(212,169,116,0.4); }
@media (prefers-reduced-motion: no-preference) {
  .login-hero__mark .login-dot { animation: login-dot-pulse 4.5s ease-in-out infinite; }
  @keyframes login-dot-pulse {
    0%, 100% { box-shadow: 0 0 10px 2px rgba(212,169,116,0.30); }
    50%      { box-shadow: 0 0 18px 4px rgba(212,169,116,0.60); }
  }
}
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
  padding: 26px 28px 26px; position: relative; overflow: hidden;
}
/* one-time sheen sweeping across the card top on load */
@media (prefers-reduced-motion: no-preference) {
  .login-card::before {
    content: ""; position: absolute; top: 0; left: 0; width: 40%; height: 1px;
    background: linear-gradient(90deg, transparent, rgba(212,169,116,0.7), transparent);
    animation: login-sheen 2.6s var(--l-ease-out) 1.1s both; pointer-events: none;
  }
  @keyframes login-sheen {
    from { transform: translateX(-110%); opacity: 0; }
    30%  { opacity: 1; }
    to   { transform: translateX(290%); opacity: 0; }
  }
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
  outline: none; transition: border-color 0.25s ease, background 0.25s ease, box-shadow 0.25s ease;
}
.login-field__input::placeholder { color: var(--l-fg-4); }
.login-field__input:focus { border-color: rgba(212,169,116,0.5); background: rgba(245,240,230,0.05); box-shadow: 0 0 0 3px rgba(212,169,116,0.08); }
input[type="password"].login-field__input { letter-spacing: 0.20em; font-size: 13px; }

.login-submit {
  width: 100%; height: 44px; margin-top: 6px; border: none; border-radius: 6px;
  background: #ece8e0; color: #14110c; font-family: var(--l-font-mono); font-weight: 600;
  font-size: 13px; letter-spacing: 0.20em; cursor: pointer;
  display: inline-flex; align-items: center; justify-content: center; gap: 10px;
  transition: background 0.2s ease, transform 0.1s ease, box-shadow 0.25s ease;
}
.login-submit:hover { background: #ffffff; box-shadow: 0 6px 24px -8px rgba(236,232,224,0.35); }
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
