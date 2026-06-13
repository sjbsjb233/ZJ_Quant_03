// rpBlocks.jsx — 页面一「资源画像」区块：标题行(A) / 指标卡(B) / 阶段拆解(D)
// / 基线对比(E-1) / 历史趋势(E-2) / 并发上下文(E-3).
// Ported from the Claude Design handoff (rp-blocks.jsx).
import React from 'react';
import { rpCol, rpMB, rpMBd, rpThousand, rpMidTrunc } from './rpFormat';
import { RPPanel, RPAttrBadge, RPPerfBadge, RPInfoDot, RP_TIPS, RPHover, RPEmpty } from './rpShared';

// ── A · 标题行 ───────────────────────────────────────────────────────────────
export function RPTitleRow({ level = 'exact', n = 3, degradedTag, run }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, minHeight: 26 }}>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 500, color: 'var(--fg)' }}>资源画像</span>
      <RPAttrBadge level={level} n={n} />
      {degradedTag && (
        <span className="chip warn" style={{ height: 17, fontSize: 10 }}>⚠ 时间线缺失（采集器降级）</span>
      )}
      <div style={{ flex: 1 }}></div>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)', display: 'inline-flex', gap: 8, alignItems: 'center' }}>
        <span style={{ color: 'var(--fg-2)' }}>{run.id}</span>
        <span style={{ color: 'var(--fg-mute)' }}>·</span>
        <span title={run.factor} style={{ cursor: 'default' }}>{rpMidTrunc(run.factor, 30)}</span>
        <span style={{ color: 'var(--fg-mute)' }}>·</span>
        <span>{run.pool} · {run.win}</span>
      </span>
    </div>
  );
}

