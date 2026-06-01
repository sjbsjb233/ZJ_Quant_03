// Tiny SVG chart primitives used across the factor pages — direct port of
// the design `pages-factor-shared.jsx` ICChart / BarChart / LineChart / Heatmap.
// Self-contained (no echarts dependency) so they render fast on tiny KPI grids.
import React from 'react';

// IC bars + cumulative line on a single SVG.
export function ICChart({ data, width = 720, height = 220, padding = { t: 10, r: 36, b: 24, l: 32 } }) {
  if (!data || !data.length) return <Empty mini label="无 IC 数据" />;
  const ics = data.map((d) => d.ic);
  const cumIc = data.reduce((acc, d, i) => {
    acc.push((acc[i - 1] || 0) + d.ic);
    return acc;
  }, []);
  const maxAbs = Math.max(...ics.map(Math.abs)) * 1.15 || 1;
  const cumMax = Math.max(...cumIc, 0) * 1.05 || 1;
  const cumMin = Math.min(0, ...cumIc) * 1.05;
  const W = width - padding.l - padding.r;
  const H = height - padding.t - padding.b;
  const bw = W / data.length;
  const y0 = padding.t + H / 2;
  const barScale = (H / 2) / maxAbs;
  const cumY = (v) => padding.t + H - ((v - cumMin) / (cumMax - cumMin || 1)) * H;
  return (
    <svg width={width} height={height} style={{ display: 'block' }} className="tnum">
      <g style={{ fontFamily: 'var(--font-mono)', fontSize: 9, fill: 'var(--fg-3)' }}>
        <text x={padding.l - 4} y={padding.t + 8} textAnchor="end">{maxAbs.toFixed(2)}</text>
        <text x={padding.l - 4} y={y0 + 3} textAnchor="end">0</text>
        <text x={padding.l - 4} y={padding.t + H - 1} textAnchor="end">-{maxAbs.toFixed(2)}</text>
      </g>
      <line x1={padding.l} y1={y0} x2={width - padding.r} y2={y0} stroke="var(--border-strong)" strokeWidth="0.5" />
      {data.map((d, i) => {
        const x = padding.l + i * bw;
        const h = Math.abs(d.ic) * barScale;
        const y = d.ic >= 0 ? y0 - h : y0;
        const positive = d.ic >= 0;
        return <rect key={i} x={x + bw * 0.15} y={y} width={bw * 0.7} height={Math.max(0.6, h)} fill={positive ? 'var(--ok)' : 'var(--err)'} opacity={0.6} />;
      })}
      <path
        d={data.map((d, i) => `${i === 0 ? 'M' : 'L'}${(padding.l + i * bw + bw / 2).toFixed(1)},${cumY(cumIc[i]).toFixed(1)}`).join(' ')}
        fill="none" stroke="var(--fg)" strokeWidth="1.3"
      />
      <g style={{ fontFamily: 'var(--font-mono)', fontSize: 9, fill: 'var(--fg-3)' }}>
        {data
          .filter((_, i) => i % Math.ceil(data.length / 8) === 0 || i === data.length - 1)
          .map((d) => {
            const idx = data.indexOf(d);
            return <text key={idx} x={padding.l + idx * bw + bw / 2} y={height - 8} textAnchor="middle">{d.label}</text>;
          })}
      </g>
    </svg>
  );
}

