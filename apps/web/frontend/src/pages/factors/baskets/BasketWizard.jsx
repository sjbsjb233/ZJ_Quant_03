// ─────────────────────────────────────────────────────────────────────────────
// BasketWizard.jsx — §10.1 新建因子篮子 (5 步向导). Ported from the Claude Design
// mockup fl-basket-wizard.jsx into the real terminal shell + react-query + API.
//   ① 选因子 · ② 公共参数 · ③ 条目参数 · ④ 相关性与追踪 · ⑤ 预检与提交
// The step bar is clickable (go back), content persists across steps. Same factor
// can be added multiple times (each → an entry); ★参数扫描 expands one entry into N.
// The shared_config is FORCED uniform across the basket — that is the precondition
// for in-basket correlation + cross comparison.
// ─────────────────────────────────────────────────────────────────────────────
import React, { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { endpoints } from '../../../lib/api';
import { TerminalShell } from '../../../components/shell';
import { Loading, ErrorState, Empty, Modal } from '../../../components/ui';
import { Badge, Segmented, Toggle, SectionHead, LightHint, ParamChips } from './kit';

const WIZ_STEPS = [
  { id: 1, l: '选因子' }, { id: 2, l: '公共参数' }, { id: 3, l: '条目参数' },
  { id: 4, l: '相关性与追踪' }, { id: 5, l: '预检与提交' },
];

const HORIZON_OPTS = [5, 10, 20, 60];
let ENTRY_SEQ = 0;
function nextKey() { ENTRY_SEQ += 1; return `e_${ENTRY_SEQ}_${Math.random().toString(36).slice(2, 6)}`; }

// Build a fresh entry from a factor library row.
function mkEntry(f) {
  const name = f.display_name || f.base_name || f.factor_id;
  return {
    key: nextKey(),
    factorId: f.factor_id,
    factorName: name,
    category: f.category || '—',
    direction: f.direction || '+',
    version: f.version,
    label: name,
    params: {},
  };
}

// Default label for an entry = factor name + params summary.
function defaultLabel(e) {
  const ks = Object.keys(e.params || {});
  if (!ks.length) return e.factorName;
  return `${e.factorName}·${ks.map((k) => `${k}=${e.params[k]}`).join('·')}`;
}

// ★参数扫描: parse "5,10,20" or "5..8" → ordered unique numbers.
function parseScan(raw) {
  const out = [];
  String(raw || '').split(',').forEach((part) => {
    part = part.trim();
    if (!part) return;
    if (part.includes('..')) {
      const [a, b] = part.split('..').map(Number);
      if (!Number.isNaN(a) && !Number.isNaN(b) && b >= a && b - a < 50) {
        for (let i = a; i <= b; i++) out.push(i);
      }
    } else if (!Number.isNaN(Number(part))) {
      out.push(Number(part));
    }
  });
  return Array.from(new Set(out));
}

export default function BasketWizard() {
  const navigate = useNavigate();
  const qc = useQueryClient();

  const factorsQ = useQuery({ queryKey: ['factors', { page_size: 200 }], queryFn: () => endpoints.factors({ page_size: 200 }) });
  const universesQ = useQuery({ queryKey: ['universes'], queryFn: () => endpoints.universes() });

  const [step, setStep] = useState(1);
  const [name, setName] = useState('新建篮子');
  const [entries, setEntries] = useState([]);

  // ── shared_config state ──────────────────────────────────────────────────
  const [universeId, setUniverseId] = useState('');
  const [winMode, setWinMode] = useState('rolling');      // 'rolling' | 'fixed'
  const [winSpan, setWinSpan] = useState('365d');         // when rolling
  const [winStart, setWinStart] = useState('2020-01-01'); // when fixed
  const [winEnd, setWinEnd] = useState('2023-12-31');
  const [rebalance, setRebalance] = useState('W');        // 'D' | 'W' | 'M'
  const [horizons, setHorizons] = useState([5, 10, 20]);
  const [primaryHorizon, setPrimaryHorizon] = useState(20);
  const [icType, setIcType] = useState('rank');           // 'rank' | 'pearson'
  const [returnPrice, setReturnPrice] = useState('close');
  const [winsorize, setWinsorize] = useState('mad');
  const [winsorizeN, setWinsorizeN] = useState(3);
  const [standardize, setStandardize] = useState('zscore');
  const [neutralizeSize, setNeutralizeSize] = useState(true);
  const [sizeField, setSizeField] = useState('circ_mv');
  const [neutralizeIndustry, setNeutralizeIndustry] = useState(true);
  const [classificationSystem, setClassificationSystem] = useState('SW');
  const [industryLevel, setIndustryLevel] = useState(1);
  const [quantiles, setQuantiles] = useState(10);
  const [layerWeight, setLayerWeight] = useState('equal'); // 'equal' | 'cap'
  const [costBps, setCostBps] = useState(15);

  // ── correlation + tracking ───────────────────────────────────────────────
  const [corrOn, setCorrOn] = useState(true);
  const [corrMethod, setCorrMethod] = useState('pearson'); // 'pearson' | 'spearman'
  const [threshold, setThreshold] = useState(0.70);
  const [corrMinPeriods, setCorrMinPeriods] = useState(60);
  const [trackOn, setTrackOn] = useState(false);
  const [trackHour, setTrackHour] = useState(18);
  const [trackMinute, setTrackMinute] = useState(30);
  const [runNow, setRunNow] = useState(true);

  // ── scan modal ───────────────────────────────────────────────────────────
  const [scanFor, setScanFor] = useState(null);

  const trackingDisabled = winMode === 'fixed';
  const universes = universesQ.data?.items || [];

  // default-select first universe once loaded
  React.useEffect(() => {
    if (!universeId && universes.length) setUniverseId(universes[0].universe_id);
  }, [universes, universeId]);

  // keep primary_horizon valid w.r.t. selected horizons
  React.useEffect(() => {
    if (horizons.length && !horizons.includes(primaryHorizon)) setPrimaryHorizon(horizons[0]);
  }, [horizons, primaryHorizon]);

  function addFactor(f) { setEntries((es) => [...es, mkEntry(f)]); }
  function delEntry(idx) { setEntries((es) => es.filter((_, i) => i !== idx)); }
  function move(idx, d) {
    setEntries((es) => {
      const n = [...es]; const j = idx + d;
      if (j < 0 || j >= n.length) return es;
      [n[idx], n[j]] = [n[j], n[idx]];
      return n;
    });
  }
  function dupEntry(idx) {
    setEntries((es) => {
      const e = es[idx];
      return [...es.slice(0, idx + 1), { ...e, key: nextKey(), params: { ...e.params } }, ...es.slice(idx + 1)];
    });
  }
  function patchEntry(idx, patch) {
    setEntries((es) => es.map((e, i) => (i === idx ? { ...e, ...patch } : e)));
  }
  function setEntryParam(idx, k, v) {
    setEntries((es) => es.map((e, i) => {
      if (i !== idx) return e;
      const params = { ...e.params };
      if (v === '' || v == null) delete params[k]; else params[k] = v;
      const wasDefault = e.label === defaultLabel(e);
      const ne = { ...e, params };
      if (wasDefault) ne.label = defaultLabel(ne);
      return ne;
    }));
  }
  // expand entry idx into N variants over a numeric param `pkey`.
  function applyScan(idx, pkey, values) {
    setEntries((es) => {
      const e = es[idx];
      const variants = values.map((v) => {
        const params = { ...e.params, [pkey]: v };
        const ne = { ...e, key: nextKey(), params };
        ne.label = defaultLabel(ne);
        return ne;
      });
      return [...es.slice(0, idx), ...variants, ...es.slice(idx + 1)];
    });
    setScanFor(null);
  }

  // ── build createBasket body (§ body spec) ────────────────────────────────
  const sharedConfig = useMemo(() => ({
    universe_id: universeId,
    window: winMode === 'rolling'
      ? { mode: 'rolling', span: winSpan }
      : { mode: 'fixed', start: winStart, end: winEnd },
    rebalance,
    horizons,
    primary_horizon: primaryHorizon,
    ic_type: icType,
    return_price: returnPrice,
    winsorize,
    winsorize_n: Number(winsorizeN),
    standardize,
    neutralize_size: neutralizeSize,
    size_field: sizeField,
    neutralize_industry: neutralizeIndustry,
    classification_system: classificationSystem,
    industry_level: Number(industryLevel),
    quantiles: Number(quantiles),
    layer_weight: layerWeight,
    cost_bps: Number(costBps),
  }), [universeId, winMode, winSpan, winStart, winEnd, rebalance, horizons, primaryHorizon, icType,
    returnPrice, winsorize, winsorizeN, standardize, neutralizeSize, sizeField, neutralizeIndustry,
    classificationSystem, industryLevel, quantiles, layerWeight, costBps]);

  const body = useMemo(() => ({
    name,
    description: '',
    shared_config: sharedConfig,
    items: entries.map((e) => ({
      factor_id: e.factorId,
      version: e.version,
      params: e.params,
      label: e.label || defaultLabel(e),
    })),
    correlation_enabled: corrOn,
    correlation_method: corrMethod,
    correlation_threshold: Number(threshold),
    corr_min_periods: Number(corrMinPeriods),
    tracking: { enabled: trackOn && !trackingDisabled, hour: Number(trackHour), minute: Number(trackMinute) },
    run_now: runNow,
  }), [name, sharedConfig, entries, corrOn, corrMethod, threshold, corrMinPeriods, trackOn,
    trackingDisabled, trackHour, trackMinute, runNow]);

  const createMut = useMutation({
    mutationFn: (b) => endpoints.createBasket(b),
    onSuccess: (resp) => {
      qc.invalidateQueries({ queryKey: ['baskets'] });
      navigate('/factors/baskets/' + resp.basket_id);
    },
  });
  const createErr = createMut.error?.apiError?.message;

  const canNext = step === 1 ? entries.length >= 1 && !!name.trim() : true;

  function footerNote() {
    if (step === 1) return `已选 ${entries.length} 个条目${entries.length === 1 ? ' · 相关性分析需 ≥2 个条目，仍可继续' : ''}`;
    if (step === 5) return '预检通过 · 可创建';
    return '';
  }

  // ── shell-level loading / error for the factor library ───────────────────
  let bodyEl;
  if (factorsQ.isLoading || universesQ.isLoading) {
    bodyEl = <Loading />;
  } else if (factorsQ.isError) {
    bodyEl = <ErrorState error={factorsQ.error} onRetry={factorsQ.refetch} />;
  } else if (universesQ.isError) {
    bodyEl = <ErrorState error={universesQ.error} onRetry={universesQ.refetch} />;
  } else {
    bodyEl = (
      <>
        {/* Step bar */}
        <div style={{ padding: '12px 24px', background: 'var(--surface)', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 0, flexShrink: 0 }}>
          {WIZ_STEPS.map((s, i) => {
            const done = s.id < step; const cur = s.id === step;
            return (
              <React.Fragment key={s.id}>
                <button onClick={() => s.id <= step && setStep(s.id)} style={{ display: 'flex', alignItems: 'center', gap: 8, background: 'none', border: 'none', padding: 0, cursor: s.id <= step ? 'pointer' : 'default' }}>
                  <span style={{ width: 22, height: 22, borderRadius: '50%', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 600, background: cur ? 'var(--fg)' : done ? 'var(--ok)' : 'var(--surface-3)', color: cur || done ? 'var(--bg)' : 'var(--fg-3)', border: cur ? 'none' : '1px solid var(--border)' }}>{done ? '✓' : s.id}</span>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: cur ? 'var(--fg)' : done ? 'var(--fg-2)' : 'var(--fg-4)', fontWeight: cur ? 500 : 400 }}>{s.l}</span>
                </button>
                {i < WIZ_STEPS.length - 1 && <div style={{ flex: 1, height: 1, background: done ? 'var(--ok)' : 'var(--border)', margin: '0 12px' }} />}
              </React.Fragment>
            );
          })}
        </div>

        <div className="scroll" style={{ flex: 1, minHeight: 0, background: 'var(--bg)' }}>
          {step === 1 && (
            <Step1 factors={factorsQ.data?.items || []} entries={entries} addFactor={addFactor}
              delEntry={delEntry} move={move} name={name} setName={setName} />
          )}
          {step === 2 && (
            <Step2
              universes={universes} universeId={universeId} setUniverseId={setUniverseId}
              winMode={winMode} setWinMode={setWinMode} winSpan={winSpan} setWinSpan={setWinSpan}
              winStart={winStart} setWinStart={setWinStart} winEnd={winEnd} setWinEnd={setWinEnd}
              rebalance={rebalance} setRebalance={setRebalance} horizons={horizons} setHorizons={setHorizons}
              primaryHorizon={primaryHorizon} setPrimaryHorizon={setPrimaryHorizon}
              icType={icType} setIcType={setIcType} returnPrice={returnPrice} setReturnPrice={setReturnPrice}
              winsorize={winsorize} setWinsorize={setWinsorize} winsorizeN={winsorizeN} setWinsorizeN={setWinsorizeN}
              standardize={standardize} setStandardize={setStandardize}
              neutralizeSize={neutralizeSize} setNeutralizeSize={setNeutralizeSize} sizeField={sizeField} setSizeField={setSizeField}
              neutralizeIndustry={neutralizeIndustry} setNeutralizeIndustry={setNeutralizeIndustry}
              classificationSystem={classificationSystem} setClassificationSystem={setClassificationSystem}
              industryLevel={industryLevel} setIndustryLevel={setIndustryLevel}
              quantiles={quantiles} setQuantiles={setQuantiles} layerWeight={layerWeight} setLayerWeight={setLayerWeight}
              costBps={costBps} setCostBps={setCostBps}
            />
          )}
          {step === 3 && (
            <Step3 entries={entries} patchEntry={patchEntry} setEntryParam={setEntryParam}
              dupEntry={dupEntry} delEntry={delEntry} setScanFor={setScanFor} />
          )}
          {step === 4 && (
            <Step4
              corrOn={corrOn} setCorrOn={setCorrOn} corrMethod={corrMethod} setCorrMethod={setCorrMethod}
              threshold={threshold} setThreshold={setThreshold} corrMinPeriods={corrMinPeriods} setCorrMinPeriods={setCorrMinPeriods}
              trackOn={trackOn} setTrackOn={setTrackOn} trackHour={trackHour} setTrackHour={setTrackHour}
              trackMinute={trackMinute} setTrackMinute={setTrackMinute} disabled={trackingDisabled}
            />
          )}
          {step === 5 && (
            <Step5 name={name} entries={entries} sharedConfig={sharedConfig} universes={universes}
              winMode={winMode} corrOn={corrOn} corrMethod={corrMethod} threshold={threshold}
              trackOn={trackOn && !trackingDisabled} trackHour={trackHour} trackMinute={trackMinute}
              runNow={runNow} setRunNow={setRunNow} error={createErr} />
          )}
        </div>

        {/* Footer nav */}
        <div style={{ padding: '12px 24px', background: 'var(--surface)', borderTop: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
          <button className="btn sm" disabled={step === 1} onClick={() => setStep((s) => s - 1)}>← 上一步</button>
          <div style={{ flex: 1, fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)' }}>{footerNote()}</div>
          {step < 5 ? (
            <button className="btn primary sm" disabled={!canNext} onClick={() => setStep((s) => s + 1)}>下一步 →</button>
          ) : (
            <button className="btn primary sm" disabled={createMut.isPending || !entries.length || !universeId} onClick={() => createMut.mutate(body)}>
              {createMut.isPending ? '提交中…' : (runNow ? '创建并立即运行' : '创建篮子')}
            </button>
          )}
        </div>

        {scanFor != null && entries[scanFor] && (
          <ScanModal idx={scanFor} entry={entries[scanFor]} onClose={() => setScanFor(null)} onApply={applyScan} />
        )}
      </>
    );
  }

  return (
    <TerminalShell
      module="factor"
      current="baskets"
      path={['factor_mgmt/', 'baskets/', '新建篮子']}
      title="新建因子篮子"
      subtitle={`向导 · 第 ${step}/5 步 · ${WIZ_STEPS[step - 1].l}`}
      actions={<button className="btn ghost sm" onClick={() => navigate('/factors/baskets')}>✕ 退出向导</button>}
    >
      {bodyEl}
    </TerminalShell>
  );
}

// ─── small form atoms (local) ────────────────────────────────────────────────
function Field({ label, required, hint, children }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
      <div style={{ width: 110, flexShrink: 0, paddingTop: 4 }}>
        <span style={{ fontSize: 11, color: 'var(--fg-2)' }}>{label}</span>
        {required && <span style={{ color: 'var(--err)', marginLeft: 3 }}>*</span>}
      </div>
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        {children}
        {hint && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', flexBasis: '100%' }}>{hint}</span>}
      </div>
    </div>
  );
}
function TInput({ value, onChange, w = 90, suffix, placeholder, type = 'text' }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
      <input type={type} value={value} placeholder={placeholder}
        onChange={(e) => onChange && onChange(e.target.value)}
        style={{ width: w, height: 26, padding: '0 8px', background: 'var(--surface-2)', border: '1px solid var(--border-strong)', borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--fg)' }} />
      {suffix && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)' }}>{suffix}</span>}
    </span>
  );
}
function TSelect({ value, onChange, options, w = 130 }) {
  return (
    <select value={value} onChange={(e) => onChange && onChange(e.target.value)}
      style={{ width: w, height: 26, padding: '0 8px', background: 'var(--surface-2)', border: '1px solid var(--border-strong)', borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--fg)' }}>
      {options.map((o) => {
        const v = typeof o === 'string' || typeof o === 'number' ? o : o.v;
        const l = typeof o === 'string' || typeof o === 'number' ? o : o.l;
        return <option key={v} value={v}>{l}</option>;
      })}
    </select>
  );
}
function chip(active) {
  return { height: 22, padding: '0 9px', borderRadius: 3, border: 'none', cursor: 'pointer', fontFamily: 'var(--font-mono)', fontSize: 10.5, background: active ? 'var(--info-bg)' : 'var(--surface-2)', color: active ? 'var(--info)' : 'var(--fg-3)' };
}

// ─── Step 1 · 选因子 ─────────────────────────────────────────────────────────
function Step1({ factors, entries, addFactor, delEntry, move, name, setName }) {
  const [cat, setCat] = useState('全部');
  const cats = useMemo(() => ['全部', ...Array.from(new Set(factors.map((f) => f.category).filter(Boolean)))], [factors]);
  const list = useMemo(() => factors.filter((f) => cat === '全部' || f.category === cat), [factors, cat]);

  return (
    <div style={{ padding: 20 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
        <span style={{ fontSize: 11, color: 'var(--fg-2)' }}>篮子名称 *</span>
        <input value={name} onChange={(e) => setName(e.target.value)}
          style={{ width: 280, height: 28, padding: '0 10px', background: 'var(--surface)', border: '1px solid var(--border-strong)', borderRadius: 4, fontSize: 13, fontWeight: 500, color: 'var(--fg)' }} />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16, height: 540 }}>
        {/* Selector */}
        <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <SectionHead title="因子库选择器" meta="点「添加」可重复加入" />
          <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--border-faint)', display: 'flex', gap: 6, flexWrap: 'wrap' }}>
            {cats.map((c) => <button key={c} onClick={() => setCat(c)} style={chip(cat === c)}>{c}</button>)}
          </div>
          <div className="scroll" style={{ flex: 1, minHeight: 0 }}>
            {list.length === 0 ? (
              <Empty label="无可用因子" hint="该分类下没有因子" />
            ) : list.map((f) => {
              const nm = f.display_name || f.base_name || f.factor_id;
              const dir = f.direction || '+';
              return (
                <div key={f.factor_id} style={{ padding: '7px 12px', borderBottom: '1px solid var(--border-faint)', display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ fontFamily: 'var(--font-mono)', color: dir === '+' ? 'var(--ok)' : 'var(--err)', fontWeight: 600, width: 10 }}>{dir}</span>
                  <div style={{ flex: 1 }}>
                    <span style={{ fontSize: 12, color: 'var(--fg)', fontWeight: 500 }}>{nm}</span>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--fg-4)', marginLeft: 6 }}>{f.category || '—'}{f.base_name && f.base_name !== nm ? ` · ${f.base_name}` : ''}</span>
                  </div>
                  <button className="btn sm" onClick={() => addFactor(f)}>+ 添加</button>
                </div>
              );
            })}
          </div>
        </div>
        {/* Selected entries */}
        <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <SectionHead title={`已选条目 · ${entries.length}`} meta="顺序即子任务执行顺序" />
          <div className="scroll" style={{ flex: 1, minHeight: 0 }}>
            {entries.length === 0 ? (
              <div style={{ padding: 30, textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-4)' }}>从左侧添加因子 · 同一因子可重复添加，后续配不同参数</div>
            ) : entries.map((e, i) => (
              <div key={e.key} style={{ padding: '7px 12px', borderBottom: '1px solid var(--border-faint)', display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-4)', width: 18 }}>{i + 1}</span>
                <div style={{ flex: 1 }}>
                  <span style={{ fontSize: 12, color: 'var(--fg)', fontWeight: 500 }}>{e.label}</span>
                  <span style={{ marginLeft: 6 }}><ParamChips params={e.params} /></span>
                </div>
                <button className="btn ghost sm" style={{ width: 22, padding: 0 }} onClick={() => move(i, -1)}>↑</button>
                <button className="btn ghost sm" style={{ width: 22, padding: 0 }} onClick={() => move(i, 1)}>↓</button>
                <button className="btn ghost sm" style={{ width: 22, padding: 0, color: 'var(--err)' }} onClick={() => delEntry(i)}>✕</button>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Step 2 · 公共参数 ───────────────────────────────────────────────────────
function ParamGroup({ title, tone, desc, children, mt }) {
  const col = tone === 'red' ? 'var(--err)' : 'var(--warn)';
  return (
    <div style={{ marginTop: mt || 0, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
      <div style={{ padding: '8px 16px', background: 'var(--surface-2)', borderBottom: '1px solid var(--border)', borderLeft: `3px solid ${col}`, display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 600, color: 'var(--fg)' }}>{title}</span>
        <span style={{ fontSize: 10.5, color: 'var(--fg-3)' }}>{desc}</span>
      </div>
      <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 12 }}>{children}</div>
    </div>
  );
}
function Step2(p) {
  const { winMode, setWinMode, winSpan, setWinSpan } = p;
  function toggleHorizon(h) {
    p.setHorizons((hs) => (hs.includes(h) ? hs.filter((x) => x !== h) : [...hs, h].sort((a, b) => a - b)));
  }
  return (
    <div style={{ padding: 20, maxWidth: 980 }}>
      <LightHint tone="info">这些参数篮内<b>强制统一</b>，否则相关性与横向对比无意义。</LightHint>

      <ParamGroup title="组一 · 决定样本与收益定义（考卷）" tone="red" mt={16}
        desc="这些字段改变的是“考卷”——样本空间与收益定义。不同取值的结果完全不可比。">
        <Field label="股票池" required>
          <TSelect value={p.universeId} onChange={p.setUniverseId} w={180}
            options={p.universes.map((u) => ({ v: u.universe_id, l: u.name }))} />
        </Field>
        <Field label="时间窗口" required hint={winMode === 'rolling' ? '窗口随交易日自动右移，支持自动追踪' : '窗口固定不动，适合历史区间研究，不支持自动追踪'}>
          <Segmented options={[{ v: 'rolling', l: '滚动' }, { v: 'fixed', l: '固定区间' }]} value={winMode} onChange={setWinMode} />
          {winMode === 'rolling' ? (
            <span style={{ display: 'inline-flex', gap: 6, marginLeft: 4, alignItems: 'center' }}>
              {[{ v: '365d', l: '365 天' }, { v: '180d', l: '180 天' }, { v: '252td', l: '252 交易日' }, { v: 'custom', l: '自定义' }].map((o) => (
                <button key={o.v} onClick={() => setWinSpan(o.v)} style={chip(winSpan === o.v)}>{o.l}</button>
              ))}
              {winSpan === 'custom' && <TInput value={p.winSpan === 'custom' ? '' : p.winSpan} onChange={setWinSpan} w={80} placeholder="如 500d" />}
            </span>
          ) : (
            <span style={{ display: 'inline-flex', gap: 6, marginLeft: 4, alignItems: 'center' }}>
              <TInput value={p.winStart} onChange={p.setWinStart} w={100} />
              <span style={{ color: 'var(--fg-3)' }}>~</span>
              <TInput value={p.winEnd} onChange={p.setWinEnd} w={100} />
            </span>
          )}
        </Field>
        <Field label="调仓频率" required>
          <Segmented options={[{ v: 'D', l: '日频' }, { v: 'W', l: '周频' }, { v: 'M', l: '月频' }]} value={p.rebalance} onChange={p.setRebalance} />
        </Field>
        <Field label="前向收益周期" required hint="多选 · 至少一个">
          <span style={{ display: 'inline-flex', gap: 6, flexWrap: 'wrap' }}>
            {HORIZON_OPTS.map((h) => (
              <button key={h} onClick={() => toggleHorizon(h)} style={chip(p.horizons.includes(h))}>{h}d</button>
            ))}
          </span>
        </Field>
        <Field label="主周期" required hint="横向对比与优选默认使用的周期">
          <TSelect value={p.primaryHorizon} onChange={(v) => p.setPrimaryHorizon(Number(v))} w={100}
            options={(p.horizons.length ? p.horizons : HORIZON_OPTS).map((h) => ({ v: h, l: `${h} 日` }))} />
        </Field>
        <Field label="收益口径" required>
          <TSelect value={p.returnPrice} onChange={p.setReturnPrice} w={150}
            options={[{ v: 'close', l: '收盘价' }, { v: 'vwap', l: 'VWAP' }, { v: 'open_next', l: '次日开盘' }]} />
        </Field>
        <Field label="IC 类型" required>
          <Segmented options={[{ v: 'rank', l: 'RankIC' }, { v: 'pearson', l: 'Pearson' }]} value={p.icType} onChange={p.setIcType} />
        </Field>
      </ParamGroup>

      <ParamGroup title="组二 · 决定加工方式（答题方式）" tone="yellow" mt={16}
        desc="这些字段改变的是“答题方式”，篮内同样必须统一，否则口径不一致。">
        <Field label="去极值" required>
          <TSelect value={p.winsorize} onChange={p.setWinsorize} w={90} options={[{ v: 'mad', l: 'MAD' }, { v: 'quantile', l: '分位截断' }]} />
          <TInput value={p.winsorizeN} onChange={p.setWinsorizeN} suffix="σ" w={48} type="number" />
        </Field>
        <Field label="标准化" required>
          <Segmented options={[{ v: 'zscore', l: 'Z-Score' }, { v: 'rank', l: '分位' }]} value={p.standardize} onChange={p.setStandardize} />
        </Field>
        <Field label="市值中性化">
          <Toggle on={p.neutralizeSize} onClick={() => p.setNeutralizeSize(!p.neutralizeSize)} />
          {p.neutralizeSize && (
            <span style={{ marginLeft: 4 }}>
              <TSelect value={p.sizeField} onChange={p.setSizeField} w={130} options={[{ v: 'circ_mv', l: '流通市值' }, { v: 'total_mv', l: '总市值' }]} />
            </span>
          )}
        </Field>
        <Field label="行业中性化">
          <Toggle on={p.neutralizeIndustry} onClick={() => p.setNeutralizeIndustry(!p.neutralizeIndustry)} />
          {p.neutralizeIndustry && (
            <span style={{ marginLeft: 4, display: 'inline-flex', gap: 6, alignItems: 'center' }}>
              <TSelect value={p.classificationSystem} onChange={p.setClassificationSystem} w={100} options={[{ v: 'SW', l: '申万' }, { v: 'CITIC', l: '中信' }]} />
              <TSelect value={p.industryLevel} onChange={(v) => p.setIndustryLevel(Number(v))} w={80} options={[{ v: 1, l: '一级' }, { v: 2, l: '二级' }, { v: 3, l: '三级' }]} />
            </span>
          )}
        </Field>
        <Field label="分层数" required>
          <TInput value={p.quantiles} onChange={p.setQuantiles} w={60} type="number" />
          <span style={{ marginLeft: 4 }}>
            <TSelect value={p.layerWeight} onChange={p.setLayerWeight} w={90} options={[{ v: 'equal', l: '等权' }, { v: 'cap', l: '市值加权' }]} />
          </span>
        </Field>
        <Field label="交易成本">
          <TInput value={p.costBps} onChange={p.setCostBps} suffix="bps 双边" w={70} type="number" />
        </Field>
      </ParamGroup>
    </div>
  );
}

// ─── Step 3 · 条目参数 ───────────────────────────────────────────────────────
function Step3({ entries, patchEntry, setEntryParam, dupEntry, delEntry, setScanFor }) {
  // local input for adding a new param key to a row
  const [newKey, setNewKey] = useState({});
  return (
    <div style={{ padding: 20 }}>
      <LightHint tone="info">每个条目可配自身参数（自由 key/value）。数值型参数可用「★扫描」一键展开成多个条目（如 <span className="mono">5,10,20</span> 或 <span className="mono">5..8</span>）。默认标签 = 因子名 + 参数。</LightHint>
      {entries.length === 0 ? (
        <div style={{ marginTop: 14 }}><Empty label="尚无条目" hint="请回到第 ① 步添加因子" /></div>
      ) : (
        <div style={{ marginTop: 14, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11.5 }}>
            <thead>
              <tr style={{ background: 'var(--surface-2)', borderBottom: '1px solid var(--border-strong)' }}>
                {['#', '因子', '自定义标签', '参数 (key=value)', '扫描', '操作'].map((h, i) => (
                  <th key={h} style={{ textAlign: i >= 4 ? 'center' : 'left', padding: '8px 12px', fontSize: 9.5, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.05em', fontFamily: 'var(--font-mono)' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {entries.map((e, i) => {
                const pkeys = Object.keys(e.params || {});
                return (
                  <tr key={e.key} style={{ borderBottom: '1px solid var(--border-faint)', verticalAlign: 'top' }}>
                    <td style={{ padding: '10px 12px', fontFamily: 'var(--font-mono)', color: 'var(--fg-4)' }}>{i + 1}</td>
                    <td style={{ padding: '10px 12px' }}>
                      <span style={{ fontWeight: 500, color: 'var(--fg)' }}>{e.factorName}</span>
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--fg-4)', marginLeft: 6 }}>{e.category}</span>
                    </td>
                    <td style={{ padding: '10px 12px' }}>
                      <input value={e.label} onChange={(ev) => patchEntry(i, { label: ev.target.value })}
                        style={{ width: 150, height: 24, padding: '0 8px', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)' }} />
                    </td>
                    <td style={{ padding: '10px 12px' }}>
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                        {pkeys.length === 0 && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-mute)' }}>无参数</span>}
                        {pkeys.map((k) => (
                          <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
                            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', minWidth: 56 }}>{k}</span>
                            <input value={String(e.params[k])} onChange={(ev) => setEntryParam(i, k, ev.target.value)}
                              style={{ width: 70, height: 22, padding: '0 6px', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)' }} />
                            <button className="btn ghost sm" style={{ width: 20, padding: 0, color: 'var(--err)' }} onClick={() => setEntryParam(i, k, '')}>✕</button>
                          </span>
                        ))}
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, marginTop: 2 }}>
                          <input value={newKey[e.key] || ''} placeholder="新参数名" onChange={(ev) => setNewKey((m) => ({ ...m, [e.key]: ev.target.value }))}
                            onKeyDown={(ev) => { if (ev.key === 'Enter' && (newKey[e.key] || '').trim()) { setEntryParam(i, newKey[e.key].trim(), ''); setEntryParam(i, newKey[e.key].trim(), '0'); setNewKey((m) => ({ ...m, [e.key]: '' })); } }}
                            style={{ width: 88, height: 22, padding: '0 6px', background: 'var(--surface)', border: '1px dashed var(--border-strong)', borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg)' }} />
                          <button className="btn ghost sm" style={{ height: 22 }} disabled={!(newKey[e.key] || '').trim()}
                            onClick={() => { const k = (newKey[e.key] || '').trim(); if (!k) return; setEntryParam(i, k, '0'); setNewKey((m) => ({ ...m, [e.key]: '' })); }}>+ 加参数</button>
                        </span>
                      </div>
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'center' }}>
                      {pkeys.length ? <button className="btn sm" onClick={() => setScanFor(i)}>★ 扫描</button> : <span style={{ color: 'var(--fg-mute)' }}>—</span>}
                    </td>
                    <td style={{ padding: '10px 12px', textAlign: 'center', whiteSpace: 'nowrap' }}>
                      <button className="btn ghost sm" style={{ width: 24, padding: 0 }} title="复制" onClick={() => dupEntry(i)}>⎘</button>
                      <button className="btn ghost sm" style={{ width: 24, padding: 0, color: 'var(--err)' }} title="删除" onClick={() => delEntry(i)}>✕</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function ScanModal({ idx, entry, onClose, onApply }) {
  const pkeys = Object.keys(entry.params || {});
  const [pkey, setPkey] = useState(pkeys[0] || '');
  const [raw, setRaw] = useState('5,10,20');
  const values = parseScan(raw);
  return (
    <Modal width={480} title="★ 参数扫描" onClose={onClose}
      footer={(
        <>
          <span style={{ flex: 1, fontFamily: 'var(--font-mono)', fontSize: 10.5, color: values.length ? 'var(--ok)' : 'var(--err)' }}>将展开为 {values.length} 个条目</span>
          <button className="btn sm" onClick={onClose}>取消</button>
          <button className="btn primary sm" disabled={!values.length || !pkey} onClick={() => onApply(idx, pkey, values)}>确认展开</button>
        </>
      )}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={{ fontSize: 11, color: 'var(--fg-2)' }}>{entry.factorName} — 选一个数值型参数，输入一串值，支持逗号列表 <span className="mono" style={{ color: 'var(--fg)' }}>5,10,20</span> 或范围 <span className="mono" style={{ color: 'var(--fg)' }}>5..8</span>。</div>
        <Field label="扫描参数">
          <TSelect value={pkey} onChange={setPkey} w={160} options={pkeys.length ? pkeys : ['—']} />
        </Field>
        <Field label="取值">
          <input value={raw} onChange={(e) => setRaw(e.target.value)}
            style={{ width: 260, height: 30, padding: '0 10px', background: 'var(--surface-2)', border: '1px solid var(--border-strong)', borderRadius: 4, fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--fg)' }} />
        </Field>
        <div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>预览 · {values.length} 个变体</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {values.map((v) => <span key={v} style={{ ...chip(true), height: 22, cursor: 'default' }}>{entry.factorName}·{pkey}={v}</span>)}
            {!values.length && <span style={{ color: 'var(--err)', fontSize: 11 }}>无法解析</span>}
          </div>
        </div>
      </div>
    </Modal>
  );
}

// ─── Step 4 · 相关性与追踪 ───────────────────────────────────────────────────
function Step4(p) {
  const { corrOn, setCorrOn, threshold, setThreshold, trackOn, setTrackOn, disabled } = p;
  return (
    <div style={{ padding: 20, maxWidth: 720, display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
        <SectionHead title="相关性" right={<Toggle on={corrOn} onClick={() => setCorrOn(!corrOn)} />} />
        <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 14, opacity: corrOn ? 1 : 0.4 }}>
          <div style={{ fontSize: 11, color: 'var(--fg-3)' }}>全部条目算完后自动计算篮内相关性。需 ≥2 个条目才有意义。</div>
          <Field label="方法">
            <Segmented options={[{ v: 'pearson', l: 'Pearson' }, { v: 'spearman', l: 'Spearman' }]} value={p.corrMethod} onChange={p.setCorrMethod} />
          </Field>
          <Field label="冗余判定阈值">
            <input type="range" min="0.5" max="0.95" step="0.01" value={threshold} onChange={(e) => setThreshold(+e.target.value)} style={{ width: 240, accentColor: 'var(--fg)' }} />
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 500, color: 'var(--fg)', width: 44 }}>{Number(threshold).toFixed(2)}</span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>ρ ≥ 此值视为冗余对</span>
          </Field>
          <Field label="最小重叠期数" hint="两条目对齐后样本数低于此值，相关性视为不足">
            <TInput value={p.corrMinPeriods} onChange={p.setCorrMinPeriods} w={70} type="number" />
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>min_periods</span>
          </Field>
        </div>
      </div>

      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden', position: 'relative' }}>
        <SectionHead title="自动追踪" right={<Toggle on={trackOn && !disabled} disabled={disabled} onClick={() => setTrackOn(!trackOn)} />} />
        <div style={{ padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 14, opacity: trackOn && !disabled ? 1 : 0.4 }}>
          <div style={{ fontSize: 11, color: 'var(--fg-3)' }}>每个交易日数据就绪后自动增量计算，产出新快照。</div>
          <Field label="每日触发时刻">
            <TInput value={p.trackHour} onChange={(v) => p.setTrackHour(v)} w={50} type="number" />
            <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--fg-3)' }}>:</span>
            <TInput value={p.trackMinute} onChange={(v) => p.setTrackMinute(v)} w={50} type="number" />
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>时:分</span>
          </Field>
        </div>
        {disabled && (
          <div style={{ position: 'absolute', inset: 0, top: 30, background: 'var(--surface)', opacity: 0.92, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 20 }}>
            <LightHint tone="warn">固定窗口不会随交易日移动，没有可追踪的增量（§5.1）。追踪在此场景下不可用。</LightHint>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Step 5 · 预检与提交 ─────────────────────────────────────────────────────
function Step5({ name, entries, sharedConfig, universes, winMode, corrOn, corrMethod, threshold, trackOn, trackHour, trackMinute, runNow, setRunNow, error }) {
  const universeName = universes.find((u) => u.universe_id === sharedConfig.universe_id)?.name || sharedConfig.universe_id || '—';
  const winText = winMode === 'rolling' ? `滚动 ${sharedConfig.window.span}` : `固定 ${sharedConfig.window.start} ~ ${sharedConfig.window.end}`;
  const blocking = entries.length === 0 || !sharedConfig.universe_id;

  const sharedRows = [
    ['股票池', universeName], ['时间窗口', winText], ['调仓频率', { D: '日频', W: '周频', M: '月频' }[sharedConfig.rebalance]],
    ['前向收益', (sharedConfig.horizons || []).map((h) => `${h}d`).join(', ')], ['主周期', `${sharedConfig.primary_horizon} 日`],
    ['IC 类型', sharedConfig.ic_type === 'rank' ? 'RankIC' : 'Pearson'], ['收益口径', sharedConfig.return_price],
    ['去极值', `${sharedConfig.winsorize} ${sharedConfig.winsorize_n}σ`], ['标准化', sharedConfig.standardize],
    ['市值中性化', sharedConfig.neutralize_size ? `开 · ${sharedConfig.size_field}` : '关'],
    ['行业中性化', sharedConfig.neutralize_industry ? `开 · ${sharedConfig.classification_system} L${sharedConfig.industry_level}` : '关'],
    ['分层', `${sharedConfig.quantiles} 层 · ${sharedConfig.layer_weight === 'equal' ? '等权' : '市值加权'}`],
    ['交易成本', `${sharedConfig.cost_bps} bps`],
    ['相关性', corrOn ? `开 · ${corrMethod} · ρ≥${Number(threshold).toFixed(2)}` : '关'],
    ['自动追踪', trackOn ? `开 · ${String(trackHour).padStart(2, '0')}:${String(trackMinute).padStart(2, '0')}` : '关'],
  ];

  return (
    <div style={{ padding: 20, maxWidth: 920 }}>
      {error && <div style={{ marginBottom: 14 }}><LightHint tone="err">提交失败：{error}</LightHint></div>}

      <div style={{ display: 'flex', gap: 12, marginBottom: 14 }}>
        {[['条目数', entries.length, entries.length ? 'ok' : 'err'], ['共享参数', '统一', 'ok'], ['阻断项', blocking ? 1 : 0, blocking ? 'err' : 'ok']].map(([l, n, t]) => (
          <div key={l} style={{ flex: 1, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, padding: '10px 14px' }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase' }}>{l}</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 22, fontWeight: 500, color: `var(--${t})` }}>{n}</div>
          </div>
        ))}
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1.3fr 1fr', gap: 16 }}>
        {/* entries */}
        <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
          <SectionHead title="篮内条目" meta={`共 ${entries.length} 个 · 名称「${name}」`} />
          {entries.length === 0 ? (
            <div style={{ padding: 24 }}><Empty label="尚无条目" hint="请回到第 ① 步添加因子" /></div>
          ) : entries.map((e, i) => (
            <div key={e.key} style={{ padding: '9px 14px', borderBottom: '1px solid var(--border-faint)', display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-4)', width: 18 }}>{i + 1}</span>
              <div style={{ flex: 1 }}>
                <span style={{ fontSize: 12, color: 'var(--fg)', fontWeight: 500 }}>{e.label}</span>
                <span style={{ marginLeft: 6 }}><ParamChips params={e.params} /></span>
              </div>
              <Badge group="run" value="排队" sm />
            </div>
          ))}
        </div>
        {/* shared config */}
        <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden', alignSelf: 'start' }}>
          <SectionHead title="共享参数（口径）" meta="篮内强制统一" />
          <div style={{ padding: '10px 14px', display: 'flex', flexDirection: 'column', gap: 6 }}>
            {sharedRows.map(([k, v]) => (
              <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 11 }}>
                <span style={{ color: 'var(--fg-3)' }}>{k}</span>
                <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--fg)', textAlign: 'right' }}>{v}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div style={{ marginTop: 16, display: 'flex', alignItems: 'center', gap: 10 }}>
        <span onClick={() => setRunNow(!runNow)} style={{ display: 'inline-flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
          <span style={{ width: 15, height: 15, borderRadius: 3, border: '1px solid var(--border-strong)', background: runNow ? 'var(--fg)' : 'var(--surface)', color: 'var(--bg)', fontSize: 10, lineHeight: '14px', textAlign: 'center' }}>{runNow ? '✓' : ''}</span>
          <span style={{ fontSize: 12, color: 'var(--fg-2)' }}>创建后立即发起首次全量运行</span>
        </span>
      </div>
    </div>
  );
}