// ── B · 指标卡 ×4 ────────────────────────────────────────────────────────────
export function RPCards({ run, perfState = 'normal', perfN = 3, narrow }) {
  const c = run.cards;
  const cards = [
    {
      l: '峰值内存', v: rpMB(c.peak), badge: <RPPerfBadge state={perfState} n={perfN} sm />,
      hero: true,
    },
    { l: 'CPU 时间（精确）', v: `${(c.cpuS || 0).toFixed(1)} s`, sub: <span>归一化 {c.cpuNorm} <RPInfoDot tip={RP_TIPS.norm} /></span> },
    { l: '平均 CPU', v: `${c.avgCpu}%`, sub: `线程配额 threads ${c.threads}` },
    {
      l: <span>归一化内存 <RPInfoDot tip={RP_TIPS.norm} /></span>, v: `${rpThousand(c.memNorm)} MB/百万行`,
      sub: <span>基线 p50 {c.p50n != null ? rpThousand(c.p50n) : '—'} · p90 {c.p90n != null ? rpThousand(c.p90n) : '—'} <RPInfoDot tip={RP_TIPS.baseline} /></span>,
    },
  ];
  return (
    <div style={{ display: 'grid', gridTemplateColumns: narrow ? '1fr 1fr' : 'repeat(4, 1fr)', gap: 1, background: 'var(--border)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'visible' }}>
      {cards.map((cd, i) => (
        <div key={i} style={{ background: 'var(--surface)', padding: '9px 14px 10px', borderRadius: i === 0 ? '6px 0 0 6px' : i === 3 ? '0 6px 6px 0' : 0, position: 'relative' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{cd.l}</span>
            <div style={{ flex: 1 }}></div>
            {cd.badge}
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: cd.hero ? 22 : 19, fontWeight: 500, color: 'var(--fg)', fontVariantNumeric: 'tabular-nums', lineHeight: 1.2, marginTop: 4 }}>{cd.v}</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', marginTop: 3, minHeight: 14 }}>{cd.sub || ''}</div>
        </div>
      ))}
    </div>
  );
}

// ── D · 阶段拆解 ─────────────────────────────────────────────────────────────
export function RPStageBreakdown({ run, sel }) {
  const rows = run.stageRows;
  if (!rows || !rows.length) return null;
  const cpuMax = Math.max(...rows.map((r) => r.cpu), 0.01);
  const posMax = Math.max(...rows.map((r) => Math.max(0, r.mem)), 0);
  const negMax = Math.max(...rows.map((r) => Math.max(0, -r.mem)), 0);
  const span = posMax + negMax || 1;
  const zeroPct = negMax / span * 100;
  const covered = sel ? run.stages.filter((s) => s.to > sel.t0 && s.from < sel.t1).map((s) => s.id) : null;

  return (
    <RPPanel title="阶段拆解" meta="贵在哪一段 · 条长按本运行内最大值归一"
      right={sel && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--info)' }}>◧ 选区联动 · 高亮覆盖阶段</span>}>
      <div style={{ padding: '8px 14px 12px' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '92px 1.1fr 1.1fr', gap: '0 18px', padding: '0 0 5px', fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--fg-4)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          <span>阶段</span><span>CPU 用量</span><span>内存增量（负值 = 写盘释放）</span>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          {rows.map((r) => {
            const hl = covered && covered.includes(r.id);
            const dim = covered && !hl;
            const neg = r.mem < 0;
            const memW = Math.abs(r.mem) / span * 100;
            return (
              <div key={r.id} style={{
                display: 'grid', gridTemplateColumns: '92px 1.1fr 1.1fr', gap: '0 18px', alignItems: 'center',
                padding: '4px 6px', margin: '0 -6px', borderRadius: 4,
                background: hl ? 'var(--info-bg)' : 'transparent',
                boxShadow: hl ? 'inset 2px 0 0 var(--info)' : 'none',
                opacity: dim ? 0.45 : 1, transition: 'opacity .15s, background .15s',
              }}>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)', display: 'inline-flex', alignItems: 'center', gap: 7 }}>
                  <span style={{ width: 8, height: 8, borderRadius: 2, background: rpCol(r.id, 0.75) }}></span>{r.id}
                </span>
                {/* CPU bar */}
                <span style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
                  <span style={{ flex: 1, height: 9, background: 'var(--surface-3)', borderRadius: 2, overflow: 'hidden' }}>
                    <span style={{ display: 'block', width: `${Math.max(1.5, r.cpu / cpuMax * 100)}%`, height: '100%', background: rpCol(r.id, 0.6) }}></span>
                  </span>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg)', fontVariantNumeric: 'tabular-nums', width: 52, textAlign: 'right' }}>{r.cpu.toFixed(1)}s</span>
                </span>
                {/* mem diverging bar */}
                <span style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
                  <span style={{ flex: 1, position: 'relative', height: 9, background: 'var(--surface-3)', borderRadius: 2 }}>
                    <span style={{ position: 'absolute', left: `${zeroPct}%`, top: -2, bottom: -2, width: 1, background: 'var(--border-strong)' }}></span>
                    {neg ? (
                      <span style={{
                        position: 'absolute', right: `${100 - zeroPct}%`, top: 0, bottom: 0, width: `${Math.max(1.5, memW)}%`,
                        background: 'var(--ok-bg)', border: '1px dashed var(--ok)', borderRadius: 2, boxSizing: 'border-box',
                      }}></span>
                    ) : (
                      <span style={{
                        position: 'absolute', left: `${zeroPct}%`, top: 0, bottom: 0, width: `${Math.max(1.5, memW)}%`,
                        background: 'var(--fg-3)', opacity: 0.65, borderRadius: 2,
                      }}></span>
                    )}
                  </span>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, fontVariantNumeric: 'tabular-nums', width: 96, textAlign: 'right', color: neg ? 'var(--ok)' : 'var(--fg)' }}>
                    {rpMBd(r.mem)}{neg && <span style={{ color: 'var(--fg-4)', fontSize: 9 }}> 释放</span>}
                  </span>
                </span>
              </div>
            );
          })}
        </div>
      </div>
    </RPPanel>
  );
}

