// /factors/screening — 因子优选工作台 (因子层重构 §11.2).
// 铁律: 严格单篮子单快照, 无任何跨篮对比入口.
//   (a) Picker home: 选一个篮子 + 一份快照 → 进入工作台.
//   (b) Workbench 三步: ① 筛选与评分 → ② 低相关剪枝 → ③ 保存集合.
// §11.4: 拖动权重滑杆时, 用已下载的 score_detail.z 客户端重算 score 并即时重排;
//        只有 filters/collapse/include_degraded 变化时才回后端 refetch.
import React from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery, useMutation } from '@tanstack/react-query';
import { endpoints } from '../../../lib/api';
import { TerminalShell } from '../../../components/shell';
import { ErrorState, Loading } from '../../../components/ui';
import {
  Badge, CalibrChips, LightHint, ScoreBar, Hover, Segmented, Toggle,
  SectionHead, EmptyState, fmtNum, fmtPct,
} from '../baskets/kit';

// ─── Weight keys (the 6 §11.2 metrics; turnover + mdd are "lower is better") ──
const WEIGHT_KEYS = [
  { k: 'icir_annual', l: '年化ICIR' },
  { k: 'rank_ic_mean', l: '|RankIC|' },
  { k: 'ic_win_rate', l: 'IC胜率' },
  { k: 'ls_sharpe', l: '多空夏普' },
  { k: 'ls_max_drawdown', l: '最大回撤' },
  { k: 'turnover', l: '换手率' },
];
const DEFAULT_WEIGHTS = {
  icir_annual: 0.30, rank_ic_mean: 0.25, ic_win_rate: 0.15,
  ls_sharpe: 0.15, ls_max_drawdown: 0.10, turnover: 0.05,
};
const DEFAULT_PRESET = 'preset:spre_builtin_ic_quality';

// Client-side score from a row's score_detail z-values: score = Σ z·w / Σ|w|.
function clientScore(row, weights) {
  const detail = row.score_detail || {};
  let num = 0; let den = 0;
  WEIGHT_KEYS.forEach(({ k }) => {
    const w = weights[k];
    if (w == null) return;
    const d = detail[k];
    const z = d ? Number(d.z) : 0;
    num += (Number.isFinite(z) ? z : 0) * w;
    den += Math.abs(w);
  });
  return den > 0 ? num / den : 0;
}

// Build a score_detail keyed by current weights for the hover bar.
function detailWithWeights(row, weights) {
  const detail = row.score_detail || {};
  const out = {};
  WEIGHT_KEYS.forEach(({ k, l }) => {
    const d = detail[k];
    if (!d) return;
    const w = weights[k] ?? d.w ?? 0;
    out[l] = { x: d.x, z: d.z, w, contrib: (Number(d.z) || 0) * w };
  });
  return out;
}

// Greedy correlation prune preview over the grid's correlation matrix.
// order: [item_key] in priority order. forced: [item_key] kept regardless.
function previewPrune(candidates, order, matrix, threshold, maxN, forced) {
  const forcedSet = new Set(forced || []);
  const candSet = new Set(candidates);
  const kept = []; const keptKeys = []; const pruned = [];
  // seed forced first
  (order || candidates).forEach((key) => {
    if (forcedSet.has(key) && candSet.has(key) && !keptKeys.includes(key)) {
      kept.push({ item_key: key, decision: 'kept_manual' });
      keptKeys.push(key);
    }
  });
  (order || candidates).forEach((key) => {
    if (!candSet.has(key) || keptKeys.includes(key)) return;
    let blocked = null; let blockRho = 0;
    for (const kk of keptKeys) {
      const rho = matrix?.[key]?.[kk];
      if (rho != null && Math.abs(rho) >= threshold) { blocked = kk; blockRho = rho; break; }
    }
    if (blocked) {
      pruned.push({ item_key: key, decision: 'pruned_corr', blocked_by: blocked, rho: blockRho });
    } else if (maxN != null && kept.length >= maxN) {
      pruned.push({ item_key: key, decision: 'pruned_capped' });
    } else {
      kept.push({ item_key: key, decision: 'kept' });
      keptKeys.push(key);
    }
  });
  return { kept, pruned };
}

// ════════════════════════════════════════════════════════════════════════════
export default function Screening() {
  const [params, setParams] = useSearchParams();
  const basketId = params.get('basket') || '';
  const sid = params.get('snapshot') || '';

  const enterWorkbench = (bId, sId) => {
    setParams({ basket: bId, snapshot: sId });
  };
  const exitWorkbench = () => setParams({});

  if (basketId && sid) {
    return <Workbench basketId={basketId} sid={sid} onExit={exitWorkbench} onSwitchSnapshot={(sId) => enterWorkbench(basketId, sId)} />;
  }
  return <PickerHome onPick={enterWorkbench} />;
}

// ─── (a) Picker home ─────────────────────────────────────────────────────────
function PickerHome({ onPick }) {
  const navigate = useNavigate();
  const q = useQuery({ queryKey: ['screening.baskets'], queryFn: () => endpoints.screeningBaskets() });
  const baskets = (q.data?.items || []).filter((b) => (b.snapshots || []).length > 0);

  return (
    <TerminalShell
      module="factor"
      current="screener"
      path={['factor_mgmt/', 'screening']}
      title="因子优选"
      subtitle="严格单篮子单快照 · 不跨篮对比"
      subToolbarRight={<span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>⚠ 单篮单快照 · 无跨篮对比</span>}
    >
      {q.isLoading ? (
        <Loading />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={q.refetch} />
      ) : (
        <>
          <div style={{ padding: '8px 18px 0', flexShrink: 0 }}>
            <LightHint tone="info">
              一次优选会话只针对<b>单一篮子的单一快照</b>。不同篮子参数口径可能不同，跨篮对比没有统计意义，故界面上不存在任何跨篮入口。
            </LightHint>
          </div>
          {baskets.length === 0 ? (
            <EmptyState
              icon="◇"
              title="没有任何已完成运行的篮子"
              desc="优选需要一份已产出的快照。先去篮子页创建并运行一个篮子。"
              action={<button className="btn primary" onClick={() => navigate('/factors/baskets')}>去因子篮子</button>}
            />
          ) : (
            <div className="scroll" style={{ flex: 1, minHeight: 0, padding: 18, display: 'flex', flexWrap: 'wrap', gap: 16, alignContent: 'flex-start', background: 'var(--bg)' }}>
              {baskets.map((b) => <PickCard key={b.basket_id} b={b} onPick={onPick} />)}
            </div>
          )}
        </>
      )}
    </TerminalShell>
  );
}

