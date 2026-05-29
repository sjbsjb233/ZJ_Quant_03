// "New run" drawer — the canonical RunConfig form (design §10). Used from
// FactorLibrary (preset factor) + FactorDetail + FactorTasks. Submits to
// POST /runs and reports the resulting task_id; closes on success.
import React from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';
import { endpoints } from '../../lib/api';
import { useUniverses } from './shared';

const DEFAULT_CFG = {
  universe_id: 'preset:hs300',
  start: yearAgo(),
  end: today(),
  rebalance: 'W',
  horizons: [1, 5, 10, 20],
  primary_horizon: 5,
  ic_type: 'rank',
  return_price: 'close',
  winsorize: 'mad',
  winsorize_n: 3,
  standardize: 'zscore',
  neutralize_size: true,
  size_field: 'circ_mv',
  neutralize_industry: true,
  classification_system: 'SW',
  industry_level: 1,
  quantiles: 5,
  layer_weight: 'equal',
  compute_long_short: true,
  compute_turnover: true,
  compute_autocorr: false,
  cost_bps: 30,
  lane: 'interactive',
};

function today() { return new Date().toISOString().slice(0, 10); }
function yearAgo() {
  const d = new Date(); d.setFullYear(d.getFullYear() - 1);
  return d.toISOString().slice(0, 10);
}