// ── E-1 · 基线对比 ───────────────────────────────────────────────────────────
export function RPBaselineCompare({ run, building, buildingN = 3 }) {
  const data = (run.baselineCompare || []).filter((d) => d.v != null);
  const max = Math.max(...data.map((d) => d.v), 1);
  return (
    <RPPanel title="基线对比" meta={<span>归一化 MB/百万行 <RPInfoDot tip={RP_TIPS.norm} /></span>}>
      {building || data.length <= 1 ? (
        <RPEmpty icon="◌" pad={24} title={`基线建立中（${buildingN}/5）`}
          desc="历史同签名运行不足 5 次，p50 / p90 基线尚未建立；积累到 5 次后自动启用对比。" />
      ) : (
        <div style={{ padding: '10px 14px 12px', display: 'flex', flexDirection: 'column', gap: 6 }}>
          {data.map((d) => (
            <div key={d.l} style={{ display: 'grid', gridTemplateColumns: '86px 1fr 74px', gap: 10, alignItems: 'center' }}>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: d.self ? 'var(--fg)' : 'var(--fg-3)', fontWeight: d.self ? 600 : 400, textAlign: 'right' }}>
                {d.self ? '▸ 本次' : <RPHover tip={RP_TIPS.baseline} w={260}><span style={{ cursor: 'help' }}>{d.l}</span></RPHover>}
              </span>
              <span style={{ height: d.self ? 13 : 9, background: 'var(--surface-3)', borderRadius: 2, overflow: 'hidden' }}>
                <span style={{ display: 'block', width: `${d.v / max * 100}%`, height: '100%', background: d.self ? 'var(--fg)' : 'var(--fg-4)', opacity: d.self ? 1 : 0.55 }}></span>
              </span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: d.self ? 12 : 10.5, color: d.self ? 'var(--fg)' : 'var(--fg-3)', fontWeight: d.self ? 600 : 400, fontVariantNumeric: 'tabular-nums', textAlign: 'right' }}>{rpThousand(d.v)}</span>
            </div>
          ))}
        </div>
      )}
    </RPPanel>
  );
}

// ── E-2 · 历史趋势迷你图 ─────────────────────────────────────────────────────
export function RPHistoryTrend({ run, w = 560, onPick }) {
  const data = run.history || [];
  const H = 132, pad = { t: 16, r: 18, b: 30, l: 44 };
  const iw = w - pad.l - pad.r, ih = H - pad.t - pad.b;
  if (data.length < 2) {
    return (
      <RPPanel title="历史趋势" meta="最近 10 次运行 · 峰值内存">
        <RPEmpty icon="◌" pad={22} title="历史趋势建立中" desc="同因子运行积累更多后，这里会出现最近 10 次的峰值内存趋势。" />
      </RPPanel>
    );
  }
  const vMin = Math.min(...data.map((d) => d.v)) * 0.92, vMax = Math.max(...data.map((d) => d.v)) * 1.05;
  const x = (i) => pad.l + iw * i / (data.length - 1);
  const y = (v) => pad.t + ih - (v - vMin) / ((vMax - vMin) || 1) * ih;
  return (
    <RPPanel title="历史趋势" meta="最近 10 次运行 · 峰值内存 · 点击任一点跳该 run 详情">
      <div style={{ padding: '6px 12px 4px' }}>
        <svg width={w} height={H} style={{ display: 'block', maxWidth: '100%' }}>
          {[vMin + (vMax - vMin) * 0.2, vMin + (vMax - vMin) * 0.8].map((v, i) => (
            <g key={i}>
              <line x1={pad.l} y1={y(v)} x2={pad.l + iw} y2={y(v)} stroke="var(--border-faint)" />
              <text x={pad.l - 5} y={y(v) + 3} textAnchor="end" style={{ fontFamily: 'var(--font-mono)', fontSize: 8.5, fill: 'var(--fg-4)' }}>{rpThousand(v)}</text>
            </g>
          ))}
          <path d={data.map((d, i) => `${i ? 'L' : 'M'}${x(i)},${y(d.v)}`).join('')} fill="none" stroke="var(--fg-4)" strokeWidth="1" opacity="0.6" />
          {data.map((d, i) => (
            <g key={d.id + i} style={{ cursor: 'pointer' }} onClick={() => !d.current && onPick && d.run_id && onPick(d.run_id)}>
              {d.current && <circle cx={x(i)} cy={y(d.v)} r="7" fill="none" stroke="var(--fg)" strokeWidth="1" opacity="0.5" />}
              <circle cx={x(i)} cy={y(d.v)} r="3.2" fill={d.current ? 'var(--fg)' : 'var(--surface)'} stroke={d.current ? 'var(--fg)' : 'var(--info)'} strokeWidth="1.3" />
              <text x={x(i)} y={H - 13} textAnchor="middle" style={{ fontFamily: 'var(--font-mono)', fontSize: 8, fill: d.current ? 'var(--fg)' : 'var(--fg-4)', fontWeight: d.current ? 600 : 400 }}>{d.id.replace('r_', '')}</text>
              <title>{`${d.id} · 峰值 ${rpMB(d.v)}${d.current ? '（当前 run）' : ' · 点击跳转该 run 详情'}`}</title>
            </g>
          ))}
          <text x={pad.l + iw} y={pad.t - 5} textAnchor="end" style={{ fontFamily: 'var(--font-mono)', fontSize: 8.5, fill: 'var(--fg-4)' }}>当前 = 实心点</text>
        </svg>
      </div>
    </RPPanel>
  );
}