function PickCard({ b, onPick }) {
  const snaps = b.snapshots || [];
  const [snap, setSnap] = React.useState(b.latest_snapshot_id || snaps[0]?.snapshot_id);
  const latest = snaps.find((s) => s.snapshot_id === (b.latest_snapshot_id || snaps[0]?.snapshot_id));

  return (
    <div style={{ flex: '1 1 380px', maxWidth: 520, alignSelf: 'flex-start', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden', position: 'relative' }}>
      {b.stale && (
        <div style={{ position: 'absolute', top: 8, right: 8 }}>
          <Badge group="stale" value="待重算" sm />
        </div>
      )}
      <div style={{ padding: '12px 14px' }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--fg)', marginBottom: 8, paddingRight: b.stale ? 70 : 0 }}>{b.name}</div>
        <CalibrChips calibration={b.calibration} />
        <div style={{ display: 'flex', gap: 16, marginTop: 12, fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)', flexWrap: 'wrap' }}>
          <span>条目 <b style={{ color: 'var(--fg)' }}>{b.entry_count ?? 0}</b></span>
          <span>快照 <b style={{ color: 'var(--fg)' }}>{snaps.length}</b></span>
          <span>最新 as_of <b style={{ color: 'var(--fg)' }}>{latest?.asOf || '—'}</b></span>
        </div>
      </div>
      <div style={{ padding: '10px 14px', borderTop: '1px solid var(--border-faint)', display: 'flex', alignItems: 'center', gap: 8 }}>
        <select
          value={snap || ''}
          onChange={(e) => setSnap(e.target.value)}
          style={{ height: 24, padding: '0 8px', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)', maxWidth: 260 }}
        >
          {snaps.map((s) => (
            <option key={s.snapshot_id} value={s.snapshot_id}>
              as_of {s.asOf} · rev {s.rev}{s.snapshot_id === (b.latest_snapshot_id || snaps[0]?.snapshot_id) ? ' · 最新' : ''}
            </option>
          ))}
        </select>
        <div style={{ flex: 1 }} />
        <button className="btn sm primary" disabled={!snap} onClick={() => onPick(b.basket_id, snap)}>开始优选 →</button>
      </div>
    </div>
  );
}

// ─── (b) Workbench ───────────────────────────────────────────────────────────
const STEPS = [{ id: 1, l: '筛选与评分' }, { id: 2, l: '低相关剪枝' }, { id: 3, l: '保存集合' }];

function Workbench({ basketId, sid, onExit, onSwitchSnapshot }) {
  const navigate = useNavigate();
  const [step, setStep] = React.useState(1);

  // ── filter / weight / collapse / include_degraded state ──
  const [weightsObj, setWeightsObj] = React.useState(DEFAULT_WEIGHTS);
  const [usePreset, setUsePreset] = React.useState(DEFAULT_PRESET); // 'preset:ID' or null (custom)
  const [collapse, setCollapse] = React.useState('best');
  const [includeDegraded, setIncludeDegraded] = React.useState('partial');
  const [filterState, setFilterState] = React.useState({
    icirMin: '', winMin: '', monotonic: false, category: '',
  });

  // ── step ② state (lifted so it persists) ──
  const [orderMode, setOrderMode] = React.useState('score');
  const [threshold, setThreshold] = React.useState(0.8);
  const [maxN, setMaxN] = React.useState('');
  const [forced, setForced] = React.useState([]);
  const [manualOrder, setManualOrder] = React.useState(null); // [item_key] for orderMode=manual

  // presets (for the weight templates segmented)
  const presetsQ = useQuery({ queryKey: ['screen.presets'], queryFn: () => endpoints.screenPresets() });
  const builtins = (presetsQ.data?.items || []).filter((p) => p.builtin);

  // Build the filters DSL JSON string. Only changes that affect server-side
  // selection (filters/collapse/include_degraded) trigger a refetch.
  const filtersJson = React.useMemo(() => {
    const dsl = { metric: [], flags: {}, factor: {}, params: [] };
    if (filterState.icirMin !== '') dsl.metric.push({ k: 'icir_annual', op: 'gte', v: Number(filterState.icirMin) });
    if (filterState.winMin !== '') dsl.metric.push({ k: 'ic_win_rate', op: 'gte', v: Number(filterState.winMin) });
    if (filterState.monotonic) dsl.flags.monotonic = true;
    if (filterState.category) dsl.factor.category = filterState.category;
    return JSON.stringify(dsl);
  }, [filterState]);

  // weights param: a preset string OR a JSON string of the weight object.
  const weightsParam = usePreset || JSON.stringify(weightsObj);

  // NOTE: weightsParam is intentionally NOT in the query key — slider drags must
  // not refetch (§11.4). Server score is recomputed client-side from score_detail.
  const gridQ = useQuery({
    queryKey: ['screening.grid', basketId, sid, filtersJson, collapse, includeDegraded],
    queryFn: () => endpoints.screeningGrid(basketId, sid, {
      filters: filtersJson,
      weights: weightsParam,
      collapse,
      include_degraded: includeDegraded,
      sort: 'score_desc',
      page: 1,
      page_size: 200,
    }),
    keepPreviousData: true,
  });

  const grid = gridQ.data;
  const rawRows = grid?.rows || [];
  const correlation = grid?.correlation || {};
  const calibration = grid?.calibration || {};
  const stale = grid?.stale;

  // sync threshold default once grid arrives
  const thresholdInitRef = React.useRef(false);
  React.useEffect(() => {
    if (!thresholdInitRef.current && correlation.threshold != null) {
      setThreshold(correlation.threshold);
      thresholdInitRef.current = true;
    }
  }, [correlation.threshold]);

  // Client-side scored + sorted rows (recomputed instantly on weight change).
  const scoredRows = React.useMemo(() => {
    const rows = rawRows.map((r) => ({ ...r, _score: clientScore(r, weightsObj) }));
    rows.sort((a, b) => b._score - a._score);
    return rows.map((r, i) => ({ ...r, _rank: i + 1 }));
  }, [rawRows, weightsObj]);

  const setOneWeight = (k, val) => {
    setUsePreset(null);
    setWeightsObj((w) => ({ ...w, [k]: Math.max(0, Math.min(1, val)) }));
  };
  const applyPreset = (presetId) => {
    setUsePreset(`preset:${presetId}`);
    const p = builtins.find((x) => (x.preset_id || x.id) === presetId);
    if (p && p.weights && typeof p.weights === 'object') setWeightsObj({ ...DEFAULT_WEIGHTS, ...p.weights });
  };

  const excluded = grid?.excluded || {};

  return (
    <TerminalShell
      module="factor"
      current="screener"
      path={['factor_mgmt/', 'screening', calibration.name || basketId]}
      titleBar={false}
    >
      {/* context bar */}
      <div style={{ padding: '10px 18px', background: 'var(--surface)', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0, flexWrap: 'wrap' }}>
        <span onClick={onExit} style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-3)', cursor: 'pointer' }}>← 选篮子</span>
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--fg)' }}>{calibration.name || '优选会话'}</span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-2)' }}>快照 {sid}</span>
        <span style={{ color: 'var(--fg-mute)' }}>·</span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-2)' }}>当前候选 <b style={{ color: 'var(--fg)' }}>{scoredRows.length}</b></span>
        <div style={{ flex: 1 }} />
        <CalibrChips calibration={calibration} compact />
      </div>

      {stale && (
        <div style={{ padding: '8px 18px', background: 'var(--bg)' }}>
          <LightHint tone="info">建议基于最新结果优选 —— 当前快照已非最新。</LightHint>
        </div>
      )}

      {/* step bar */}
      <div style={{ padding: '10px 24px', background: 'var(--surface)', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', flexShrink: 0 }}>
        {STEPS.map((s, i) => {
          const done = s.id < step; const cur = s.id === step;
          return (
            <React.Fragment key={s.id}>
              <button onClick={() => s.id <= step && setStep(s.id)} style={{ display: 'flex', alignItems: 'center', gap: 8, background: 'none', border: 'none', cursor: s.id <= step ? 'pointer' : 'default', padding: 0 }}>
                <span style={{ width: 20, height: 20, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--font-mono)', fontSize: 10.5, fontWeight: 600, background: cur ? 'var(--fg)' : done ? 'var(--ok)' : 'var(--surface-3)', color: cur || done ? 'var(--bg)' : 'var(--fg-3)' }}>{done ? '✓' : s.id}</span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: cur ? 'var(--fg)' : 'var(--fg-3)', fontWeight: cur ? 500 : 400 }}>{s.l}</span>
              </button>
              {i < STEPS.length - 1 && <div style={{ width: 60, height: 1, background: done ? 'var(--ok)' : 'var(--border)', margin: '0 12px' }} />}
            </React.Fragment>
          );
        })}
      </div>

      <div style={{ flex: 1, minHeight: 0, display: 'flex', flexDirection: 'column' }}>
        {gridQ.isLoading ? (
          <Loading />
        ) : gridQ.isError ? (
          <ErrorState error={gridQ.error} onRetry={gridQ.refetch} />
        ) : (
          <>
            {step === 1 && (
              <Step1
                rows={scoredRows}
                weights={weightsObj}
                setOneWeight={setOneWeight}
                applyPreset={applyPreset}
                usePreset={usePreset}
                builtins={builtins}
                collapse={collapse}
                setCollapse={setCollapse}
                includeDegraded={includeDegraded}
                setIncludeDegraded={setIncludeDegraded}
                filterState={filterState}
                setFilterState={setFilterState}
                excluded={excluded}
                fetching={gridQ.isFetching}
                onNext={() => setStep(2)}
              />
            )}
            {step === 2 && (
              <Step2
                rows={scoredRows}
                correlation={correlation}
                orderMode={orderMode}
                setOrderMode={setOrderMode}
                threshold={threshold}
                setThreshold={setThreshold}
                maxN={maxN}
                setMaxN={setMaxN}
                forced={forced}
                setForced={setForced}
                manualOrder={manualOrder}
                setManualOrder={setManualOrder}
                basketId={basketId}
                sid={sid}
                weightsParam={weightsParam}
                includeDegraded={includeDegraded}
                onBack={() => setStep(1)}
                onNext={() => setStep(3)}
              />
            )}
            {step === 3 && (
              <Step3
                rows={scoredRows}
                correlation={correlation}
                calibration={calibration}
                basketId={basketId}
                sid={sid}
                filtersJson={filtersJson}
                weightsParam={weightsParam}
                collapse={collapse}
                includeDegraded={includeDegraded}
                orderMode={orderMode}
                manualOrder={manualOrder}
                threshold={threshold}
                maxN={maxN}
                forced={forced}
                onBack={() => setStep(2)}
                onCreated={(selId) => navigate(`/factors/selections/${selId}`)}
              />
            )}
          </>
        )}
      </div>
    </TerminalShell>
  );
}

function PanelLabel({ children }) {
  return <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>{children}</div>;
}

// ─── Step ① 筛选与评分 ────────────────────────────────────────────────────────
function Step1({
  rows, weights, setOneWeight, applyPreset, usePreset, builtins,
  collapse, setCollapse, includeDegraded, setIncludeDegraded,
  filterState, setFilterState, excluded, fetching, onNext,
}) {
  const [panelOpen, setPanelOpen] = React.useState(true);
  const setF = (patch) => setFilterState((s) => ({ ...s, ...patch }));
  const excludedTotal = (excluded.failed || 0) + (excluded.severe || 0) + (excluded.metric_missing || 0);

  return (
    <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
      {/* Left filter panel */}
      {panelOpen ? (
        <div style={{ width: 280, borderRight: '1px solid var(--border)', background: 'var(--surface)', display: 'flex', flexDirection: 'column', minHeight: 0, flexShrink: 0 }}>
          <SectionHead title="筛选" right={<button className="btn ghost sm" style={{ width: 20, padding: 0 }} onClick={() => setPanelOpen(false)}>‹</button>} />
          <div className="scroll" style={{ flex: 1, minHeight: 0, padding: 12, display: 'flex', flexDirection: 'column', gap: 14 }}>
            <div>
              <PanelLabel>指标条件</PanelLabel>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 8 }}>
                <span style={{ fontSize: 11, color: 'var(--fg-2)', width: 64 }}>年化ICIR ≥</span>
                <input value={filterState.icirMin} onChange={(e) => setF({ icirMin: e.target.value })} placeholder="—"
                  style={{ width: 56, height: 24, padding: '0 8px', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)' }} />
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ fontSize: 11, color: 'var(--fg-2)', width: 64 }}>IC胜率 ≥</span>
                <input value={filterState.winMin} onChange={(e) => setF({ winMin: e.target.value })} placeholder="0.50"
                  style={{ width: 56, height: 24, padding: '0 8px', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)' }} />
              </div>
            </div>

            <div>
              <PanelLabel>因子筛选</PanelLabel>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ fontSize: 11, color: 'var(--fg-2)', width: 64 }}>类别</span>
                <input value={filterState.category} onChange={(e) => setF({ category: e.target.value })} placeholder="不限"
                  style={{ width: 120, height: 24, padding: '0 8px', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3, fontSize: 11, color: 'var(--fg)' }} />
              </div>
            </div>

            <div>
              <PanelLabel>快捷开关</PanelLabel>
              <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }} onClick={() => setF({ monotonic: !filterState.monotonic })}>
                <Toggle on={filterState.monotonic} onClick={() => setF({ monotonic: !filterState.monotonic })} />
                <span style={{ fontSize: 11, color: 'var(--fg-2)' }}>要求单调性</span>
              </label>
            </div>

            <div>
              <PanelLabel>折叠同因子变体</PanelLabel>
              <Segmented
                options={[{ v: 'best', l: '只看最优' }, { v: 'all', l: '全部变体' }]}
                value={collapse}
                onChange={setCollapse}
              />
            </div>

            <div>
              <PanelLabel>纳入降级</PanelLabel>
              <select
                value={includeDegraded}
                onChange={(e) => setIncludeDegraded(e.target.value)}
                style={{ width: '100%', height: 26, padding: '0 8px', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)' }}
              >
                <option value="none">不含降级</option>
                <option value="partial">含部分降级</option>
                <option value="all">全部纳入（含严重）</option>
              </select>
              {includeDegraded === 'all' && <div style={{ marginTop: 6, fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--warn)' }}>⚠ 表内将出现严重降级项</div>}
            </div>

            <div>
              <PanelLabel>权重模板</PanelLabel>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                {builtins.map((p) => {
                  const pid = p.preset_id || p.id;
                  const active = usePreset === `preset:${pid}`;
                  return (
                    <button key={pid} className="btn ghost sm" onClick={() => applyPreset(pid)}
                      style={{ justifyContent: 'flex-start', borderColor: active ? 'var(--fg)' : 'var(--border)', color: active ? 'var(--fg)' : 'var(--fg-2)' }}>
                      {active ? '● ' : '○ '}{p.name}
                    </button>
                  );
                })}
                {builtins.length === 0 && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-4)' }}>内置预设加载中…</span>}
              </div>
            </div>

            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-4)', lineHeight: 1.6, borderTop: '1px solid var(--border-faint)', paddingTop: 10 }}>失败条目永远不出现在候选中，不提供开关。</div>
          </div>
        </div>
      ) : (
        <div style={{ width: 32, borderRight: '1px solid var(--border)', background: 'var(--surface)', display: 'flex', justifyContent: 'center', paddingTop: 8, flexShrink: 0 }}>
          <button className="btn ghost sm" style={{ width: 22, padding: 0 }} onClick={() => setPanelOpen(true)}>›</button>
        </div>
      )}

      {/* Right main area */}
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        <WeightBar weights={weights} setOneWeight={setOneWeight} usePreset={usePreset} />
        {/* table toolbar */}
        <div style={{ padding: '6px 14px', background: 'var(--surface)', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0, fontFamily: 'var(--font-mono)', fontSize: 11 }}>
          <span style={{ color: 'var(--fg-3)' }}>已排除 {excludedTotal} 项（失败 {excluded.failed || 0} · 严重 {excluded.severe || 0} · 指标缺失 {excluded.metric_missing || 0}）</span>
          <div style={{ flex: 1 }} />
          {fetching && <span style={{ color: 'var(--info)' }}>● 重新筛选中…</span>}
          <span style={{ color: 'var(--fg-4)' }}>拖动权重滑杆即时重排「综合得分」</span>
        </div>
        {/* main table */}
        <div className="scroll" style={{ flex: 1, minHeight: 0 }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
            <thead>
              <tr style={{ position: 'sticky', top: 0, zIndex: 2, background: 'var(--surface-2)', borderBottom: '1px solid var(--border-strong)' }}>
                {['名次', '条目', '参数', '综合得分', '年化ICIR', '|RankIC|', 'IC胜率', '多空夏普', '最大回撤', '换手', '结算', '徽标'].map((h, i) => (
                  <th key={i} style={{ textAlign: i >= 3 && i <= 10 ? 'right' : 'left', padding: '7px 12px', fontSize: 9, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => {
                const m = r.metrics || {};
                const severe = r.degrade === 'severe';
                return (
                  <tr key={r.item_key} style={{ borderBottom: '1px solid var(--border-faint)', height: 34, background: severe ? 'var(--err-bg)' : i % 2 ? 'rgba(0,0,0,0.012)' : 'transparent' }}>
                    <td style={{ padding: '0 12px', fontFamily: 'var(--font-mono)', color: 'var(--fg-4)' }}>{r._rank}</td>
                    <td style={{ padding: '0 12px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                        <span style={{ fontWeight: 500, color: 'var(--fg)' }}>{r.label}</span>
                        {collapse === 'best' && r.variant_count > 1 && <span className="chip neutral" style={{ height: 14, fontSize: 8.5 }}>{r.variant_count} 个变体</span>}
                      </div>
                    </td>
                    <td style={{ padding: '0 12px', fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', maxWidth: 160, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {paramText(r.params)}
                    </td>
                    <td style={{ padding: '0 12px', textAlign: 'right' }}>
                      <Hover align="right" w={300} content={<ScoreBar detail={detailWithWeights(r, weights)} />}>
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'help' }}>
                          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 600, color: 'var(--fg)' }}>{fmtNum(r._score, 3)}</span>
                          <span style={{ display: 'inline-block', width: 40, height: 5, background: 'var(--surface-3)', borderRadius: 2, overflow: 'hidden', verticalAlign: 'middle' }}>
                            <span style={{ display: 'block', width: `${Math.max(2, Math.min(100, (r._score + 1) * 50))}%`, height: '100%', background: 'var(--fg)' }} />
                          </span>
                        </span>
                      </Hover>
                    </td>
                    <td style={cellNum()}>{fmtNum(m.icir_annual, 2)}</td>
                    <td style={cellNum()}>{fmtNum(Math.abs(m.rank_ic_mean ?? NaN), 3)}</td>
                    <td style={cellNum()}>{m.ic_win_rate != null ? fmtPct(m.ic_win_rate, 0) : '—'}</td>
                    <td style={cellNum()}>{fmtNum(m.ls_sharpe, 2)}</td>
                    <td style={cellNum('var(--err)')}>{m.ls_max_drawdown != null ? fmtPct(m.ls_max_drawdown, 1) : '—'}</td>
                    <td style={cellNum()}>{fmtNum(m.turnover, 2)}</td>
                    <td style={{ padding: '0 12px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--fg-3)' }}>{r.settled_n ?? '—'}</td>
                    <td style={{ padding: '0 12px' }}>
                      {severe ? <Badge group="degrade" value="严重" sm />
                        : r.degrade === 'partial' ? <Badge group="degrade" value="部分" sm />
                          : <span style={{ color: 'var(--fg-mute)' }}>—</span>}
                    </td>
                  </tr>
                );
              })}
              {rows.length === 0 && (
                <tr><td colSpan={12} style={{ padding: 24, textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-4)' }}>没有满足条件的候选</td></tr>
              )}
            </tbody>
          </table>
        </div>
        {/* footer */}
        <div style={{ padding: '10px 18px', background: 'var(--surface)', borderTop: '1px solid var(--border)', display: 'flex', alignItems: 'center', flexShrink: 0 }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)' }}>{rows.length} 个候选{collapse === 'best' ? '（已折叠同因子变体）' : ''}</span>
          <div style={{ flex: 1 }} />
          <button className="btn sm primary" disabled={rows.length === 0} onClick={onNext}>进入剪枝（{rows.length} 项）→</button>
        </div>
      </div>
    </div>
  );
}

function cellNum(color) {
  return { padding: '0 12px', textAlign: 'right', fontFamily: 'var(--font-mono)', fontVariantNumeric: 'tabular-nums', color: color || 'var(--fg-2)' };
}
function paramText(params) {
  const keys = Object.keys(params || {});
  if (!keys.length) return '—';
  return keys.map((k) => `${k}=${params[k]}`).join(' ');
}

// Weight bar — sliders rebuild the weights object (no renormalization; score
// normalizes by Σ|w|). Dragging recomputes scores client-side.
function WeightBar({ weights, setOneWeight, usePreset }) {
  return (
    <div style={{ padding: '8px 14px', background: 'var(--surface-2)', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 14, flexShrink: 0, flexWrap: 'wrap' }}>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase' }}>权重</span>
      {usePreset && <span className="chip neutral" style={{ height: 15, fontSize: 9 }}>{usePreset.replace('preset:', '')}</span>}
      <span style={{ color: 'var(--fg-mute)' }}>│</span>
      {WEIGHT_KEYS.map((w) => (
        <div key={w.k} style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 96 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--font-mono)', fontSize: 9.5 }}>
            <span style={{ color: 'var(--fg-3)' }}>{w.l}</span>
            <span style={{ color: 'var(--fg)', fontWeight: 600 }}>{((weights[w.k] || 0) * 100).toFixed(0)}%</span>
          </div>
          <input type="range" min="0" max="1" step="0.01" value={weights[w.k] || 0} onChange={(e) => setOneWeight(w.k, +e.target.value)} style={{ width: 96, accentColor: 'var(--fg)', height: 4 }} />
        </div>
      ))}
    </div>
  );
}

// ─── Step ② 低相关剪枝 ────────────────────────────────────────────────────────
function Step2({
  rows, correlation, orderMode, setOrderMode, threshold, setThreshold,
  maxN, setMaxN, forced, setForced, manualOrder, setManualOrder,
  basketId, sid, weightsParam, includeDegraded, onBack, onNext,
}) {
  const matrix = correlation.matrix || {};
  // candidates = current grid rows minus corr_insufficient
  const candidates = React.useMemo(() => rows.filter((r) => !r.corr_insufficient), [rows]);
  const insufficientCnt = rows.length - candidates.length;
  const labelOf = React.useMemo(() => {
    const o = {}; rows.forEach((r) => { o[r.item_key] = r.label; }); return o;
  }, [rows]);

  // priority order
  const order = React.useMemo(() => {
    if (orderMode === 'manual' && manualOrder) {
      // keep only still-present candidates, append any new ones
      const present = new Set(candidates.map((r) => r.item_key));
      const kept = manualOrder.filter((k) => present.has(k));
      candidates.forEach((r) => { if (!kept.includes(r.item_key)) kept.push(r.item_key); });
      return kept;
    }
    if (typeof orderMode === 'string' && orderMode.startsWith('metric:')) {
      const mk = orderMode.slice(7).replace(/_desc$/, '');
      return [...candidates].sort((a, b) => (b.metrics?.[mk] ?? -Infinity) - (a.metrics?.[mk] ?? -Infinity)).map((r) => r.item_key);
    }
    // score order (already sorted by _score)
    return candidates.map((r) => r.item_key);
  }, [orderMode, manualOrder, candidates]);

  // initialize manual order lazily when entering manual mode
  React.useEffect(() => {
    if (orderMode === 'manual' && !manualOrder) setManualOrder(candidates.map((r) => r.item_key));
  }, [orderMode, manualOrder, candidates, setManualOrder]);

  const maxNum = maxN === '' ? null : Math.max(1, Number(maxN) || 1);

  // live client-side preview
  const preview = React.useMemo(
    () => previewPrune(candidates.map((r) => r.item_key), order, matrix, threshold, maxNum, forced),
    [candidates, order, matrix, threshold, maxNum, forced],
  );

  // authoritative backend prune (debounced reconcile)
  const pruneM = useMutation({
    mutationFn: (body) => endpoints.screeningPrune(basketId, sid, body),
  });
  const debTimer = React.useRef(null);
  const requestPrune = React.useCallback(() => {
    const body = {
      candidates: candidates.map((r) => r.item_key),
      order_mode: orderMode,
      threshold,
      forced,
      weights: weightsParam,
      include_degraded: includeDegraded,
    };
    if (maxNum != null) body.max_n = maxNum;
    if (orderMode === 'manual') body.manual_order = order;
    pruneM.mutate(body);
  }, [candidates, order, orderMode, threshold, forced, maxNum, weightsParam, includeDegraded]); // eslint-disable-line
  React.useEffect(() => {
    if (debTimer.current) clearTimeout(debTimer.current);
    debTimer.current = setTimeout(() => requestPrune(), 450);
    return () => { if (debTimer.current) clearTimeout(debTimer.current); };
  }, [requestPrune]);

  // prefer authoritative result if it's current; else show live preview
  const authoritative = pruneM.data;
  const kept = authoritative?.kept || preview.kept;
  const pruned = authoritative?.pruned || preview.pruned;

  const corrDrop = pruned.filter((d) => (d.decision || '').includes('corr')).length;
  const capDrop = pruned.filter((d) => (d.decision || '').includes('capped')).length;
  const manualCnt = kept.filter((k) => (k.decision || '').includes('manual')).length;

  const moveManual = (key, dir) => {
    setManualOrder((prev) => {
      const arr = prev ? [...prev] : order.slice();
      const from = arr.indexOf(key);
      const to = from + dir;
      if (from < 0 || to < 0 || to >= arr.length) return arr;
      arr.splice(to, 0, arr.splice(from, 1)[0]);
      return arr;
    });
  };
  const addForced = (key) => setForced((f) => (f.includes(key) ? f : [...f, key]));

  const orderedCands = order.map((k) => candidates.find((r) => r.item_key === k)).filter(Boolean);

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      {/* control row */}
      <div style={{ padding: '8px 16px', background: 'var(--surface)', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 18, flexShrink: 0, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>阈值</span>
          <input type="range" min="0.5" max="0.95" step="0.01" value={threshold} onChange={(e) => setThreshold(+e.target.value)} style={{ width: 140, accentColor: 'var(--fg)' }} />
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 600, color: 'var(--fg)', width: 40 }}>{Number(threshold).toFixed(2)}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>保留上限</span>
          <input value={maxN} placeholder="不限" onChange={(e) => setMaxN(e.target.value.replace(/[^0-9]/g, ''))}
            style={{ width: 56, height: 24, padding: '0 8px', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--fg)' }} />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>优先序</span>
          <Segmented
            options={[{ v: 'score', l: '推荐得分' }, { v: 'metric:icir_annual_desc', l: '单指标' }, { v: 'manual', l: '手动' }]}
            value={orderMode}
            onChange={setOrderMode}
          />
        </div>
        <div style={{ flex: 1 }} />
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: pruneM.isPending ? 'var(--info)' : 'var(--ok)' }}>
          {pruneM.isPending ? '● 后端核对中…' : authoritative ? '● 已与后端核对' : '● 即时预览'}
        </span>
      </div>

      {insufficientCnt > 0 && (
        <div style={{ padding: '8px 16px 0', background: 'var(--bg)' }}>
          <LightHint tone="warn">{insufficientCnt} 个条目因样本不足未参与剪枝</LightHint>
        </div>
      )}

      <div style={{ flex: 1, display: 'flex', gap: 12, padding: 12, minHeight: 0 }}>
        {/* manual order column (only manual mode) */}
        {orderMode === 'manual' && (
          <div style={{ width: 240, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, display: 'flex', flexDirection: 'column', minHeight: 0, flexShrink: 0 }}>
            <SectionHead title="候选优先序" meta="上下移动 · 顺序决定保留优先级" />
            <div className="scroll" style={{ flex: 1, minHeight: 0 }}>
              {orderedCands.map((r, i) => (
                <div key={r.item_key} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '5px 10px', borderBottom: '1px solid var(--border-faint)' }}>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-4)', width: 16 }}>{i + 1}</span>
                  <span style={{ flex: 1, fontSize: 11, color: 'var(--fg)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.label}</span>
                  <button className="btn ghost sm" style={{ width: 18, padding: 0 }} disabled={i === 0} onClick={() => moveManual(r.item_key, -1)}>↑</button>
                  <button className="btn ghost sm" style={{ width: 18, padding: 0 }} disabled={i === orderedCands.length - 1} onClick={() => moveManual(r.item_key, 1)}>↓</button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* dual columns */}
        <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, minHeight: 0 }}>
          {/* keep */}
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            <SectionHead title="保留" meta={`${kept.length} 项`} right={<span className="chip ok" style={{ height: 16, fontSize: 9 }}>✓ {kept.length}</span>} />
            <div className="scroll" style={{ flex: 1, minHeight: 0 }}>
              {kept.map((k, i) => {
                const row = candidates.find((r) => r.item_key === k.item_key);
                return (
                  <div key={k.item_key} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '7px 12px', borderBottom: '1px solid var(--border-faint)' }}>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-4)', width: 16 }}>{i + 1}</span>
                    <span style={{ flex: 1, fontSize: 11.5, color: 'var(--fg)', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{k.label || labelOf[k.item_key]}</span>
                    {(k.decision || '').includes('manual') && <Badge group="decision" value="手动保留" sm />}
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-2)' }}>{fmtNum(k.score ?? row?._score, 3)}</span>
                  </div>
                );
              })}
              {kept.length === 0 && <div style={{ padding: 16, fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-4)' }}>无保留项</div>}
            </div>
          </div>
          {/* drop */}
          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
            <SectionHead title="剔除" meta={`${pruned.length} 项`} right={<span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--fg-3)' }}>相关{corrDrop} · 超限{capDrop}</span>} />
            <div className="scroll" style={{ flex: 1, minHeight: 0 }}>
              {pruned.map((d) => {
                const isCorr = (d.decision || '').includes('corr');
                return (
                  <div key={d.item_key} style={{ padding: '7px 12px', borderBottom: '1px solid var(--border-faint)' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ flex: 1, fontSize: 11.5, color: 'var(--fg-2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.label || labelOf[d.item_key]}</span>
                      <Badge group="decision" value={isCorr ? '因相关剔除' : '超上限剔除'} sm />
                      <button className="btn ghost sm" style={{ fontSize: 9.5, height: 18, color: 'var(--info)' }} onClick={() => addForced(d.item_key)}>仍要加入</button>
                    </div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-4)', marginTop: 3 }}>
                      {isCorr
                        ? `与 ${labelOf[d.blocked_by] || d.blocked_by || '某条目'} 相关 ρ=${d.rho != null ? Number(d.rho).toFixed(2) : '—'} > 阈值 ${Number(threshold).toFixed(2)}`
                        : `超出保留上限 ${maxNum}`}
                    </div>
                  </div>
                );
              })}
              {pruned.length === 0 && <div style={{ padding: 16, fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-4)' }}>无剔除项</div>}
            </div>
          </div>
        </div>
      </div>

      <div style={{ padding: '10px 18px', background: 'var(--surface)', borderTop: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
        <button className="btn sm" onClick={onBack}>← 返回筛选</button>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)' }}>保留 {kept.length} · 剔除 {pruned.length}（相关 {corrDrop} / 超限 {capDrop} / 手动 {manualCnt}）</span>
        <div style={{ flex: 1 }} />
        <button className="btn sm primary" disabled={kept.length === 0} onClick={onNext}>保存为入选集合 →</button>
      </div>
    </div>
  );
}

// ─── Step ③ 保存入选集合 ─────────────────────────────────────────────────────
function Step3({
  rows, correlation, calibration, basketId, sid,
  filtersJson, weightsParam, collapse, includeDegraded,
  orderMode, manualOrder, threshold, maxN, forced, onBack, onCreated,
}) {
  const matrix = correlation.matrix || {};
  const candidates = React.useMemo(() => rows.filter((r) => !r.corr_insufficient), [rows]);
  const maxNum = maxN === '' ? null : Math.max(1, Number(maxN) || 1);

  // recompute order consistently with step ②
  const order = React.useMemo(() => {
    if (orderMode === 'manual' && manualOrder) {
      const present = new Set(candidates.map((r) => r.item_key));
      const kept = manualOrder.filter((k) => present.has(k));
      candidates.forEach((r) => { if (!kept.includes(r.item_key)) kept.push(r.item_key); });
      return kept;
    }
    if (typeof orderMode === 'string' && orderMode.startsWith('metric:')) {
      const mk = orderMode.slice(7).replace(/_desc$/, '');
      return [...candidates].sort((a, b) => (b.metrics?.[mk] ?? -Infinity) - (a.metrics?.[mk] ?? -Infinity)).map((r) => r.item_key);
    }
    return candidates.map((r) => r.item_key);
  }, [orderMode, manualOrder, candidates]);

  const { kept } = React.useMemo(
    () => previewPrune(candidates.map((r) => r.item_key), order, matrix, threshold, maxNum, forced),
    [candidates, order, matrix, threshold, maxNum, forced],
  );
  const keptRows = kept.map((k) => ({ ...k, row: candidates.find((r) => r.item_key === k.item_key) }));
  const hasSevere = keptRows.some((k) => k.row?.degrade === 'severe');

  const [name, setName] = React.useState(`${calibration.name || '篮子'} · 优选`);
  const [ack, setAck] = React.useState(false);
  const [blocked, setBlocked] = React.useState(false); // DEGRADED_INPUT_BLOCKED hint

  const orderModeLabel = orderMode === 'score' ? '推荐得分' : orderMode === 'manual' ? '手动拖拽' : `单指标 · ${String(orderMode).replace('metric:', '').replace(/_desc$/, '')}`;
  let weightsLabel = weightsParam;
  if (weightsParam && weightsParam.startsWith('preset:')) weightsLabel = weightsParam.replace('preset:', '');
  else { try { const w = JSON.parse(weightsParam); weightsLabel = Object.entries(w).map(([k, v]) => `${(Number(v) * 100).toFixed(0)}%`).join('/'); } catch { /* keep */ } }

  const createM = useMutation({
    mutationFn: () => endpoints.createSelection({
      basket_id: basketId,
      snapshot_id: sid,
      name,
      criteria: {
        filters: filtersJson,
        weights: weightsParam && weightsParam.startsWith('preset:') ? weightsParam : (() => { try { return JSON.parse(weightsParam); } catch { return {}; } })(),
        collapse,
        order_mode: orderMode,
        manual_order: orderMode === 'manual' ? order : undefined,
        threshold,
        max_n: maxNum,
        forced,
        acknowledge_degraded: ack,
        include_degraded: includeDegraded,
      },
    }),
    onSuccess: (sel) => onCreated(sel.selection_id || sel.id),
    onError: (err) => {
      if (err?.apiError?.code === 'DEGRADED_INPUT_BLOCKED' || err?.response?.status === 422) setBlocked(true);
    },
  });

  const summaryRows = [
    ['来源篮子', calibration.name || basketId],
    ['快照', `${sid} · rev ${calibration.rev ?? '—'}`],
    ['保留条目', `${kept.length} 项`],
    ['优先序模式', orderModeLabel],
    ['相关性阈值', Number(threshold).toFixed(2)],
    ['保留上限', maxNum == null ? '不限' : maxNum],
    ['折叠', collapse === 'best' ? '同因子只看最优' : '全部变体'],
    ['手动保留', forced.length ? `${forced.length} 项` : '无'],
    ['纳入降级', includeDegraded === 'all' ? '全部' : includeDegraded === 'partial' ? '含部分' : '不含'],
    ['权重', weightsLabel],
  ];

  return (
    <div style={{ flex: 1, overflow: 'auto', background: 'var(--bg)', padding: 24, display: 'flex', justifyContent: 'center' }}>
      <div style={{ width: 640 }}>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 15, fontWeight: 500, color: 'var(--fg)', marginBottom: 4 }}>保存入选集合</div>
        <div style={{ fontSize: 11, color: 'var(--fg-3)', marginBottom: 18 }}>生成一份<b style={{ color: 'var(--fg)' }}>草稿</b>状态的不可变存档：选中了哪些因子、为什么。</div>

        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 11, color: 'var(--fg-2)', marginBottom: 6 }}>命名 *</div>
          <input value={name} onChange={(e) => setName(e.target.value)}
            style={{ width: '100%', height: 32, padding: '0 12px', background: 'var(--surface)', border: '1px solid var(--border-strong)', borderRadius: 4, fontSize: 13, fontWeight: 500, color: 'var(--fg)' }} />
        </div>

        <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden', marginBottom: 16 }}>
          <SectionHead title="摘要回顾" meta="只读" />
          <div style={{ padding: '12px 16px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 24px', fontSize: 11.5 }}>
            {summaryRows.map(([k, v]) => (
              <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, borderBottom: '1px solid var(--border-faint)', padding: '3px 0' }}>
                <span style={{ color: 'var(--fg-3)' }}>{k}</span>
                <span style={{ color: 'var(--fg)', fontFamily: 'var(--font-mono)', fontSize: 11, textAlign: 'right' }}>{v}</span>
              </div>
            ))}
          </div>
          <div style={{ padding: '10px 16px', borderTop: '1px solid var(--border-faint)' }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', marginBottom: 6 }}>保留成员</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {keptRows.map((k) => (
                <span key={k.item_key} className="chip ok" style={{ height: 20 }}>
                  {k.label || k.row?.label} {fmtNum(k.score ?? k.row?._score, 2)}{(k.decision || '').includes('manual') ? ' ✦' : ''}
                </span>
              ))}
              {keptRows.length === 0 && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-4)' }}>无</span>}
            </div>
          </div>
        </div>

        {hasSevere && (
          <div style={{ marginBottom: 16 }}>
            <label onClick={() => setAck(!ack)} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px', background: 'var(--err-bg)', border: '1px solid var(--err)', borderRadius: 5, cursor: 'pointer' }}>
              <span style={{ width: 15, height: 15, borderRadius: 3, border: '1px solid var(--err)', background: ack ? 'var(--err)' : 'var(--surface)', color: '#fff', fontSize: 10, lineHeight: '14px', textAlign: 'center' }}>{ack ? '✓' : ''}</span>
              <span style={{ fontSize: 11.5, color: 'var(--fg-2)' }}>我已知晓所选集合包含<b style={{ color: 'var(--err)' }}>严重降级</b>的结果</span>
            </label>
          </div>
        )}

        {blocked && (
          <div style={{ marginBottom: 16 }}>
            <LightHint tone="err">后端拒绝：所选集合包含严重降级结果，请勾选上方确认框后重试。</LightHint>
          </div>
        )}
        {createM.isError && !blocked && (
          <div style={{ marginBottom: 16 }}>
            <LightHint tone="err">{createM.error?.apiError?.message || '创建失败'}</LightHint>
          </div>
        )}

        <div style={{ display: 'flex', gap: 10 }}>
          <button className="btn sm" onClick={onBack}>← 返回剪枝</button>
          <div style={{ flex: 1 }} />
          <button
            className="btn sm primary"
            disabled={!name.trim() || kept.length === 0 || (hasSevere && !ack) || createM.isPending}
            onClick={() => { setBlocked(false); createM.mutate(); }}
          >
            {createM.isPending ? '创建中…' : '创建集合'}
          </button>
        </div>
      </div>
    </div>
  );
}