export default function RunConfigDrawer({ factorId, onClose, onSubmitted, initialUniverseId, initialParams }) {
  const [cfg, setCfg] = React.useState(() => ({ ...DEFAULT_CFG, universe_id: initialUniverseId || DEFAULT_CFG.universe_id }));
  const [params, setParams] = React.useState(initialParams || {});
  const [eta, setEta] = React.useState(null);
  const universes = useUniverses();
  const factor = useQuery({
    queryKey: ['factor', factorId],
    queryFn: () => endpoints.factor(factorId),
    enabled: !!factorId,
  });

  // Preview ETA whenever a relevant field changes (debounced via React).
  React.useEffect(() => {
    let cancelled = false;
    endpoints.previewEta({
      task_type: 'evaluation',
      payload: { run_config: cfg },
      lane: cfg.lane,
    }).then((r) => { if (!cancelled) setEta(r); }).catch(() => {});
    return () => { cancelled = true; };
  }, [cfg.universe_id, cfg.start, cfg.end, cfg.rebalance, cfg.industry_level, cfg.neutralize_industry, cfg.quantiles, cfg.lane]);

  const submit = useMutation({
    mutationFn: () => endpoints.submitRun({
      factor_id: factorId,
      params,
      run_config: cfg,
    }),
    onSuccess: (resp) => {
      onSubmitted?.(resp);
      onClose?.();
    },
  });

  const isLoading = !factor.data || universes.isLoading;
  const paramSchema = factor.data?.versions?.[0]?.param_schema || factor.data?.versions?.find?.((v) => v.enabled)?.param_schema || {};

  return (
    <aside data-testid="newrun-drawer" style={{
      width: 540, flexShrink: 0, background: 'var(--surface)',
      display: 'flex', flexDirection: 'column', minHeight: 0,
      boxShadow: '-8px 0 24px rgba(20,18,14,0.06)',
    }}>
      <DrawerHeader title="新建评估运行" subtitle={factorId} onClose={onClose} />

      <div className="scroll" style={{ flex: 1, minHeight: 0, padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: 18 }}>
        {isLoading ? (
          <div style={{ color: 'var(--fg-3)' }}>加载因子信息…</div>
        ) : (
          <>
            <FormGroup n="1" title="基本">
              <Field label="因子">{factor.data.display_name || factorId} <span style={{ color: 'var(--fg-3)' }}>· {factorId}</span></Field>
              <Field label="启用版本">{factor.data.versions?.find?.((v) => v.enabled)?.version || 'v1'}</Field>
              {Object.entries(paramSchema).length > 0 && (
                <SubGroup title="因子参数">
                  {Object.entries(paramSchema).map(([k, info]) => (
                    <Field key={k} label={k} hint={info.type}>
                      <Input value={params[k] ?? info.default} onChange={(v) => setParams({ ...params, [k]: coerce(v, info.type) })} width={140} />
                    </Field>
                  ))}
                </SubGroup>
              )}
            </FormGroup>

            <FormGroup n="2" title="评估范围">
              <Field label="股票池" required>
                <Select value={cfg.universe_id} onChange={(v) => setCfg({ ...cfg, universe_id: v })} options={
                  (universes.data?.items || []).map((u) => ({ value: u.universe_id, label: u.name + (u.is_preset ? '' : ' (custom)') }))
                } />
              </Field>
              <Field label="区间" required>
                <DateRange a={cfg.start} b={cfg.end} onA={(a) => setCfg({ ...cfg, start: a })} onB={(b) => setCfg({ ...cfg, end: b })} />
              </Field>
              <Field label="调仓频率">
                <Radio v={cfg.rebalance} onChange={(v) => setCfg({ ...cfg, rebalance: v })} opts={[
                  ['D', '日'], ['W', '周'], ['M', '月'],
                ]} />
              </Field>
            </FormGroup>

            <FormGroup n="3" title="收益与 IC">
              <Field label="前向收益周期" hint="多选">
                <Chips opts={[1, 5, 10, 20]} sel={cfg.horizons} onChange={(arr) => setCfg({ ...cfg, horizons: arr.length ? arr : [5] })} />
              </Field>
              <Field label="主周期">
                <Radio v={cfg.primary_horizon} onChange={(v) => setCfg({ ...cfg, primary_horizon: Number(v) })}
                  opts={cfg.horizons.map((h) => [h, `${h}D`])} />
              </Field>
              <Field label="IC 类型">
                <Radio v={cfg.ic_type} onChange={(v) => setCfg({ ...cfg, ic_type: v })} opts={[
                  ['rank', 'Rank'], ['pearson', 'Pearson'], ['both', '两者'],
                ]} />
              </Field>
              <Field label="收益口径">
                <Radio v={cfg.return_price} onChange={(v) => setCfg({ ...cfg, return_price: v })} opts={[
                  ['close', 'CLOSE'], ['vwap', 'VWAP'], ['open_next', 'NEXT OPEN'],
                ]} />
              </Field>
            </FormGroup>

            <FormGroup n="4" title="预处理与中性化">
              <Field label="去极值">
                <Radio v={cfg.winsorize} onChange={(v) => setCfg({ ...cfg, winsorize: v })} opts={[
                  ['none', '关'], ['mad', 'MAD'], ['quantile', '分位'],
                ]} />
                {cfg.winsorize !== 'none' && (
                  <Input value={cfg.winsorize_n} onChange={(v) => setCfg({ ...cfg, winsorize_n: Number(v) })} width={70} suffix={cfg.winsorize === 'mad' ? 'σ' : 'q'} />
                )}
              </Field>
              <Field label="标准化"><Toggle on={cfg.standardize === 'zscore'} onClick={() => setCfg({ ...cfg, standardize: cfg.standardize === 'zscore' ? 'none' : 'zscore' })} /></Field>
              <Field label="市值中性化"><Toggle on={cfg.neutralize_size} onClick={() => setCfg({ ...cfg, neutralize_size: !cfg.neutralize_size })} /></Field>
              {cfg.neutralize_size && (
                <Field label="市值口径">
                  <Radio v={cfg.size_field} onChange={(v) => setCfg({ ...cfg, size_field: v })} opts={[
                    ['circ_mv', '流通市值'], ['total_mv', '总市值'],
                  ]} />
                </Field>
              )}
              <Field label="行业中性化"><Toggle on={cfg.neutralize_industry} onClick={() => setCfg({ ...cfg, neutralize_industry: !cfg.neutralize_industry })} /></Field>
              {cfg.neutralize_industry && (
                <>
                  <Field label="行业体系">
                    <Radio v={cfg.classification_system} onChange={(v) => setCfg({ ...cfg, classification_system: v })} opts={[
                      ['SW', '申万'], ['CITIC', '中信'],
                    ]} />
                  </Field>
                  <Field label="行业级别">
                    <Radio v={String(cfg.industry_level)} onChange={(v) => setCfg({ ...cfg, industry_level: Number(v) })} opts={[
                      ['1', '一级'], ['2', '二级'], ['3', '三级'],
                    ]} />
                  </Field>
                </>
              )}
            </FormGroup>

            <FormGroup n="5" title="分层与统计">
              <Field label="分层数">
                <Input value={cfg.quantiles} onChange={(v) => setCfg({ ...cfg, quantiles: Number(v) || 5 })} width={80} />
              </Field>
              <Field label="组内加权">
                <Radio v={cfg.layer_weight} onChange={(v) => setCfg({ ...cfg, layer_weight: v })} opts={[
                  ['equal', '等权'], ['cap', '市值加权'],
                ]} />
              </Field>
              <Field label="算多空"><Toggle on={cfg.compute_long_short} onClick={() => setCfg({ ...cfg, compute_long_short: !cfg.compute_long_short })} /></Field>
              <Field label="算换手"><Toggle on={cfg.compute_turnover} onClick={() => setCfg({ ...cfg, compute_turnover: !cfg.compute_turnover })} /></Field>
              <Field label="算自相关"><Toggle on={cfg.compute_autocorr} onClick={() => setCfg({ ...cfg, compute_autocorr: !cfg.compute_autocorr })} /></Field>
              <Field label="单边成本">
                <Input value={cfg.cost_bps} onChange={(v) => setCfg({ ...cfg, cost_bps: Number(v) || 30 })} width={70} suffix="bps" />
              </Field>
            </FormGroup>

            <FormGroup n="6" title="优先级">
              <Field label="任务车道" hint="interactive=高优先">
                <Radio v={cfg.lane} onChange={(v) => setCfg({ ...cfg, lane: v })} opts={[
                  ['interactive', 'interactive'], ['routine', 'routine'],
                ]} />
              </Field>
            </FormGroup>

            {submit.isError && (
              <div className="chip err" style={{ height: 'auto', padding: '6px 10px' }}>
                {submit.error?.apiError?.message || '提交失败'}
              </div>
            )}
          </>
        )}
      </div>

      <footer style={{
        padding: '10px 16px', borderTop: '1px solid var(--border)', background: 'var(--surface-2)',
        display: 'flex', alignItems: 'center', gap: 8,
      }}>
        <span data-testid="newrun-eta-preview" style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>
          {eta
            ? <>预估耗时 ~<span style={{ color: 'var(--fg)' }}>{Math.round(eta.est_duration_sec)}s</span> · 排在 <span style={{ color: 'var(--fg)' }}>{cfg.lane}</span> 第 <span style={{ color: 'var(--fg)' }}>{eta.queue_position}</span> 位</>
            : '估算中…'}
        </span>
        <div style={{ flex: 1 }} />
        <button className="btn ghost sm" onClick={onClose}>取消</button>
        <button className="btn sm" data-testid="newrun-submit" disabled={submit.isLoading} onClick={() => submit.mutate()}
          style={{ background: 'var(--fg)', color: 'var(--bg)', borderColor: 'var(--fg)' }}>
          {submit.isLoading ? '提交中…' : '提交 → 进队列'}
        </button>
      </footer>
    </aside>
  );
}