// Vertical bars (IC decay).
export function BarChart({ data, width = 360, height = 200, padding = { t: 10, r: 12, b: 28, l: 32 }, color = 'var(--info)' }) {
  if (!data || !data.length) return <Empty mini label="无衰减数据" />;
  const max = Math.max(...data.map((d) => d.v)) * 1.1 || 1;
  const min = Math.min(0, ...data.map((d) => d.v)) * 1.1;
  const W = width - padding.l - padding.r;
  const H = height - padding.t - padding.b;
  const bw = W / data.length;
  const y = (v) => padding.t + H - ((v - min) / (max - min || 1)) * H;
  const y0 = y(0);
  return (
    <svg width={width} height={height} style={{ display: 'block' }} className="tnum">
      <g style={{ fontFamily: 'var(--font-mono)', fontSize: 9, fill: 'var(--fg-3)' }}>
        <text x={padding.l - 4} y={padding.t + 8} textAnchor="end">{max.toFixed(3)}</text>
        <text x={padding.l - 4} y={y0 + 3} textAnchor="end">0</text>
      </g>
      <line x1={padding.l} y1={y0} x2={width - padding.r} y2={y0} stroke="var(--border-strong)" strokeWidth="0.5" />
      {data.map((d, i) => {
        const x = padding.l + i * bw;
        const yy = y(d.v);
        const h = Math.abs(yy - y0);
        return (
          <g key={i}>
            <rect x={x + bw * 0.2} y={Math.min(yy, y0)} width={bw * 0.6} height={Math.max(0.5, h)} fill={color} opacity={0.55} />
            <text x={x + bw / 2} y={height - 12} textAnchor="middle" style={{ fontFamily: 'var(--font-mono)', fontSize: 9, fill: 'var(--fg-3)' }}>{d.l}</text>
            <text x={x + bw / 2} y={Math.min(yy, y0) - 3} textAnchor="middle" style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, fill: 'var(--fg)' }}>{d.v.toFixed(3)}</text>
          </g>
        );
      })}
    </svg>
  );
}

