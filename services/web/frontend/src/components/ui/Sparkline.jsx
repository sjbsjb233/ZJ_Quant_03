import React from 'react';

export function Sparkline({ data, width = 60, height = 18, color, mode = 'line', fill = false }) {
  if (!data || data.length === 0) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const step = data.length > 1 ? width / (data.length - 1) : 0;
  const points = data.map((v, i) => [i * step, height - ((v - min) / range) * (height - 2) - 1]);

  if (mode === 'bars') {
    const bw = Math.max(1, width / data.length - 1);
    return (
      <svg className="spark" width={width} height={height} style={{ color: color || 'var(--fg-3)' }}>
        {data.map((v, i) => {
          const h = Math.max(1, ((v - min) / range) * (height - 1));
          return <rect key={i} x={i * (bw + 1)} y={height - h} width={bw} height={h} fill="currentColor" />;
        })}
      </svg>
    );
  }
  const d = points.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`).join(' ');
  const [lx, ly] = points[points.length - 1];
  return (
    <svg className="spark" width={width} height={height} style={{ color: color || 'var(--fg-3)', overflow: 'visible' }}>
      {fill && <path d={`${d} L${width},${height} L0,${height} Z`} fill="currentColor" opacity="0.1" stroke="none" />}
      <path d={d} />
      <circle cx={lx} cy={ly} r="1.6" fill="currentColor" />
    </svg>
  );
}
