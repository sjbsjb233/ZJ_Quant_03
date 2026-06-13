// rpTimeline.jsx — 模块 C：资源时间线（主图 + hover + 阶段背景 + 基线 + 准入事件）
// + 缩略导航条（框选/拖拽/拉伸/双击重置，最小选区 2s）+ 选区统计 (§3.4/§7.1).
// Ported from the Claude Design handoff (rp-timeline.jsx).
import React from 'react';
import { rpCol, rpThousand, rpMMSS, rpMB } from './rpFormat';
import { RPInfoDot, RP_TIPS } from './rpShared';

export function rpScale(domain, range) {
  const k = (range[1] - range[0]) / ((domain[1] - domain[0]) || 1);
  return (v) => range[0] + (v - domain[0]) * k;
}
export function rpClampSel(a, b, T) {
  if (b - a < 2) { const m = (a + b) / 2; a = m - 1; b = m + 1; }
  if (a < 0) { b -= a; a = 0; }
  if (b > T) { a -= (b - T); b = T; }
  return { t0: Math.max(0, a), t1: Math.min(T, b) };
}

// ── main timeline chart ──────────────────────────────────────────────────────
export function RPTimeline({ run, sel, onSel, width = 1380, height = 280, level = 'exact', fineLoading }) {
  const T = run.T || 1;
  const view = sel ? [sel.t0, sel.t1] : [0, T];
  const pad = { t: 24, r: 54, b: 30, l: 50 };
  const iw = width - pad.l - pad.r, ih = height - pad.t - pad.b;
  const pts = run.series.filter((p) => p.t >= view[0] - 1.6 && p.t <= view[1] + 1.6);
  const yMax = (Math.max(run.p90 || 0, ...pts.map((p) => p.rss), 1)) * 1.1;
  const x = rpScale(view, [pad.l, pad.l + iw]);
  const y = rpScale([0, yMax], [pad.t + ih, pad.t]);
  const [hover, setHover] = React.useState(null);
  const svgRef = React.useRef(null);

  function onMove(e) {
    const rect = svgRef.current.getBoundingClientRect();
    const px = e.clientX - rect.left;
    if (px < pad.l || px > pad.l + iw) { setHover(null); return; }
    const t = view[0] + (px - pad.l) / iw * (view[1] - view[0]);
    let best = pts[0];
    for (const p of pts) if (Math.abs(p.t - t) < Math.abs(best.t - t)) best = p;
    if (best) setHover({ p: best, px: x(best.t), py: y(best.rss) });
  }

  const path = (c) => pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p[c]).toFixed(1)}`).join('');
  const yTicks = [0.25, 0.5, 0.75, 1].map((f) => yMax * f);
  const xTicks = Array.from({ length: 6 }, (_, i) => view[0] + (view[1] - view[0]) * i / 5);

  return (
    <div style={{ position: 'relative' }} data-testid="rp-timeline">
      <svg ref={svgRef} width={width} height={height} style={{ display: 'block', cursor: 'crosshair' }}
        onMouseMove={onMove} onMouseLeave={() => setHover(null)}
        onDoubleClick={() => onSel && onSel(null)}>
        {/* stage background bands */}
        {run.stages.map((s) => {
          const a = Math.max(s.from, view[0]), b = Math.min(s.to, view[1]);
          if (b <= a) return null;
          const x0 = x(a), x1 = x(b), w = x1 - x0;
          return (
            <g key={s.id}>
              <rect x={x0} y={pad.t} width={w} height={ih} fill={rpCol(s.id, 0.06)} />
              <rect x={x0} y={pad.t} width={w} height={2.5} fill={rpCol(s.id, 0.55)} />
              <line x1={x1} y1={pad.t} x2={x1} y2={pad.t + ih} stroke="var(--border)" strokeWidth="1" />
              {w >= 50
                ? <text x={x0 + 5} y={pad.t + 12} style={{ fontFamily: 'var(--font-mono)', fontSize: 8.5, fill: rpCol(s.id), letterSpacing: '0.05em' }}>{s.id}</text>
                : <g><rect x={x0} y={pad.t} width={Math.max(1, w)} height={ih} fill="transparent"><title>{`阶段 ${s.id} · ${rpMMSS(s.from)}–${rpMMSS(s.to)}`}</title></rect></g>}
            </g>
          );
        })}
        {/* y grid + ticks */}
        {yTicks.map((v, i) => (
          <g key={i}>
            <line x1={pad.l} y1={y(v)} x2={pad.l + iw} y2={y(v)} stroke="var(--border-faint)" strokeWidth="1" />
            <text x={pad.l - 6} y={y(v) + 3} textAnchor="end" style={{ fontFamily: 'var(--font-mono)', fontSize: 9, fill: 'var(--fg-4)' }}>{rpThousand(v)}</text>
          </g>
        ))}
        <text x={pad.l - 6} y={pad.t - 8} textAnchor="end" style={{ fontFamily: 'var(--font-mono)', fontSize: 8.5, fill: 'var(--fg-4)' }}>MB</text>
        {/* baselines p50 / p90 — absolute MB (v1.2 §7.1②), only when present */}
        {[['p50', run.p50], ['p90', run.p90]].map(([l, v]) => v != null && v <= yMax && (
          <g key={l}>
            <line x1={pad.l} y1={y(v)} x2={pad.l + iw} y2={y(v)} stroke="var(--fg-3)" strokeWidth="1" strokeDasharray="5 4" opacity="0.55" />
            <text x={pad.l + iw + 5} y={y(v) + 3} style={{ fontFamily: 'var(--font-mono)', fontSize: 9, fill: 'var(--fg-3)' }}>{l}</text>
          </g>
        ))}
        {/* arrow allocation curve — exact runs only (§7.1④) */}
        {level === 'exact' && <path d={path('arrow')} fill="none" stroke="var(--info)" strokeWidth="1" opacity="0.55" strokeDasharray="1 0" />}
        {/* main RSS curve */}
        <path d={path('rss')} fill="none" stroke="var(--fg)" strokeWidth="1.6" />
        {/* admission events */}
        {(run.events || []).filter((e) => e.t >= view[0] && e.t <= view[1]).map((e, i) => {
          const ex = x(e.t), ey = pad.t + ih - 7;
          const release = e.kind === 'release';
          return (
            <g key={i} style={{ cursor: 'help' }}>
              <path d={release ? `M${ex - 4.5},${ey + 7} L${ex + 4.5},${ey + 7} L${ex},${ey} Z` : `M${ex - 4.5},${ey} L${ex + 4.5},${ey} L${ex},${ey + 7} Z`}
                fill={release ? 'var(--ok)' : 'var(--warn)'} />
              <title>{`${e.label} · ${e.time}\n${RP_TIPS.admission}`}</title>
            </g>
          );
        })}
        {/* x axis */}
        <line x1={pad.l} y1={pad.t + ih} x2={pad.l + iw} y2={pad.t + ih} stroke="var(--border-strong)" strokeWidth="1" />
        {xTicks.map((t, i) => (
          <text key={i} x={x(t)} y={pad.t + ih + 16} textAnchor="middle" style={{ fontFamily: 'var(--font-mono)', fontSize: 9, fill: 'var(--fg-4)' }}>{rpMMSS(t)}</text>
        ))}
        {/* hover crosshair */}
        {hover && (
          <g pointerEvents="none">
            <line x1={hover.px} y1={pad.t} x2={hover.px} y2={pad.t + ih} stroke="var(--fg-3)" strokeWidth="1" strokeDasharray="2 3" />
            <circle cx={hover.px} cy={hover.py} r="3" fill="var(--surface)" stroke="var(--fg)" strokeWidth="1.4" />
          </g>
        )}
      </svg>
      {/* hover tooltip */}
      {hover && (
        <div style={{
          position: 'absolute', left: Math.min(hover.px + 14, width - 190), top: 28, width: 172, zIndex: 50, pointerEvents: 'none',
          background: 'var(--surface)', border: '1px solid var(--border-strong)', borderRadius: 5,
          boxShadow: '0 8px 24px rgba(20,18,14,0.14)', padding: '7px 10px',
          fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-2)', lineHeight: 1.7,
        }}>
          {[
            ['时间', rpMMSS(hover.p.t)],
            ['内存 RSS', rpMB(hover.p.rss)],
            ['CPU', Math.round(hover.p.cpu) + '%'],
            ...(level === 'exact' ? [['Arrow 内存', rpMB(hover.p.arrow)]] : []),
            ['并发', level === 'exact' ? '1' : String(hover.p.concShared)],
          ].map(([k, v]) => (
            <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 10 }}>
              <span style={{ color: 'var(--fg-3)' }}>{k}</span><span style={{ color: 'var(--fg)' }}>{v}</span>
            </div>
          ))}
        </div>
      )}
      {/* fine-data loading indicator (first zoom) */}
      {fineLoading && (
        <div style={{
          position: 'absolute', right: 60, top: 8, display: 'flex', alignItems: 'center', gap: 6,
          padding: '3px 9px', background: 'var(--info-bg)', border: '1px solid var(--info)', borderRadius: 4,
          fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--info)',
        }}>
          <span className="dot running" style={{ margin: 0, width: 5, height: 5 }}></span> 加载精细采样…
        </div>
      )}
    </div>
  );
}

// ── 缩略导航条（brush） ──────────────────────────────────────────────────────
export function RPBrushNav({ run, sel, onSel, width = 1380, height = 42, padL = 50, padR = 54, series, stages }) {
  const T = run.T || 1;
  const ser = series || run.series;
  const stg = stages || run.stages;
  const pad = { l: padL, r: padR, t: 5, b: 11 };
  const iw = width - pad.l - pad.r, ih = height - pad.t - pad.b;
  const yMax = (Math.max(...ser.map((p) => p.rss), 1)) * 1.08;
  const x = rpScale([0, T], [pad.l, pad.l + iw]);
  const y = rpScale([0, yMax], [pad.t + ih, pad.t]);
  const inv = (px) => (px - pad.l) / iw * T;
  const ref = React.useRef(null);
  const drag = React.useRef(null);
  const clamp = (t) => Math.max(0, Math.min(T, t));

  function down(e) {
    const rect = ref.current.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const t = clamp(inv(px));
    let mode = 'create';
    if (sel) {
      const x0 = x(sel.t0), x1 = x(sel.t1);
      if (Math.abs(px - x0) < 7) mode = 'l';
      else if (Math.abs(px - x1) < 7) mode = 'r';
      else if (px > x0 && px < x1) mode = 'move';
    }
    drag.current = { mode, anchor: t, sel0: sel ? { ...sel } : null };
    e.currentTarget.setPointerCapture(e.pointerId);
  }
  function move(e) {
    if (!drag.current) return;
    const rect = ref.current.getBoundingClientRect();
    const t = clamp(inv(e.clientX - rect.left));
    const d = drag.current;
    if (d.mode === 'create') {
      const a = Math.min(d.anchor, t), b = Math.max(d.anchor, t);
      if (b - a >= 0.4) onSel(rpClampSel(a, b, T));
    } else if (d.mode === 'move') {
      const off = t - d.anchor;
      let a = d.sel0.t0 + off, b = d.sel0.t1 + off;
      const len = b - a;
      if (a < 0) { a = 0; b = len; }
      if (b > T) { b = T; a = T - len; }
      onSel({ t0: a, t1: b });
    } else if (d.mode === 'l') {
      onSel(rpClampSel(Math.min(t, d.sel0.t1 - 2), d.sel0.t1, T));
    } else if (d.mode === 'r') {
      onSel(rpClampSel(d.sel0.t0, Math.max(t, d.sel0.t0 + 2), T));
    }
  }
  function up() { drag.current = null; }

  const path = ser.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p.rss).toFixed(1)}`).join('')
    + `L${x(T).toFixed(1)},${pad.t + ih}L${x(0).toFixed(1)},${pad.t + ih}Z`;

  return (
    <svg ref={ref} data-testid="rp-minimap" width={width} height={height} style={{ display: 'block', cursor: sel ? 'grab' : 'crosshair', touchAction: 'none' }}
      onPointerDown={down} onPointerMove={move} onPointerUp={up}>
      <rect x={pad.l} y={pad.t} width={iw} height={ih} fill="var(--surface-2)" stroke="var(--border)" rx="2" />
      <path d={path} fill="var(--fg-mute)" opacity="0.35" stroke="var(--fg-4)" strokeWidth="0.8" />
      {/* stage strip */}
      {stg.map((s) => (
        <rect key={s.id} x={x(s.from)} y={pad.t + ih + 2} width={Math.max(1, x(s.to) - x(s.from))} height={4} fill={rpCol(s.id, 0.65)}>
          <title>{s.id}</title>
        </rect>
      ))}
      {/* selection window */}
      {sel && (
        <g>
          <rect x={pad.l} y={pad.t} width={Math.max(0, x(sel.t0) - pad.l)} height={ih} fill="var(--bg)" opacity="0.62" />
          <rect x={x(sel.t1)} y={pad.t} width={Math.max(0, pad.l + iw - x(sel.t1))} height={ih} fill="var(--bg)" opacity="0.62" />
          <rect x={x(sel.t0)} y={pad.t} width={Math.max(2, x(sel.t1) - x(sel.t0))} height={ih} fill="none" stroke="var(--info)" strokeWidth="1.4" rx="2" />
          {[sel.t0, sel.t1].map((t, i) => (
            <g key={i} style={{ cursor: 'ew-resize' }}>
              <rect x={x(t) - 3} y={pad.t + ih / 2 - 8} width={6} height={16} rx={2} fill="var(--info)" />
              <line x1={x(t)} y1={pad.t + ih / 2 - 4} x2={x(t)} y2={pad.t + ih / 2 + 4} stroke="var(--bg)" strokeWidth="1" />
            </g>
          ))}
        </g>
      )}
    </svg>
  );
}