// Multi-line plot — for layered returns + tracking.
export function LineChart({ series, width = 720, height = 240, padding = { t: 12, r: 50, b: 24, l: 32 }, colors }) {
  if (!series || !series.length || !series[0]?.data?.length) return <Empty mini label="无序列" />;
  const all = series.flatMap((s) => s.data);
  const max = Math.max(...all) * 1.05;
  const min = Math.min(...all) * 1.05;
  const W = width - padding.l - padding.r;
  const H = height - padding.t - padding.b;
  const n = series[0].data.length;
  const xStep = n > 1 ? W / (n - 1) : W;
  const y = (v) => padding.t + H - ((v - min) / (max - min || 1)) * H;
  const palette = colors || ['#cc4334', '#cc7a34', '#a89234', '#5a8a34', '#34a07a', '#3478a0', '#5a4ea0', '#8a4ea0'];
  return (
    <svg width={width} height={height} style={{ display: 'block' }} className="tnum">
      {[0, 0.25, 0.5, 0.75, 1].map((p, i) => {
        const v = min + (max - min) * p;
        const yy = y(v);
        return (
          <g key={i}>
            <line x1={padding.l} y1={yy} x2={width - padding.r} y2={yy} stroke="var(--border-faint)" strokeWidth="0.5" />
            <text x={padding.l - 4} y={yy + 3} textAnchor="end" style={{ fontFamily: 'var(--font-mono)', fontSize: 9, fill: 'var(--fg-3)' }}>{v.toFixed(2)}</text>
          </g>
        );
      })}
      {min < 0 && max > 0 && (
        <line x1={padding.l} y1={y(0)} x2={width - padding.r} y2={y(0)} stroke="var(--border-strong)" strokeWidth="0.7" />
      )}
      {series.map((s, idx) => {
        const c = s.color || palette[idx % palette.length];
        const d = s.data.map((v, i) => `${i === 0 ? 'M' : 'L'}${(padding.l + i * xStep).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
        const lastY = y(s.data[s.data.length - 1]);
        return (
          <g key={idx}>
            <path d={d} fill="none" stroke={c} strokeWidth={s.bold ? 1.6 : 1.1} opacity={s.dim ? 0.4 : 1} />
            <text x={width - padding.r + 4} y={lastY + 3} style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, fill: c, fontWeight: 500 }}>{s.label}</text>
          </g>
        );
      })}
      {series[0].xLabels && (
        <g style={{ fontFamily: 'var(--font-mono)', fontSize: 9, fill: 'var(--fg-3)' }}>
          {series[0].xLabels
            .filter((_, i) => i % Math.ceil(n / 6) === 0 || i === n - 1)
            .map((l) => {
              const idx = series[0].xLabels.indexOf(l);
              return <text key={idx} x={padding.l + idx * xStep} y={height - 8} textAnchor="middle">{l}</text>;
            })}
        </g>
      )}
    </svg>
  );
}

// Correlation matrix heatmap.
export function Heatmap({ matrix, labels, width = 600, height = 600, padding = { t: 100, r: 16, b: 16, l: 100 }, threshold = 0.7 }) {
  if (!matrix?.length) return <Empty mini label="无矩阵" />;
  const n = matrix.length;
  const W = width - padding.l - padding.r;
  const H = height - padding.t - padding.b;
  const cell = Math.min(W / n, H / n);
  const colorFor = (v) => {
    const a = Math.abs(v);
    if (v > 0) return `oklch(${0.96 - a * 0.42} ${0.05 + a * 0.10} 152 / 1)`;
    if (v < 0) return `oklch(${0.96 - a * 0.42} ${0.04 + a * 0.14} 27  / 1)`;
    return 'var(--surface-2)';
  };
  return (
    <svg width={width} height={height} style={{ display: 'block' }}>
      {labels.map((l, i) => (
        <text key={`c${i}`} x={padding.l + i * cell + cell / 2} y={padding.t - 8} textAnchor="start"
          transform={`rotate(-45 ${padding.l + i * cell + cell / 2} ${padding.t - 8})`}
          style={{ fontFamily: 'var(--font-mono)', fontSize: 10, fill: 'var(--fg-2)' }}>{l}</text>
      ))}
      {labels.map((l, i) => (
        <text key={`r${i}`} x={padding.l - 6} y={padding.t + i * cell + cell / 2 + 3} textAnchor="end"
          style={{ fontFamily: 'var(--font-mono)', fontSize: 10, fill: 'var(--fg-2)' }}>{l}</text>
      ))}
      {matrix.map((row, i) =>
        row.map((v, j) => {
          const x = padding.l + j * cell;
          const yy = padding.t + i * cell;
          const hi = Math.abs(v) >= threshold && i !== j;
          return (
            <g key={`${i}_${j}`}>
              <rect x={x} y={yy} width={cell - 1} height={cell - 1}
                fill={colorFor(v)} stroke={hi ? 'var(--err)' : 'var(--border-faint)'} strokeWidth={hi ? 1 : 0.5} />
              <text x={x + cell / 2} y={yy + cell / 2 + 3} textAnchor="middle"
                style={{ fontFamily: 'var(--font-mono)', fontSize: cell > 38 ? 10 : 9, fill: Math.abs(v) > 0.5 ? 'var(--fg)' : 'var(--fg-3)', fontVariantNumeric: 'tabular-nums' }}>
                {i === j ? '1.00' : v.toFixed(2)}
              </text>
            </g>
          );
        })
      )}
    </svg>
  );
}

// In-row tiny bars used by FactorLibrary's ic trend column.
export function MiniBars({ data, width = 80, height = 16 }) {
  if (!data || !data.length) return null;
  const max = Math.max(...data.map(Math.abs)) || 1;
  const bw = width / data.length;
  return (
    <svg width={width} height={height} style={{ display: 'block' }}>
      {data.map((v, i) => {
        const h = (Math.abs(v) / max) * (height - 2);
        const isPos = v >= 0;
        return <rect key={i} x={i * bw + bw * 0.1} y={isPos ? height / 2 - h : height / 2} width={bw * 0.8}
          height={Math.max(0.5, h)} fill={isPos ? 'var(--ok)' : 'var(--err)'} opacity={0.55} />;
      })}
      <line x1="0" y1={height / 2} x2={width} y2={height / 2} stroke="var(--border)" strokeWidth="0.5" />
    </svg>
  );
}

// ─── 运行透视 (Run Trace) primitives ─────────────────────────────────────────
// `Distribution` — a horizontal box-and-whisker built from a `describe()` summary
// ({min,p25,p50,p75,max,mean,std,n}). Used for ⑤'s 预处理四步分布. Each instance
// auto-scales to its own [min,max] (raw vs standardized have wildly different
// scales), drawing a dashed zero gridline only when 0 falls inside the range.
export function Distribution({ stats, width = 176, height = 30, tone = 'info' }) {
  if (!stats || !stats.n) {
    return (
      <div style={{ width, height, display: 'flex', alignItems: 'center', color: 'var(--fg-mute)', fontFamily: 'var(--font-mono)', fontSize: 10 }}>
        无有效数据
      </div>
    );
  }
  const lo = stats.min, hi = stats.max;
  const span = (hi - lo) || 1;
  const pad = span * 0.08;
  const d0 = lo - pad, d1 = hi + pad;
  const x = (v) => 4 + ((v - d0) / (d1 - d0)) * (width - 8);
  const cy = height / 2;
  const col = `var(--${tone})`;
  const showZero = d0 < 0 && d1 > 0;
  return (
    <svg width={width} height={height} style={{ display: 'block' }} className="tnum">
      {showZero && (
        <line x1={x(0)} y1={2} x2={x(0)} y2={height - 2} stroke="var(--border-strong)" strokeWidth="0.75" strokeDasharray="2 2" />
      )}
      {/* whisker min–max */}
      <line x1={x(stats.min)} y1={cy} x2={x(stats.max)} y2={cy} stroke={col} strokeWidth="1" opacity="0.5" />
      <line x1={x(stats.min)} y1={cy - 4} x2={x(stats.min)} y2={cy + 4} stroke={col} strokeWidth="1" opacity="0.6" />
      <line x1={x(stats.max)} y1={cy - 4} x2={x(stats.max)} y2={cy + 4} stroke={col} strokeWidth="1" opacity="0.6" />
      {/* box p25–p75 */}
      <rect x={x(stats.p25)} y={cy - 6} width={Math.max(1, x(stats.p75) - x(stats.p25))} height={12} fill={col} opacity="0.16" stroke={col} strokeWidth="0.9" />
      {/* median p50 */}
      <line x1={x(stats.p50)} y1={cy - 6} x2={x(stats.p50)} y2={cy + 6} stroke={col} strokeWidth="1.5" />
      {/* mean dot */}
      {stats.mean != null && (
        <circle cx={x(stats.mean)} cy={cy} r="2.1" fill="var(--surface)" stroke={col} strokeWidth="1.2" />
      )}
    </svg>
  );
}

// `FunnelBar` — the股票池漏斗 (block ③): per-step bars scaled by symbol count,
// each annotated with the剔除数 (delta vs previous) and a noop badge when the
// filter was requested but ineffective (字段缺失/全空).
export function FunnelBar({ steps, labelOf }) {
  if (!steps || !steps.length) return <Empty mini label="无漏斗数据" />;
  const max = Math.max(...steps.map((s) => s.symbols || 0), 1);
  return (
    <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 6 }}>
      {steps.map((f, i) => {
        const w = Math.max(4, ((f.symbols || 0) / max) * 100);
        const prev = i > 0 ? (steps[i - 1].symbols || 0) : null;
        const delta = prev != null ? (f.symbols || 0) - prev : null;
        const terminal = f.terminal || f.step === 'final';
        const ineffective = !!f.ineffective;
        return (
          <div key={i} title={f.note} style={{ display: 'grid', gridTemplateColumns: '118px 1fr 150px', alignItems: 'center', gap: 12 }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: terminal ? 'var(--fg)' : 'var(--fg-2)', fontWeight: terminal ? 500 : 400, textAlign: 'right' }}>
              {labelOf ? labelOf(f.step) : f.step}
            </div>
            <div style={{ position: 'relative', height: 22, display: 'flex', alignItems: 'center' }}>
              <div style={{
                width: `${w}%`, height: '100%', borderRadius: 3,
                background: ineffective ? 'var(--warn-bg)' : terminal ? 'var(--info-bg)' : 'var(--surface-3)',
                border: `1px solid ${ineffective ? 'var(--warn)' : terminal ? 'var(--info)' : 'var(--border)'}`,
                display: 'flex', alignItems: 'center', paddingLeft: 8,
              }}>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)', fontVariantNumeric: 'tabular-nums', fontWeight: 500 }}>{f.symbols}</span>
              </div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {delta != null && delta < 0 && (
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--err)', fontVariantNumeric: 'tabular-nums' }}>−{Math.abs(delta)}</span>
              )}
              {delta === 0 && !ineffective && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-mute)' }}>−0</span>}
              {ineffective && (
                <span className="chip warn" style={{ fontSize: 10 }} title={f.note}>⚠ 未生效 · 字段全空</span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Empty({ label, mini }) {
  return (
    <div style={{
      padding: mini ? 8 : 16, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)',
      fontSize: mini ? 10 : 11, display: 'grid', placeItems: 'center', minHeight: mini ? 60 : 120,
    }}>{label}</div>
  );
}