function coerce(v, type) {
  if (type === 'int') return parseInt(v, 10) || 0;
  if (type === 'float') return parseFloat(v) || 0;
  if (type === 'bool') return v === true || v === 'true' || v === 1 || v === '1';
  return v;
}

export function DrawerHeader({ title, subtitle, onClose }) {
  return (
    <header style={{
      padding: '10px 16px', background: 'var(--surface-2)', borderBottom: '1px solid var(--border)',
      display: 'flex', alignItems: 'center', gap: 8,
    }}>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>抽屉</span>
      <span style={{ color: 'var(--fg-mute)' }}>│</span>
      <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--fg)' }}>{title}</span>
      {subtitle && <span style={{ color: 'var(--fg-3)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>· {subtitle}</span>}
      <div style={{ flex: 1 }} />
      <button className="btn ghost sm" onClick={onClose} style={{ width: 22, padding: 0 }}>✕</button>
    </header>
  );
}

function FormGroup({ n, title, children }) {
  return (
    <div>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8, paddingBottom: 6, marginBottom: 8,
        borderBottom: '1px solid var(--border)',
      }}>
        <span style={{
          width: 18, height: 18, borderRadius: 3, background: 'var(--surface-3)', border: '1px solid var(--border)',
          display: 'grid', placeItems: 'center', fontFamily: 'var(--font-mono)', fontSize: 10, fontWeight: 500, color: 'var(--fg-2)',
        }}>{n}</span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 500, color: 'var(--fg)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{title}</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>{children}</div>
    </div>
  );
}