// ── E-3 · 并发上下文（折叠面板，仅 shared） ──────────────────────────────────
export function RPConcurrency({ run, defaultOpen = false, n = 3 }) {
  const [open, setOpen] = React.useState(defaultOpen);
  const tasks = run.concTasks || [];
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
      <button onClick={() => setOpen((v) => !v)} style={{
        width: '100%', display: 'flex', alignItems: 'center', gap: 8, padding: '7px 14px',
        background: 'var(--surface-2)', border: 'none', cursor: 'pointer', textAlign: 'left',
      }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>{open ? '▾' : '▸'}</span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg)', fontWeight: 500 }}>同期并发任务（{tasks.length}）</span>
        <span style={{ fontSize: 10, color: 'var(--fg-4)' }}>解释为什么内存数字无法精确归因到本运行</span>
        <div style={{ flex: 1 }}></div>
        <RPAttrBadge level="shared" n={n} sm noTip />
      </button>
      {open && (
        tasks.length === 0 ? (
          <div style={{ padding: '10px 14px', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)' }}>同期任务明细暂不可用。</div>
        ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, borderTop: '1px solid var(--border)' }}>
          <thead>
            <tr style={{ background: 'var(--surface)', borderBottom: '1px solid var(--border-faint)' }}>
              {['任务名', '类型', '与本运行的重叠时段'].map((h) => (
                <th key={h} style={{ textAlign: 'left', padding: '5px 14px', fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.04em' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {tasks.map((t, i) => (
              <tr key={i} className="iv-row" style={{ borderBottom: i < tasks.length - 1 ? '1px solid var(--border-faint)' : 'none', height: 28 }}>
                <td style={{ padding: '0 14px', fontFamily: 'var(--font-mono)', color: 'var(--fg)' }}>{t.name}</td>
                <td style={{ padding: '0 14px', color: 'var(--fg-2)' }}>{t.type}</td>
                <td style={{ padding: '0 14px', fontFamily: 'var(--font-mono)', color: 'var(--fg-3)', fontVariantNumeric: 'tabular-nums' }}>{t.overlap}</td>
              </tr>
            ))}
          </tbody>
        </table>
        )
      )}
    </div>
  );
}
