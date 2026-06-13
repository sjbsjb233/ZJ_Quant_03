// rpFlame.jsx — 模块 F：函数级火焰图面板 (§3.8/§3.5)
// 工具栏（搜索 / 阶段 chips / 重置 / 选区指示）+ 主体（hover、点击下钻、面包屑）
// + 底部说明行。搜索、阶段、时间选区三种过滤可叠加（取交集）。
// Ported from the Claude Design handoff (rp-flame.jsx).
import React from 'react';
import { rpCol, STAGE_ORDER, rpThousand, rpMMSS, rpMidTrunc } from './rpFormat';
import { RPPanel, RPEmpty, RPSk, RPHover, RP_TIPS } from './rpShared';

function rpFlameEff(node, frac) {
  const f = node.stage ? frac(node.stage) : 1;
  return node.samples * f;
}
// fraction of each stage covered by the time selection
export function rpStageFrac(run, sel) {
  if (!sel) return () => 1;
  const m = {};
  run.stages.forEach((s) => {
    const ov = Math.max(0, Math.min(s.to, sel.t1) - Math.max(s.from, sel.t0));
    m[s.id] = (s.to - s.from) ? ov / (s.to - s.from) : 1;
  });
  return (id) => (m[id] != null ? m[id] : 1);
}

function rpFlattenFlame(root, frac) {
  const out = [];
  const rootEff = root.stage
    ? rpFlameEff(root, frac)
    : (root.children || []).reduce((s, c) => s + rpFlameEff(c, frac), 0) || 1;
  function walk(node, x0, x1, depth) {
    out.push({ node, x0, x1, depth });
    const eff = node.stage ? rpFlameEff(node, frac) : rootEff;
    if (!eff) return;
    let acc = x0;
    for (const c of node.children || []) {
      const cw = (x1 - x0) * rpFlameEff(c, frac) / eff;
      if (cw > 0.0004) walk(c, acc, acc + cw, depth + 1);
      acc += cw;
    }
  }
  walk(root, 0, 1, 0);
  return { frames: out, rootEff };
}