// ── 选区统计 ─────────────────────────────────────────────────────────────────
export function rpSelStats(run, sel) {
  const pts = run.series.filter((p) => p.t >= sel.t0 && p.t <= sel.t1);
  const peak = Math.max(...pts.map((p) => p.rss), 0);
  const meanRss = pts.reduce((s, p) => s + p.rss, 0) / (pts.length || 1);
  const meanCpu = pts.reduce((s, p) => s + p.cpu, 0) / (pts.length || 1);
  const stages = run.stages.filter((s) => s.to > sel.t0 && s.from < sel.t1).map((s) => s.id);
  return { dur: sel.t1 - sel.t0, peak, meanRss, meanCpu, stages };
}
export function RPSelStats({ run, sel, onClear }) {
  const s = rpSelStats(run, sel);
  const Item = ({ k, v }) => (
    <span style={{ display: 'inline-flex', alignItems: 'baseline', gap: 5 }}>
      <span style={{ color: 'var(--fg-3)', fontSize: 9.5 }}>{k}</span>
      <span style={{ color: 'var(--fg)', fontWeight: 500 }}>{v}</span>
    </span>
  );
  return (
    <div data-testid="rp-selection-stat" style={{
      display: 'flex', alignItems: 'center', gap: 16, padding: '5px 12px', flexWrap: 'wrap',
      background: 'var(--info-bg)', border: '1px solid var(--info)', borderRadius: 4,
      fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-2)',
    }}>
      <span style={{ color: 'var(--info)', fontWeight: 500 }}>◧ 选区 {rpMMSS(sel.t0)} – {rpMMSS(sel.t1)}</span>
      <Item k="区间时长" v={rpMMSS(s.dur)} />
      <Item k="区间峰值" v={rpMB(s.peak)} />
      <Item k="均值 RSS" v={rpMB(s.meanRss)} />
      <Item k="均值 CPU" v={Math.round(s.meanCpu) + '%'} />
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
        <span style={{ color: 'var(--fg-3)', fontSize: 9.5 }}>覆盖阶段</span>
        {s.stages.map((id) => (
          <span key={id} style={{ display: 'inline-flex', alignItems: 'center', gap: 3, color: rpCol(id), fontSize: 10 }}>
            <span style={{ width: 7, height: 7, borderRadius: 2, background: rpCol(id, 0.7) }}></span>{id}
          </span>
        ))}
      </span>
      <div style={{ flex: 1 }}></div>
      <span style={{ color: 'var(--fg-4)', fontSize: 9.5 }}>双击主图重置</span>
      {onClear && <button data-testid="rp-selection-clear" className="btn ghost sm" onClick={onClear} style={{ color: 'var(--info)' }}>✕ 清除选区</button>}
    </div>
  );
}

export function RPTimelineLegend({ level }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '4px 2px 0', fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', flexWrap: 'wrap' }}>
      <span><span style={{ display: 'inline-block', width: 16, height: 2, background: 'var(--fg)', verticalAlign: 2 }}></span> 内存 RSS</span>
      {level === 'exact' && <span><span style={{ display: 'inline-block', width: 16, height: 1.5, background: 'var(--info)', opacity: 0.6, verticalAlign: 2 }}></span> Arrow 内存分配</span>}
      <span>色带 = 执行阶段</span>
      <span>┄ p50 / p90 基线 <RPInfoDot tip={RP_TIPS.baseline} /></span>
      <span>▲▼ 准入事件 <RPInfoDot tip={RP_TIPS.admission} /></span>
      <div style={{ flex: 1 }}></div>
      <span style={{ color: 'var(--fg-4)' }}>下方导航条拖拽框选缩放 · 双击主图重置 · 缩放不持久化</span>
    </div>
  );
}