function SubGroup({ title, children }) {
  return (
    <div style={{ padding: '8px 10px', background: 'var(--surface-2)', border: '1px solid var(--border-faint)', borderRadius: 4, marginTop: 4 }}>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', marginBottom: 6, letterSpacing: '0.04em' }}>{title}</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>{children}</div>
    </div>
  );
}

function Field({ label, hint, required, children }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '110px 1fr', gap: 12, alignItems: 'center', minHeight: 22 }}>
      <span style={{ fontSize: 11, color: 'var(--fg-2)', display: 'flex', alignItems: 'center', gap: 4 }}>
        {label}
        {required && <span style={{ color: 'var(--err)' }}>*</span>}
        {hint && <span style={{ color: 'var(--fg-4)', fontSize: 10, fontFamily: 'var(--font-mono)' }}>({hint})</span>}
      </span>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>{children}</div>
    </div>
  );
}

function Input({ value, onChange, width = 100, suffix }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <input value={value ?? ''} onChange={(e) => onChange?.(e.target.value)} style={{
        width, height: 22, padding: '0 7px', background: 'var(--surface-2)', border: '1px solid var(--border)',
        borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)',
      }} />
      {suffix && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>{suffix}</span>}
    </span>
  );
}

function Select({ value, onChange, options }) {
  return (
    <select value={value ?? ''} onChange={(e) => onChange?.(e.target.value)} style={{
      flex: 1, minWidth: 0, height: 22, padding: '0 7px', background: 'var(--surface-2)',
      border: '1px solid var(--border)', borderRadius: 3, fontSize: 11,
      fontFamily: 'var(--font-mono)', color: 'var(--fg)',
    }}>
      {options.map((o) => {
        const v = typeof o === 'string' ? o : o.value;
        const l = typeof o === 'string' ? o : o.label;
        return <option key={v} value={v}>{l}</option>;
      })}
    </select>
  );
}

function Toggle({ on, onClick }) {
  return (
    <span onClick={onClick} style={{
      width: 28, height: 14, borderRadius: 7, cursor: 'pointer',
      background: on ? 'var(--ok)' : 'var(--surface-3)',
      border: '1px solid', borderColor: on ? 'var(--ok)' : 'var(--border)',
      position: 'relative', display: 'inline-block',
    }}>
      <span style={{ position: 'absolute', top: 1, [on ? 'right' : 'left']: 1, width: 10, height: 10, borderRadius: '50%', background: '#fff' }} />
    </span>
  );
}

function Radio({ v, onChange, opts }) {
  return (
    <div style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 3, overflow: 'hidden' }}>
      {opts.map(([val, label]) => (
        <span key={val} onClick={() => onChange?.(val)} style={{
          padding: '3px 9px', fontFamily: 'var(--font-mono)', fontSize: 10.5,
          background: String(val) === String(v) ? 'var(--fg)' : 'var(--surface)',
          color: String(val) === String(v) ? 'var(--bg)' : 'var(--fg-2)',
          cursor: 'pointer', borderRight: val !== opts[opts.length - 1][0] ? '1px solid var(--border)' : 'none',
        }}>{label}</span>
      ))}
    </div>
  );
}

function Chips({ opts, sel, onChange }) {
  const toggle = (o) => {
    const next = sel.includes(o) ? sel.filter((x) => x !== o) : [...sel, o].sort((a, b) => a - b);
    onChange?.(next);
  };
  return (
    <div style={{ display: 'inline-flex', gap: 4, flexWrap: 'wrap' }}>
      {opts.map((o) => {
        const on = sel.includes(o);
        return (
          <span key={o} onClick={() => toggle(o)} style={{
            padding: '2px 8px', background: on ? 'var(--info-bg)' : 'var(--surface-2)',
            color: on ? 'var(--info)' : 'var(--fg-3)',
            border: '1px solid', borderColor: on ? 'var(--info)' : 'var(--border)',
            borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 10.5, cursor: 'pointer',
          }}>{o}D</span>
        );
      })}
    </div>
  );
}

function DateRange({ a, b, onA, onB }) {
  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <input type="date" value={a} onChange={(e) => onA?.(e.target.value)} style={inputStyle} />
      <span style={{ color: 'var(--fg-3)' }}>→</span>
      <input type="date" value={b} onChange={(e) => onB?.(e.target.value)} style={inputStyle} />
    </div>
  );
}

const inputStyle = {
  width: 130, height: 22, padding: '0 7px', background: 'var(--surface-2)',
  border: '1px solid var(--border)', borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)',
};