export function RPFlamePanel({ run, flame, sel, mode = 'data', width = 1380, initialDrill, initialQuery, initialChip, onRerun }) {
  const [query, setQuery] = React.useState(initialQuery || '');
  const [chip, setChip] = React.useState(initialChip || null);
  const [drill, setDrill] = React.useState(initialDrill || []);
  const [hover, setHover] = React.useState(null);
  const boxRef = React.useRef(null);

  const headerRight = (
    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)' }}>
      <RPHover tip={RP_TIPS.flame} w={300} align="right"><span style={{ cursor: 'help' }}>用法 ⓘ</span></RPHover>
    </span>
  );

  if (mode === 'off') {
    return (
      <RPPanel title="函数级火焰图" meta="CPU 耗时分布 · 按需剖析" right={headerRight}>
        <RPEmpty icon="▤" title="本次运行未开启性能剖析"
          desc="函数级耗时分布仅在「剖析模式」下采集。普通运行不产生此数据。"
          action={
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 2 }}>
              <button className="btn primary sm" onClick={onRerun} disabled={!onRerun}>▶ 以剖析模式重跑</button>
              <span style={{ fontSize: 10, color: 'var(--fg-4)', fontFamily: 'var(--font-mono)' }}>{RP_TIPS.profiling}</span>
            </div>
          } />
      </RPPanel>
    );
  }
  if (mode === 'loading') {
    return (
      <RPPanel title="函数级火焰图" meta="CPU 耗时分布 · 剖析模式" right={headerRight}>
        <div style={{ padding: '14px 14px 16px', display: 'flex', flexDirection: 'column', gap: 5 }}>
          <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
            <RPSk w={200} h={22} /><RPSk w={300} h={22} /><RPSk w={60} h={22} />
          </div>
          {[100, 78, 55, 38, 22, 12].map((w, i) => <RPSk key={i} w={w + '%'} h={15} r={2} />)}
          <div style={{ marginTop: 6, fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>
            <span className="dot running" style={{ width: 5, height: 5 }}></span> 加载剖析数据…（其余模块不受影响）
          </div>
        </div>
      </RPPanel>
    );
  }

  // ── data mode ──
  const frac = rpStageFrac(run, sel);
  let root = flame;
  const crumb = [{ label: '全部', chip: null, drill: [] }];
  if (chip) {
    const sc = (flame.children || []).find((c) => c.stage === chip);
    if (sc) { root = sc; crumb.push({ label: sc.name, chip, drill: [] }); }
  }
  drill.forEach((name, i) => {
    const next = (root.children || []).find((c) => c.name === name);
    if (next) { root = next; crumb.push({ label: name, chip, drill: drill.slice(0, i + 1) }); }
  });

  const { frames, rootEff } = rpFlattenFlame(root, frac);
  const q = query.trim().toLowerCase();
  const matches = q ? frames.filter((f) => f.node.name.toLowerCase().includes(q)) : null;
  const visSamples = Math.round(root.stage ? rpFlameEff(root, frac) : rootEff);
  const noSamples = visSamples < 1 || (q && matches.length === 0);
  const maxDepth = frames.reduce((m, f) => Math.max(m, f.depth), 0);
  const rowH = 18;
  const flameH = (maxDepth + 1) * rowH;

  function frameColor(node, depth) {
    if (!node.stage) return 'var(--surface-3)';
    return rpCol(node.stage, 0.22 + (depth % 2) * 0.13);
  }
  function pathTo(target) {
    const p = [];
    (function find(n, acc) {
      if (n === target.node) { p.push(...acc); return true; }
      for (const c of n.children || []) if (find(c, [...acc, c.name])) return true;
      return false;
    })(chip ? (flame.children || []).find((c) => c.stage === chip) : flame, []);
    return p;
  }

  return (
    <RPPanel title="函数级火焰图" meta="CPU 耗时分布 · 剖析模式" right={headerRight}>
      {/* toolbar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 14px', borderBottom: '1px solid var(--border-faint)', flexWrap: 'wrap' }}>
        <span style={{ position: 'relative', display: 'inline-flex', alignItems: 'center' }}>
          <span style={{ position: 'absolute', left: 8, color: 'var(--fg-4)', fontSize: 10 }}>⌕</span>
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索函数名…"
            style={{ width: 190, height: 24, padding: '0 8px 0 22px', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 4, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)' }} />
        </span>
        {q && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: matches.length ? 'var(--info)' : 'var(--err)' }}>命中 {matches.length} 帧</span>}
        <span style={{ width: 1, height: 16, background: 'var(--border)' }}></span>
        {STAGE_ORDER.map((id) => {
          const active = chip === id;
          return (
            <button key={id} onClick={() => { setChip(active ? null : id); setDrill([]); }} style={{
              height: 20, padding: '0 9px', borderRadius: 3, cursor: 'pointer',
              fontFamily: 'var(--font-mono)', fontSize: 10,
              border: `1px solid ${active ? rpCol(id) : 'var(--border)'}`,
              background: active ? rpCol(id, 0.12) : 'var(--surface)',
              color: active ? rpCol(id) : 'var(--fg-3)', fontWeight: active ? 500 : 400,
              display: 'inline-flex', alignItems: 'center', gap: 4,
            }}>
              <span style={{ width: 6, height: 6, borderRadius: 2, background: rpCol(id, 0.75) }}></span>{id}
            </button>
          );
        })}
        <button className="btn ghost sm" onClick={() => { setQuery(''); setChip(null); setDrill([]); }}
          style={{ color: 'var(--fg-3)' }}>⟲ 重置</button>
        <div style={{ flex: 1 }}></div>
        {sel && (
          <span className="chip info" style={{ height: 18, fontSize: 10 }}>
            ◧ 选区 {rpMMSS(sel.t0)}–{rpMMSS(sel.t1)} · 样本 {rpThousand(visSamples)}
          </span>
        )}
      </div>

      {/* breadcrumb */}
      {crumb.length > 1 && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '5px 14px', borderBottom: '1px solid var(--border-faint)', fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', flexWrap: 'wrap' }}>
          <span style={{ color: 'var(--fg-4)' }}>下钻路径</span>
          {crumb.map((c, i) => (
            <React.Fragment key={i}>
              {i > 0 && <span style={{ color: 'var(--fg-mute)' }}>›</span>}
              <span onClick={() => { setChip(c.chip); setDrill(c.drill); }}
                style={{ cursor: i < crumb.length - 1 ? 'pointer' : 'default', color: i === crumb.length - 1 ? 'var(--fg)' : 'var(--info)', fontWeight: i === crumb.length - 1 ? 500 : 400 }}>
                {rpMidTrunc(c.label, 34)}
              </span>
            </React.Fragment>
          ))}
        </div>
      )}

      {/* flame body */}
      {noSamples ? (
        <RPEmpty icon="∅" title="该筛选条件下无样本" pad={26}
          desc="搜索、阶段筛选与时间选区取交集后没有命中的采样。调整或重置筛选即可恢复。"
          action={<button className="btn sm" onClick={() => { setQuery(''); setChip(null); setDrill([]); }}>⟲ 重置筛选</button>} />
      ) : (
        <div ref={boxRef} className="scroll" style={{ position: 'relative', margin: '10px 14px 6px', maxHeight: 248, overflowY: 'auto' }}
          onMouseLeave={() => setHover(null)}>
          <div style={{ position: 'relative', height: flameH, minWidth: 200 }}>
            {frames.map((f, i) => {
              const innerW = width - 28;
              const left = f.x0 * innerW;
              const wpx = Math.max(3, (f.x1 - f.x0) * innerW - 1);
              const dim = q && !f.node.name.toLowerCase().includes(q);
              const pct = (f.x1 - f.x0) * 100;
              const chars = Math.floor(wpx / 6.1) - 1;
              return (
                <div key={i}
                  onClick={() => { if (f.depth > 0) setDrill(pathTo(f)); }}
                  onMouseEnter={() => setHover({ f, pct, x: left, y: f.depth * rowH })}
                  style={{
                    position: 'absolute', left, top: f.depth * rowH, width: wpx, height: rowH - 2,
                    background: frameColor(f.node, f.depth),
                    border: `1px solid ${q && !dim ? 'var(--fg)' : 'var(--surface)'}`,
                    borderRadius: 2, cursor: f.depth > 0 ? 'pointer' : 'default',
                    opacity: dim ? 0.22 : 1,
                    overflow: 'hidden', whiteSpace: 'nowrap',
                    fontFamily: 'var(--font-mono)', fontSize: 9.5, lineHeight: `${rowH - 4}px`,
                    color: 'var(--fg-2)', paddingLeft: wpx > 26 ? 4 : 0,
                    boxSizing: 'border-box',
                  }}
                  title={`${f.node.name}\n${f.node.file}`}>
                  {wpx > 34 ? rpMidTrunc(f.node.name, Math.max(3, chars)) : ''}
                </div>
              );
            })}
          </div>
          {hover && (
            <div style={{
              position: 'absolute', left: Math.min(hover.x + 10, width - 320), top: hover.y + rowH + 4, zIndex: 60, pointerEvents: 'none',
              width: 290, background: 'var(--surface)', border: '1px solid var(--border-strong)', borderRadius: 5,
              boxShadow: '0 8px 24px rgba(20,18,14,0.16)', padding: '7px 10px',
              fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-2)', lineHeight: 1.65,
            }}>
              <div style={{ color: 'var(--fg)', fontWeight: 500, marginBottom: 2, wordBreak: 'break-all' }}>{hover.f.node.name}</div>
              <div style={{ color: 'var(--fg-3)', wordBreak: 'break-all' }}>{hover.f.node.file}</div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 3 }}>
                <span style={{ color: 'var(--fg-3)' }}>样本 {rpThousand(rpFlameEff(hover.f.node, frac))}</span>
                <span style={{ color: 'var(--fg)' }}>占当前视图 {hover.pct >= 99.95 ? '100' : hover.pct.toFixed(1)}%</span>
              </div>
              {hover.f.depth > 0 && <div style={{ color: 'var(--fg-4)', marginTop: 2 }}>点击下钻 · 面包屑返回</div>}
            </div>
          )}
        </div>
      )}

      {/* footer note — 实测采样率 (§3.5) */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '5px 14px 8px', fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-4)' }}>
        <span>采样 {run.profiling?.hz ?? '—'} Hz（实测）</span>
        <span>总样本 {rpThousand(run.profiling?.samples || 0)}</span>
        <RPHover tip={RP_TIPS.profiling} w={290} up><span style={{ cursor: 'help' }}>剖析模式有约 3–5% 开销 ⓘ</span></RPHover>
        <div style={{ flex: 1 }}></div>
        <span>根帧在上 · 宽度 = 耗时占比 · 深度按 40 层设计（超出纵向滚动）</span>
      </div>
    </RPPanel>
  );
}
