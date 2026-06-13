// rpBasket.jsx — 页面二「篮子资源画像」区块 (§4/§7.2)
// 汇总卡 ×4 / 总曲线+子任务甘特(共享时间轴+框选缩放) / 终态图例 / 因子成本排行表.
// Ported from the Claude Design handoff (rp-basket.jsx).
import React from 'react';
import { rpCol, rpMB, rpThousand, rpMMSS, rpMidTrunc } from './rpFormat';
import { RPPanel, RPAttrBadge, RPTermChip, RP_TERM } from './rpShared';
import { rpScale, RPBrushNav } from './rpTimeline';

// ── 汇总卡 ───────────────────────────────────────────────────────────────────
export function BKCards({ bk, narrow, onOpenRun }) {
  const c = bk.cards;
  const top = c.top || {};
  const cards = [
    {
      l: '进程峰值（物理真值）', v: rpMB(c.peak),
      sub: c.limit ? <span>limit {rpThousand(c.limit)} MB · 占 <b style={{ color: 'var(--fg)' }}>{c.limitPct}%</b></span> : '进程整体读数，无归因问题',
    },
    { l: 'CPU 合计（精确）', v: `${(c.cpuTotal || 0).toFixed(1)} s`, sub: '所有子任务 CPU 时间之和' },
    {
      l: '最贵因子',
      v: top.name ? <span onClick={() => onOpenRun && top.run_id && onOpenRun(top.run_id)} style={{ cursor: top.run_id ? 'pointer' : 'default', textDecoration: 'underline', textDecorationColor: 'var(--border-strong)', textUnderlineOffset: 3 }} title="点击跳转该因子 run 详情">{top.name} <span style={{ fontSize: 12, color: 'var(--fg-3)' }}>→</span></span> : '—',
      sub: top.cpu != null ? `${top.cpu.toFixed(1)} s CPU · 点击跳该 run 详情` : '—',
    },
    { l: '并发峰值', v: String(c.concPeak), sub: <RPAttrBadge level={c.attr > 1 ? 'shared' : 'exact'} n={c.attr} sm /> },
  ];
  return (
    <div style={{ display: 'grid', gridTemplateColumns: narrow ? '1fr 1fr' : 'repeat(4, 1fr)', gap: 1, background: 'var(--border)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'visible' }}>
      {cards.map((cd, i) => (
        <div key={i} style={{ background: 'var(--surface)', padding: '9px 14px 10px' }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{cd.l}</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: i === 0 ? 22 : 19, fontWeight: 500, color: 'var(--fg)', fontVariantNumeric: 'tabular-nums', lineHeight: 1.2, marginTop: 4 }}>{cd.v}</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', marginTop: 3, minHeight: 15, display: 'flex', alignItems: 'center' }}>{cd.sub}</div>
        </div>
      ))}
    </div>
  );
}

// ── 总曲线 + 甘特（共享时间轴） ──────────────────────────────────────────────
export function BKSharedAxis({ bk, factors, sel, onSel, width = 1380, narrow, onOpenRun }) {
  const T = bk.T || 1;
  const view = sel ? [sel.t0, sel.t1] : [0, T];
  const pad = { l: narrow ? 110 : 138, r: 50 };
  const iw = width - pad.l - pad.r;
  const x = rpScale(view, [pad.l, pad.l + iw]);
  const curveH = 138, cPad = { t: 14, b: 4 };
  const cih = curveH - cPad.t - cPad.b;
  const yMax = (Math.max(...bk.series.map((p) => p.rss), 1)) * 1.1;
  const y = rpScale([0, yMax], [cPad.t + cih, cPad.t]);
  const pts = bk.series.filter((p) => p.t >= view[0] - 2 && p.t <= view[1] + 2);
  const [hover, setHover] = React.useState(null);
  const [barHover, setBarHover] = React.useState(null);
  const svgRef = React.useRef(null);

  const N = factors.length;
  const rowH = N > 10 ? 16 : 24;
  const barH = rowH - (N > 10 ? 6 : 9);
  const ganttH = N * rowH + 24;

  function onCurveMove(e) {
    const rect = svgRef.current.getBoundingClientRect();
    const px = e.clientX - rect.left;
    if (px < pad.l || px > pad.l + iw) { setHover(null); return; }
    const t = view[0] + (px - pad.l) / iw * (view[1] - view[0]);
    let best = pts[0];
    for (const p of pts) if (Math.abs(p.t - t) < Math.abs(best.t - t)) best = p;
    if (best) setHover({ p: best, px: x(best.t), py: y(best.rss) });
  }

  const xTicks = Array.from({ length: 6 }, (_, i) => view[0] + (view[1] - view[0]) * i / 5);

  function barFill(state) {
    switch (state) {
      case 'success': return { fill: 'var(--ok)', opacity: 0.5, stroke: 'var(--ok)' };
      case 'degraded': return { fill: 'var(--warn)', opacity: 0.55, stroke: 'var(--warn)' };
      case 'failed': return { fill: 'var(--err)', opacity: 0.55, stroke: 'var(--err)' };
      case 'blocked': return { fill: 'url(#bk-hatch)', opacity: 1, stroke: 'var(--warn)' };
      case 'cancelled': return { fill: 'none', opacity: 1, stroke: 'var(--fg-4)', dash: '3 2.5' };
      default: return { fill: 'var(--fg-4)', opacity: 0.4, stroke: 'var(--fg-4)' };
    }
  }

  return (
    <div style={{ position: 'relative' }}>
      {/* total curve */}
      <svg ref={svgRef} width={width} height={curveH} style={{ display: 'block', cursor: 'crosshair' }}
        onMouseMove={onCurveMove} onMouseLeave={() => setHover(null)}
        onDoubleClick={() => onSel && onSel(null)}>
        <text x={pad.l - 8} y={cPad.t + 2} textAnchor="end" style={{ fontFamily: 'var(--font-mono)', fontSize: 8.5, fill: 'var(--fg-4)' }}>MB</text>
        {[0.33, 0.66, 1].map((f, i) => (
          <g key={i}>
            <line x1={pad.l} y1={y(yMax * f)} x2={pad.l + iw} y2={y(yMax * f)} stroke="var(--border-faint)" />
            <text x={pad.l - 6} y={y(yMax * f) + 3} textAnchor="end" style={{ fontFamily: 'var(--font-mono)', fontSize: 9, fill: 'var(--fg-4)' }}>{rpThousand(yMax * f)}</text>
          </g>
        ))}
        <path d={pts.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p.rss).toFixed(1)}`).join('')} fill="none" stroke="var(--fg)" strokeWidth="1.5" />
        <text x={pad.l + 4} y={cPad.t + 2} style={{ fontFamily: 'var(--font-mono)', fontSize: 8.5, fill: 'var(--fg-3)' }}>进程内存总曲线（RSS）</text>
        {hover && (
          <g pointerEvents="none">
            <line x1={hover.px} y1={cPad.t} x2={hover.px} y2={curveH} stroke="var(--fg-3)" strokeDasharray="2 3" />
            <circle cx={hover.px} cy={hover.py} r="3" fill="var(--surface)" stroke="var(--fg)" strokeWidth="1.4" />
          </g>
        )}
      </svg>

      {/* gantt — same x scale, glued underneath */}
      <svg width={width} height={ganttH} style={{ display: 'block' }} onMouseLeave={() => setBarHover(null)}>
        <defs>
          <pattern id="bk-hatch" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <rect width="5" height="5" fill="var(--warn-bg)" />
            <line x1="0" y1="0" x2="0" y2="5" stroke="var(--warn)" strokeWidth="1.6" />
          </pattern>
        </defs>
        {hover && <line x1={hover.px} y1={0} x2={hover.px} y2={N * rowH} stroke="var(--fg-3)" strokeDasharray="2 3" pointerEvents="none" />}
        {factors.map((f, i) => {
          const yr = i * rowH;
          const from = f.from ?? 0, to = f.to ?? 0;
          const a = Math.max(from, view[0]), b = Math.min(to, view[1]);
          const visible = b > a && to > from;
          const x0 = visible ? x(a) : 0;
          const wpx = visible ? Math.max(6, x(b) - x0) : 0;
          const st = barFill(f.state);
          const label = `${f.name}@${f.ver}`;
          return (
            <g key={i}>
              {i % 2 === 1 && <rect x={pad.l} y={yr} width={iw} height={rowH} fill="var(--hover)" />}
              <text x={pad.l - 8} y={yr + rowH / 2 + 3} textAnchor="end"
                style={{ fontFamily: 'var(--font-mono)', fontSize: N > 10 ? 8.5 : 10, fill: f.legacy ? 'var(--fg-4)' : 'var(--fg-2)' }}>
                <title>{label}</title>
                {rpMidTrunc(label, narrow ? 13 : 17)}
              </text>
              {visible && (
                <rect x={x0} y={yr + (rowH - barH) / 2} width={wpx} height={barH} rx="2"
                  fill={st.fill === 'none' ? 'transparent' : st.fill} fillOpacity={st.fill === 'none' ? 0 : st.opacity}
                  stroke={st.stroke} strokeWidth="1" strokeDasharray={st.dash || '0'}
                  style={{ cursor: f.run_id ? 'pointer' : 'default' }}
                  onMouseEnter={() => setBarHover({ f, x: Math.min(x0, width - 280), y: curveH + yr + rowH })}
                  onClick={() => onOpenRun && f.run_id && onOpenRun(f.run_id)}>
                  <title>点击跳转该 run 详情（落点 = 页面一资源画像区块）</title>
                </rect>
              )}
            </g>
          );
        })}
        {/* shared bottom axis */}
        <line x1={pad.l} y1={N * rowH + 4} x2={pad.l + iw} y2={N * rowH + 4} stroke="var(--border-strong)" />
        {xTicks.map((t, i) => (
          <g key={i}>
            <line x1={x(t)} y1={N * rowH + 4} x2={x(t)} y2={N * rowH + 8} stroke="var(--border-strong)" />
            <text x={x(t)} y={N * rowH + 19} textAnchor="middle" style={{ fontFamily: 'var(--font-mono)', fontSize: 9, fill: 'var(--fg-4)' }}>{rpMMSS(t)}</text>
          </g>
        ))}
      </svg>

      {/* curve hover tooltip */}
      {hover && (
        <div style={{
          position: 'absolute', left: Math.min(hover.px + 12, width - 170), top: 16, zIndex: 50, pointerEvents: 'none',
          width: 150, background: 'var(--surface)', border: '1px solid var(--border-strong)', borderRadius: 5,
          boxShadow: '0 8px 24px rgba(20,18,14,0.14)', padding: '6px 10px',
          fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-2)', lineHeight: 1.7,
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}><span style={{ color: 'var(--fg-3)' }}>时刻</span><span style={{ color: 'var(--fg)' }}>{rpMMSS(hover.p.t)}</span></div>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}><span style={{ color: 'var(--fg-3)' }}>进程内存</span><span style={{ color: 'var(--fg)' }}>{rpMB(hover.p.rss)}</span></div>
        </div>
      )}
      {/* gantt bar hover tooltip */}
      {barHover && (
        <div style={{
          position: 'absolute', left: barHover.x + 10, top: Math.min(barHover.y, curveH + ganttH - 110), zIndex: 55, pointerEvents: 'none',
          width: 248, background: 'var(--surface)', border: '1px solid var(--border-strong)', borderRadius: 5,
          boxShadow: '0 8px 24px rgba(20,18,14,0.16)', padding: '7px 10px',
          fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-2)', lineHeight: 1.7,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 }}>
            <span style={{ color: 'var(--fg)', fontWeight: 600 }}>{barHover.f.name}@{barHover.f.ver}</span>
            <RPTermChip state={barHover.f.state} sm />
          </div>
          {[
            ['起止', `${rpMMSS(barHover.f.from)} – ${rpMMSS(barHover.f.to)}`],
            ['时长', rpMMSS((barHover.f.to ?? 0) - (barHover.f.from ?? 0))],
            ['CPU', barHover.f.cpu == null ? '—' : barHover.f.cpu.toFixed(1) + ' s'],
          ].map(([k, v]) => (
            <div key={k} style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span style={{ color: 'var(--fg-3)' }}>{k}</span><span style={{ color: 'var(--fg)' }}>{v}</span>
            </div>
          ))}
          <div style={{ color: 'var(--info)', marginTop: 2 }}>点击 → 该 run 详情 · 资源画像区块</div>
        </div>
      )}
    </div>
  );
}

// ── 终态图例 ─────────────────────────────────────────────────────────────────
export function BKLegend() {
  const sw = (state) => {
    const m = {
      success: { background: 'var(--ok)', opacity: 0.5 },
      degraded: { background: 'var(--warn)', opacity: 0.55 },
      blocked: { background: 'repeating-linear-gradient(45deg, var(--warn-bg), var(--warn-bg) 2px, var(--warn) 2px, var(--warn) 3.4px)', border: '1px solid var(--warn)' },
      failed: { background: 'var(--err)', opacity: 0.55 },
      cancelled: { background: 'transparent', border: '1px dashed var(--fg-4)' },
    };
    return m[state];
  };
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 16, padding: '4px 2px 0', fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', flexWrap: 'wrap' }}>
      <span style={{ color: 'var(--fg-4)' }}>子任务终态</span>
      {Object.keys(RP_TERM).map((s) => (
        <span key={s} style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
          <span style={{ display: 'inline-block', width: 16, height: 8, borderRadius: 2, boxSizing: 'border-box', ...sw(s) }}></span>
          {RP_TERM[s].l}
        </span>
      ))}
      <div style={{ flex: 1 }}></div>
      <span style={{ color: 'var(--fg-4)' }}>甘特与总曲线严格共用横轴 · 下方导航条框选两图同步缩放 · 双击曲线重置</span>
    </div>
  );
}

// ── 因子成本排行表 ───────────────────────────────────────────────────────────
export function BKCostTable({ factors, onOpenRun }) {
  const rows = [...factors].sort((a, b) => (b.cpu ?? -1) - (a.cpu ?? -1));
  return (
    <RPPanel title="因子成本排行" meta="按 CPU(s) 倒序 · 排序固定（v1）· 点击整行跳 run 详情">
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--border)' }}>
            {[['因子', 'left'], ['CPU (s) ↓', 'right'], ['时长', 'right'], ['内存', 'right'], ['终态', 'left']].map(([h, al], i) => (
              <th key={h} style={{ textAlign: al, padding: '6px 14px', fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.04em', width: i === 4 ? 110 : undefined }}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((f, i) => (
            <tr key={f.name + i} className="iv-row" style={{ borderBottom: i < rows.length - 1 ? '1px solid var(--border-faint)' : 'none', height: 30, cursor: f.run_id ? 'pointer' : 'default' }}
              onClick={() => onOpenRun && f.run_id && onOpenRun(f.run_id)}
              title="点击整行跳转该 run 详情">
              <td style={{ padding: '0 14px', fontFamily: 'var(--font-mono)', color: 'var(--fg)', fontWeight: 500 }}>
                {f.name}<span style={{ color: 'var(--fg-4)', fontWeight: 400 }}>@{f.ver}</span>
                {f.legacy && <span className="chip neutral" style={{ height: 14, fontSize: 8.5, marginLeft: 7 }}>早于画像上线</span>}
              </td>
              <td style={{ padding: '0 14px', textAlign: 'right', fontFamily: 'var(--font-mono)', fontVariantNumeric: 'tabular-nums', color: f.cpu == null ? 'var(--fg-mute)' : 'var(--fg)', fontWeight: i === 0 && f.cpu != null ? 600 : 400 }}>
                {f.cpu == null ? '—' : f.cpu.toFixed(1)}
              </td>
              <td style={{ padding: '0 14px', textAlign: 'right', fontFamily: 'var(--font-mono)', fontVariantNumeric: 'tabular-nums', color: 'var(--fg-2)' }}>{rpMMSS((f.to ?? 0) - (f.from ?? 0))}</td>
              <td style={{ padding: '0 14px', textAlign: 'right', fontFamily: 'var(--font-mono)', fontVariantNumeric: 'tabular-nums' }}>
                {f.mem == null ? <span style={{ color: 'var(--fg-mute)' }}>—</span>
                  : f.mem.kind === 'exact' ? <span style={{ color: 'var(--fg)' }}>{rpMB(f.mem.v)}</span>
                  : <span style={{ color: 'var(--fg-2)' }}>区间峰值 {rpMB(f.mem.v)} <span className="chip neutral" style={{ height: 14, fontSize: 8.5, marginLeft: 4 }}>shared-{f.mem.n}</span></span>}
              </td>
              <td style={{ padding: '0 14px' }}><RPTermChip state={f.state} sm /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </RPPanel>
  );
}
