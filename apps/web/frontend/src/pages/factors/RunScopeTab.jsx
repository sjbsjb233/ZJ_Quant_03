// 运行透视 / Run Trace (RunScope) — the factor-detail tab that turns a run's
// {run_id}.trace.json into a forensic, frame-by-frame view of how the factor
// was computed (设计 §1 五区块):
//   ① 运行摘要 + 阶段流水线   ② 前置检查清单(含通过项)   ③ 股票池漏斗
//   ④ 逐调仓日总表   ⑤ 单日钻取面板
// Page-states are derived from real run data (附录 A.2.4): the *run* carries
// status / degradation_level / degradation.events (GET /runs/{id}); the *trace*
// (GET /runs/{id}/trace, 契约 §2.7) carries the process data. No mock switcher.
import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { endpoints } from '../../lib/api';
import { TPanel } from '../../components/shell/panels';
import { Loading } from '../../components/ui/primitives';
import { Distribution, FunnelBar } from './charts';
import { fmtNum } from './shared';

// ── canonical 6-stage pipeline ───────────────────────────────────────────────
const STAGE_ORDER = ['load', 'preflight', 'universe_resolve', 'compute', 'write', 'eval'];
const STAGE_LABEL = {
  load: '加载', preflight: '前置检查', universe_resolve: '股票池',
  compute: '计算', write: '写盘', eval: '汇总',
};
const STAGE_SYM = {
  ok: { sym: '✓', tone: 'ok', l: '完成' },
  degrade: { sym: '▲', tone: 'warn', l: '降级完成' },
  blocked: { sym: '⊘', tone: 'warn', l: '被阻断' },
  error: { sym: '✕', tone: 'err', l: '失败' },
  skipped: { sym: '○', tone: 'neutral', l: '跳过' },
  pending: { sym: '·', tone: 'mute', l: '未执行' },
};
const CHECK_SYM = {
  pass: { sym: '●', tone: 'ok', l: '通过' },
  degrade: { sym: '▲', tone: 'warn', l: '降级' },
  block: { sym: '⊘', tone: 'err', l: '阻断' },
  ignore: { sym: '○', tone: 'info', l: '放行' },
  skipped: { sym: '○', tone: 'neutral', l: '跳过' },
};
const FUNNEL_LABEL = {
  raw: '原始全市场', after_flag: '指数成分过滤', after_exclude_st: '剔除 ST',
  after_exclude_suspended: '剔除停牌', after_min_list_days: '次新过滤', final: '最终股票池',
};
const FREQ_LABEL = { D: '日频', W: '周频', M: '月频' };
// DG_UNI_FILTER_NOOP evidence.filter (中文) → funnel step key, for the "未生效" badge.
const NOOP_FILTER_STEP = {
  '剔除 ST': 'after_exclude_st',
  '剔除停牌': 'after_exclude_suspended',
  '次新过滤': 'after_min_list_days',
};

const toneVarOf = (tone) => (tone === 'mute' ? 'var(--fg-mute)' : `var(--${tone})`);

// ── derive helpers ───────────────────────────────────────────────────────────
function buildPipeline(t) {
  const m = {};
  (t.stages || []).forEach((s) => { m[s.stage] = s; });
  return STAGE_ORDER.map((id) => {
    const s = m[id];
    return {
      id, name: STAGE_LABEL[id],
      status: s ? s.status : 'pending',
      dur: s && s.duration_ms != null ? s.duration_ms / 1000 : 0,
      error: s ? s.error : null,
    };
  });
}

function deriveConclusion(status, level, blocked) {
  if (status === 'success') return { label: '成功', tone: 'ok' };
  if (status === 'degraded') {
    return level === 'severe'
      ? { label: '降级 · 严重', tone: 'err' }
      : { label: '降级 · 一般', tone: 'warn' };
  }
  if (status === 'failed') {
    return blocked ? { label: '前置校验未通过', tone: 'warn' } : { label: '失败', tone: 'err' };
  }
  if (status === 'canceled') return { label: '已取消', tone: 'neutral' };
  if (status === 'running') return { label: '运行中', tone: 'info' };
  return { label: status || '—', tone: 'neutral' };
}

function pipelineErrorOf(t, pipeline) {
  const errStage = pipeline.find((p) => p.status === 'error');
  if (errStage) return { stage: errStage.name, msg: errStage.error || '计算阶段出错,运行在写盘前中断。' };
  const pf = (t.stages || []).find((s) => s.stage === 'preflight' && s.status === 'blocked');
  if (pf) {
    const blk = (pf.detail?.checks || []).filter((c) => c.status === 'block');
    const msg = blk.length
      ? `前置检查阻断:${blk.map((c) => `${c.check_id}(${c.reason || c.title || ''})`).join('；')}`
      : (pf.error || '前置检查未通过,运行在进入股票池前被拦下。');
    return { stage: '前置检查', msg };
  }
  return null;
}

function preflightChecks(t) {
  const pf = (t.stages || []).find((s) => s.stage === 'preflight');
  return (pf && pf.detail && pf.detail.checks) || [];
}
function universeDetail(t) {
  const u = (t.stages || []).find((s) => s.stage === 'universe_resolve');
  return u ? (u.detail || {}) : null;
}
function noopStepSet(run) {
  const set = new Set();
  for (const e of (run?.degradation?.events || [])) {
    if (e.code === 'DG_UNI_FILTER_NOOP') {
      const f = e.evidence && e.evidence.filter;
      if (f && NOOP_FILTER_STEP[f]) set.add(NOOP_FILTER_STEP[f]);
    }
  }
  return set;
}
function dayKpis(days) {
  if (!days.length) return { nDays: 0, avgCover: 0, degradeDays: 0, posICPct: 0 };
  const degradeDays = days.filter((d) => (d.degradation_codes || []).length).length;
  const covs = days.map((d) => d.fwd_ret_coverage).filter((v) => v != null);
  const avgCover = covs.length ? Math.round(covs.reduce((a, v) => a + v, 0) / covs.length) : 0;
  const ics = days.map((d) => d.rank_ic).filter((v) => v != null);
  const pos = ics.filter((v) => v > 0).length;
  return { nDays: days.length, avgCover, degradeDays, posICPct: ics.length ? Math.round((pos / ics.length) * 100) : 0 };
}

// ─── block ① : summary row + stage pipeline ──────────────────────────────────
function StagePipeline({ t, conclusion }) {
  const meta = t.meta || {};
  const pipeline = buildPipeline(t);
  const pErr = pipelineErrorOf(t, pipeline);
  const totalDur = pipeline.reduce((a, p) => a + p.dur, 0) || 1;
  const range = meta.start && meta.end
    ? `${meta.start} → ${meta.end}`
    : (meta.rebalance_first ? `${meta.rebalance_first} → ${meta.rebalance_last}` : '—');
  const fields = [
    ['运行号', t.run_id],
    ['因子', meta.factor || '—'],
    ['计算模式', meta.factor_mode === 'pit' ? 'PIT 逐日' : (meta.requires_pit ? 'PIT 逐日' : '向量化')],
    ['股票池', meta.universe_id || '—'],
    ['调仓频率', FREQ_LABEL[meta.rebalance_freq] || meta.rebalance_freq || '—'],
    ['调仓日数', meta.n_rebalance ?? t.n_days ?? '—'],
    ['评估区间', range],
    ['主周期', meta.primary_horizon != null ? `${meta.primary_horizon}D` : '—'],
  ];
  const cSym = conclusion.tone === 'ok' ? '●' : conclusion.tone === 'err' ? '✕' : conclusion.tone === 'warn' ? '▲' : '○';

  return (
    <TPanel title="① 运行摘要 + 阶段流水线" meta={t.run_id} noBorderBottom>
      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'stretch', borderBottom: '1px solid var(--border)' }}>
        {fields.map((f, i) => (
          <div key={i} style={{ padding: '6px 14px', borderRight: '1px solid var(--border-faint)' }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{f[0]}</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--fg)', fontVariantNumeric: 'tabular-nums', marginTop: 1 }}>{f[1]}</div>
          </div>
        ))}
        <div style={{ flex: 1 }} />
        <div style={{ padding: '6px 16px', display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'flex-end' }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>整体结论</div>
          <div style={{ marginTop: 3 }}>
            <span className={`chip ${conclusion.tone}`} style={{ fontSize: 11 }}>{cSym} {conclusion.label}</span>
          </div>
        </div>
      </div>

      <div style={{ padding: '14px 16px 8px' }}>
        <div style={{ display: 'flex', alignItems: 'stretch', gap: 0 }}>
          {pipeline.map((st, i) => {
            const sym = STAGE_SYM[st.status] || STAGE_SYM.pending;
            const toneVar = toneVarOf(sym.tone);
            const isErr = st.status === 'error' || st.status === 'blocked';
            return (
              <React.Fragment key={i}>
                <div title={isErr && pErr ? pErr.msg : undefined} style={{
                  flex: 1, padding: '8px', borderRadius: 4,
                  border: `1px solid ${isErr ? toneVar : 'var(--border)'}`,
                  background: isErr ? (sym.tone === 'err' ? 'var(--err-bg)' : 'var(--warn-bg)') : st.status === 'pending' ? 'transparent' : 'var(--surface-2)',
                  opacity: st.status === 'pending' ? 0.5 : 1, cursor: isErr ? 'help' : 'default',
                  display: 'flex', flexDirection: 'column', gap: 3, minWidth: 0,
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                    <span style={{ color: toneVar, fontSize: 12 }}>{sym.sym}</span>
                    <span style={{ fontSize: 12, fontWeight: 500, color: 'var(--fg)' }}>{st.name}</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: toneVar }}>{sym.l}</span>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', fontVariantNumeric: 'tabular-nums' }}>
                      {st.status === 'pending' ? '—' : st.dur >= 60 ? `${(st.dur / 60).toFixed(1)}m` : `${st.dur.toFixed(1)}s`}
                    </span>
                  </div>
                </div>
                {i < pipeline.length - 1 && (
                  <div style={{ display: 'flex', alignItems: 'center', padding: '0 4px', color: 'var(--fg-mute)', fontSize: 11 }}>→</div>
                )}
              </React.Fragment>
            );
          })}
        </div>

        <div style={{ marginTop: 12 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>耗时占比</span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)' }}>总计 {totalDur >= 60 ? `${(totalDur / 60).toFixed(1)}m` : `${totalDur.toFixed(0)}s`}</span>
          </div>
          <div style={{ display: 'flex', height: 14, borderRadius: 3, overflow: 'hidden', border: '1px solid var(--border)' }}>
            {pipeline.filter((p) => p.dur > 0).map((st, i) => {
              const sym = STAGE_SYM[st.status] || STAGE_SYM.pending;
              const pct = (st.dur / totalDur) * 100;
              const toneVar = toneVarOf(sym.tone);
              return (
                <div key={i} title={`${st.name} · ${pct.toFixed(1)}%`} style={{
                  width: `${pct}%`, background: toneVar, opacity: st.status === 'ok' ? 0.32 : 0.5,
                  borderRight: '1px solid var(--surface)', display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'hidden',
                }}>
                  {pct > 12 && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--fg)', whiteSpace: 'nowrap' }}>{st.name} {pct.toFixed(0)}%</span>}
                </div>
              );
            })}
          </div>
        </div>

        {pErr && (
          <div style={{
            marginTop: 12, padding: '8px 12px', borderRadius: 4,
            background: conclusion.tone === 'err' ? 'var(--err-bg)' : 'var(--warn-bg)',
            border: `1px solid ${conclusion.tone === 'err' ? 'var(--err)' : 'var(--warn)'}`,
            display: 'flex', gap: 8, alignItems: 'flex-start',
          }}>
            <span style={{ color: conclusion.tone === 'err' ? 'var(--err)' : 'var(--warn)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>
              {conclusion.tone === 'err' ? '✕' : '⊘'} {pErr.stage}
            </span>
            <span style={{ fontSize: 11.5, color: 'var(--fg-2)', lineHeight: 1.5, flex: 1 }}>{pErr.msg}</span>
          </div>
        )}
      </div>
    </TPanel>
  );
}

// coverage bar with threshold tick
function CovBar({ cov, threshold, text, result }) {
  const pass = threshold == null ? true : cov >= threshold;
  const tone = result === 'block' ? 'err' : result === 'degrade' ? 'warn' : pass ? 'ok' : 'warn';
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <div style={{ position: 'relative', width: 120, height: 8, background: 'var(--surface-3)', borderRadius: 2 }}>
        <div style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${Math.max(2, cov * 100)}%`, background: `var(--${tone})`, opacity: 0.55, borderRadius: 2 }} />
        {threshold != null && (
          <div title={`阈值 ${(threshold * 100).toFixed(0)}%`} style={{ position: 'absolute', left: `${threshold * 100}%`, top: -2, bottom: -2, width: 1.5, background: 'var(--fg)' }} />
        )}
      </div>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: `var(--${tone})`, fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }}>{text}</span>
    </div>
  );
}

// ─── block ② : prechecks list (含通过项, expandable) ──────────────────────────
function PreflightChecklist({ checks }) {
  const [open, setOpen] = React.useState({});
  const [collapsePassed, setCollapsePassed] = React.useState(false);
  if (!checks.length) {
    return (
      <TPanel title="② 前置检查清单" meta="无前置检查记录" noBorderBottom>
        <div style={{ padding: '12px 16px', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-3)' }}>
          本次运行未记录前置检查(可能在透视功能上线前执行,或采集时前置检查被关闭)。
        </div>
      </TPanel>
    );
  }
  const passed = checks.filter((c) => c.status === 'pass');
  const nonPassNonSkip = checks.filter((c) => c.status !== 'pass' && c.status !== 'skipped');
  const rows = collapsePassed ? checks.filter((c) => c.status !== 'pass') : checks;

  return (
    <TPanel
      title="② 前置检查清单"
      meta={`${checks.length} 项 · ${passed.length} 通过 / ${nonPassNonSkip.length} 非通过`}
      action={
        <button className="btn ghost sm" style={{ height: 18, fontSize: 10, padding: '0 6px' }} onClick={() => setCollapsePassed((v) => !v)}>
          {collapsePassed ? `展开 ${passed.length} 项已通过` : '折叠已通过 ✓'}
        </button>
      }
      noBorderBottom
    >
      {collapsePassed && (
        <div style={{ padding: '6px 14px', borderBottom: '1px solid var(--border-faint)', background: 'var(--ok-bg)', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--ok)' }}>
          ● {passed.length} 项已通过 ✓ <span style={{ color: 'var(--fg-3)' }}>· 点上方“展开”查看全部</span>
        </div>
      )}
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
            {['检查项', '结论', '处置', '覆盖率 vs 阈值', '说明'].map((c, i) => (
              <th key={i} style={{ textAlign: 'left', padding: '6px 12px', fontSize: 9, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.04em', fontWeight: 500 }}>{c}</th>
            ))}
            <th style={{ width: 24 }} />
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => {
            const sym = CHECK_SYM[c.status] || CHECK_SYM.skipped;
            const expandable = (c.status === 'degrade' || c.status === 'block' || c.status === 'ignore') && (c.reason || c.impact);
            const isOpen = open[c.check_id];
            const covText = c.cov_text || (c.coverage != null
              ? `${(c.coverage * 100).toFixed(1)}%${c.threshold != null ? ` ${c.coverage >= c.threshold ? '≥' : '<'} ${(c.threshold * 100).toFixed(0)}%` : ''}`
              : (c.status === 'skipped' ? '本次未启用' : '—'));
            return (
              <React.Fragment key={c.check_id}>
                <tr onClick={() => expandable && setOpen((o) => ({ ...o, [c.check_id]: !o[c.check_id] }))}
                  style={{ borderBottom: '1px solid var(--border-faint)', height: 30, cursor: expandable ? 'pointer' : 'default', background: isOpen ? 'var(--surface-2)' : 'transparent' }}>
                  <td style={{ padding: '0 12px' }}>
                    <span style={{ color: 'var(--fg)', fontWeight: 500 }}>{c.title || c.check_id}</span>
                    <span style={{ color: 'var(--fg-mute)', marginLeft: 6, fontSize: 10 }}>{c.check_id}</span>
                  </td>
                  <td style={{ padding: '0 12px' }}>
                    <span style={{ color: `var(--${sym.tone})` }}>{sym.sym} {sym.l}</span>
                  </td>
                  <td style={{ padding: '0 12px', color: c.status === 'skipped' ? 'var(--fg-mute)' : 'var(--fg-3)' }}>{c.status === 'skipped' ? '—' : (c.policy || '—')}</td>
                  <td style={{ padding: '0 12px', minWidth: 200 }}>
                    {c.coverage != null ? (
                      <CovBar cov={c.coverage} threshold={c.threshold} text={covText} result={c.status} />
                    ) : (
                      <span style={{ color: 'var(--fg-3)' }}>{covText}</span>
                    )}
                  </td>
                  <td style={{ padding: '0 12px', color: 'var(--fg-2)', fontFamily: 'var(--font-sans)' }}>
                    {c.status === 'pass' ? '通过' : (c.reason || '—')}
                  </td>
                  <td style={{ padding: '0 12px', textAlign: 'center', color: 'var(--fg-3)', fontSize: 10 }}>{expandable ? (isOpen ? '▾' : '▸') : ''}</td>
                </tr>
                {expandable && isOpen && (
                  <tr style={{ background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}>
                    <td colSpan={6} style={{ padding: '4px 12px 12px 28px' }}>
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: '8px 24px', maxWidth: 1100 }}>
                        {[['触发原因', c.reason], ['对结果的影响', c.impact], ['建议怎么修', c.suggested_action], ['证据', fmtEvidence(c.evidence)]].map((d, j) => (
                          <div key={j}>
                            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 2 }}>{d[0]}</div>
                            <div style={{ fontSize: 11.5, color: 'var(--fg-2)', lineHeight: 1.5, fontFamily: 'var(--font-sans)' }}>{d[1] || '—'}</div>
                          </div>
                        ))}
                      </div>
                    </td>
                  </tr>
                )}
              </React.Fragment>
            );
          })}
        </tbody>
      </table>
    </TPanel>
  );
}

function fmtEvidence(e) {
  if (e == null) return '—';
  if (typeof e === 'object') { try { return JSON.stringify(e); } catch { return String(e); } }
  return String(e);
}

// ─── block ③ : stock-pool funnel ─────────────────────────────────────────────
function UniverseFunnelBlock({ detail, noop }) {
  const raw = (detail && detail.funnel) || [];
  if (!raw.length) {
    return (
      <TPanel title="③ 股票池漏斗" meta="无漏斗记录" noBorderBottom>
        <div style={{ padding: '12px 16px', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-3)' }}>
          本次运行未记录股票池漏斗(运行可能在解析股票池前中断)。
        </div>
      </TPanel>
    );
  }
  const steps = raw.map((f) => {
    const ineffective = !!f.ineffective || (noop && noop.has(f.step));
    return { ...f, ineffective, note: ineffective ? '过滤已请求但字段缺失/全空 · 实际未剔除' : (FUNNEL_LABEL[f.step] || f.step) };
  });
  // Append a synthetic 最终股票池 row when the contract carries it separately.
  const hasFinal = steps.some((s) => s.step === 'final' || s.terminal);
  if (!hasFinal && detail.final_symbols != null) {
    steps.push({ step: 'final', symbols: detail.final_symbols, rows: detail.final_rows, terminal: true, note: '入池标的数' });
  }
  return (
    <TPanel title="③ 股票池漏斗" meta="从全市场到最终入池 · 逐步过滤" noBorderBottom>
      <FunnelBar steps={steps} labelOf={(s) => FUNNEL_LABEL[s] || s} />
    </TPanel>
  );
}

// ─── ④ header micro-sparkline ─────────────────────────────────────────────────
function Spark({ data, w = 56, h = 16, signed = false }) {
  if (!data || !data.length) return null;
  const vals = data.map((v) => (v == null ? 0 : v));
  const max = Math.max(...vals.map(Math.abs)) || 1;
  const bw = w / vals.length;
  return (
    <svg width={w} height={h} style={{ display: 'block' }}>
      {signed && <line x1="0" y1={h / 2} x2={w} y2={h / 2} stroke="var(--border)" strokeWidth="0.5" />}
      {vals.map((v, i) => {
        const ph = (Math.abs(v) / max) * (signed ? h / 2 - 1 : h - 2);
        const y = signed ? (v >= 0 ? h / 2 - ph : h / 2) : h - ph;
        const c = signed ? (v >= 0 ? 'var(--ok)' : 'var(--err)') : 'var(--fg-3)';
        return <rect key={i} x={i * bw + bw * 0.12} y={y} width={bw * 0.76} height={Math.max(0.5, ph)} fill={c} opacity="0.55" />;
      })}
    </svg>
  );
}

// ─── block ④ : per-rebalance-day master table ────────────────────────────────
function RebalanceTable({ days, kpis, selectedDate, onSelect, truncated }) {
  const sparkIC = days.map((d) => d.rank_ic ?? 0);
  const sparkTurn = days.map((d) => d.turnover ?? 0);
  const sparkCover = days.map((d) => d.fwd_ret_coverage ?? 0);
  const cols = [
    { l: '调仓日', a: 'left' }, { l: '#池', a: 'right' },
    { l: '#因子值', a: 'right' }, { l: '#净值', a: 'right' },
    { l: '覆盖', a: 'right', spark: sparkCover }, { l: 'RankIC', a: 'right', spark: sparkIC, signed: true },
    { l: '换手', a: 'right', spark: sparkTurn }, { l: '削尾', a: 'right' }, { l: '降级', a: 'center' },
  ];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0 }}>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', borderBottom: '1px solid var(--border)', flexShrink: 0, background: 'var(--surface-2)' }}>
        {[
          { l: '调仓日', v: kpis.nDays, t: null },
          { l: '平均覆盖', v: kpis.avgCover, t: null },
          { l: '触发降级天数', v: kpis.degradeDays, t: kpis.degradeDays > 0 ? 'warn' : null },
          { l: 'RankIC>0 占比', v: `${kpis.posICPct}%`, t: kpis.posICPct >= 55 ? 'ok' : null },
        ].map((k, i) => (
          <div key={i} style={{ padding: '8px 12px', borderRight: i < 3 ? '1px solid var(--border-faint)' : 'none' }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{k.l}</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 17, fontWeight: 500, color: k.t === 'warn' ? 'var(--warn)' : k.t === 'ok' ? 'var(--ok)' : 'var(--fg)', fontVariantNumeric: 'tabular-nums', marginTop: 1 }}>{k.v}</div>
          </div>
        ))}
      </div>
      <div className="scroll" style={{ flex: 1, minHeight: 0 }}>
        <table data-testid="runscope-day-table" style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
          <thead style={{ position: 'sticky', top: 0, zIndex: 1 }}>
            <tr style={{ background: 'var(--surface)', borderBottom: '1px solid var(--border-strong)' }}>
              {cols.map((c, i) => (
                <th key={i} style={{ textAlign: c.a, padding: '5px 10px', verticalAlign: 'bottom' }}>
                  <div style={{ fontSize: 9, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.04em', fontWeight: 500 }}>{c.l}</div>
                  {c.spark && (
                    <div style={{ display: 'flex', justifyContent: c.a === 'right' ? 'flex-end' : 'center', marginTop: 2, opacity: 0.8 }}>
                      <Spark data={c.spark} signed={c.signed} />
                    </div>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {days.map((d) => {
              const sel = d.date === selectedDate;
              const factorShort = d.n_raw_values != null && d.n_in_universe != null && d.n_raw_values < d.n_in_universe - 2;
              const netShort = d.n_clean != null && d.n_raw_values != null && d.n_clean < d.n_raw_values - 18;
              const turnHigh = d.turnover != null && d.turnover >= 0.4;
              const clipped = d.winsorize_clipped ? (d.winsorize_clipped.low + d.winsorize_clipped.high) : 0;
              const codes = d.degradation_codes || [];
              const nDeg = codes.length;
              const rankIC = d.rank_ic;
              return (
                <tr key={d.date} onClick={() => onSelect(d.date)}
                  style={{
                    height: 26, cursor: 'pointer',
                    background: sel ? 'var(--info-bg)' : 'transparent',
                    boxShadow: sel ? 'inset 2px 0 0 var(--info)' : 'none',
                    borderBottom: '1px solid var(--border-faint)',
                  }}
                  onMouseEnter={(e) => { if (!sel) e.currentTarget.style.background = 'var(--hover)'; }}
                  onMouseLeave={(e) => { if (!sel) e.currentTarget.style.background = 'transparent'; }}>
                  <td style={{ padding: '0 10px', color: sel ? 'var(--fg)' : 'var(--fg-2)', fontWeight: sel ? 500 : 400 }}>{d.date}</td>
                  <td style={{ padding: '0 10px', textAlign: 'right', color: 'var(--fg-2)', fontVariantNumeric: 'tabular-nums' }}>{d.n_in_universe ?? '—'}</td>
                  <td style={{ padding: '0 10px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: factorShort ? 'var(--warn)' : 'var(--fg-2)' }} title={factorShort ? `明显少于 #池 ${d.n_in_universe}` : undefined}>
                    {d.n_raw_values ?? '—'}{factorShort && <span style={{ marginLeft: 3 }}>▲</span>}
                  </td>
                  <td style={{ padding: '0 10px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: netShort ? 'var(--warn)' : 'var(--fg-2)' }} title={netShort ? `比 #因子值 ${d.n_raw_values} 掉得多` : undefined}>{d.n_clean ?? '—'}</td>
                  <td style={{ padding: '0 10px', textAlign: 'right', color: 'var(--fg-3)', fontVariantNumeric: 'tabular-nums' }}>{d.fwd_ret_coverage ?? '—'}</td>
                  <td style={{ padding: '0 10px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: rankIC == null ? 'var(--fg-mute)' : rankIC > 0 ? 'var(--ok)' : 'var(--err)', fontWeight: 500 }}>
                    {rankIC == null ? '—' : `${rankIC >= 0 ? '+' : ''}${rankIC.toFixed(3)}`}
                  </td>
                  <td style={{ padding: '0 10px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: turnHigh ? 'var(--warn)' : 'var(--fg-2)' }} title={turnHigh ? '换手偏高' : undefined}>
                    {d.turnover == null ? '—' : `${(d.turnover * 100).toFixed(0)}%`}
                  </td>
                  <td style={{ padding: '0 10px', textAlign: 'right', fontVariantNumeric: 'tabular-nums', color: clipped === 0 ? 'var(--fg-mute)' : 'var(--fg-3)' }}>
                    {clipped === 0 ? '—' : clipped}
                  </td>
                  <td title={nDeg ? codes.join(', ') : undefined} style={{ padding: '0 10px', textAlign: 'center' }}>
                    {nDeg === 0
                      ? <span style={{ color: 'var(--fg-mute)' }}>—</span>
                      : <span style={{ display: 'inline-block', minWidth: 16, padding: '0 4px', background: 'var(--warn-bg)', color: 'var(--warn)', borderRadius: 3, fontSize: 10, fontWeight: 500, cursor: 'help' }}>{nDeg}</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {truncated && (
          <div style={{ padding: '8px 12px', borderTop: '1px solid var(--border)', background: 'var(--info-bg)', fontSize: 10.5, color: 'var(--fg-2)', fontFamily: 'var(--font-mono)', display: 'flex', alignItems: 'center', gap: 8 }}>
            <span style={{ color: 'var(--info)' }}>◌ 数据已截断</span>
            <span>共 {truncated.total} 个调仓日，仅展示前 {truncated.shown} 天。</span>
          </div>
        )}
      </div>
    </div>
  );
}

// ⑤ quintile bars
function Quintile({ layers }) {
  if (!layers || !layers.length) return <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-3)' }}>无分层数据</div>;
  const max = Math.max(...layers.map((l) => Math.abs(l.ret)), 0.5);
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10, height: 96 }}>
      {layers.map((l) => {
        const ph = (Math.abs(l.ret) / max) * 38;
        const pos = l.ret >= 0;
        return (
          <div key={l.q} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'flex-end', height: '100%' }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: pos ? 'var(--ok)' : 'var(--err)', fontVariantNumeric: 'tabular-nums', marginBottom: 2 }}>{l.ret > 0 ? '+' : ''}{l.ret.toFixed(1)}%</div>
            <div style={{ position: 'relative', height: 76, display: 'flex', flexDirection: 'column', justifyContent: 'center', width: '100%' }}>
              <div style={{ position: 'absolute', left: 0, right: 0, top: '50%', borderTop: '1px solid var(--border)' }} />
              <div style={{
                width: '64%', margin: '0 auto', height: Math.max(2, ph),
                background: pos ? 'var(--ok)' : 'var(--err)', opacity: 0.55, alignSelf: 'center',
                transform: pos ? `translateY(-${ph / 2}px)` : `translateY(${ph / 2}px)`,
              }} />
            </div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', marginTop: 2 }}>{l.q}</div>
          </div>
        );
      })}
    </div>
  );
}

function CodeList({ label, tone, total, list }) {
  const shown = list || [];
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 4 }}>
        <span className={`chip ${tone}`} style={{ fontSize: 10 }}>{label}</span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>{total} 只</span>
      </div>
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-2)', lineHeight: 1.7, fontVariantNumeric: 'tabular-nums', wordBreak: 'break-all' }}>
        {shown.length ? shown.join('  ') : '—'}
        {total > shown.length && <span style={{ color: 'var(--fg-mute)' }}>{`  … 共 ${total} 只`}</span>}
      </div>
    </div>
  );
}

// ─── block ⑤ : single-day drill-down ─────────────────────────────────────────
function buildSteps(day) {
  const steps = [];
  const wc = day.winsorize_clipped;
  if (day.dist_raw) {
    const s = day.dist_raw;
    steps.push({ key: 'raw', name: '原始因子值', stats: s, ok: true,
      notes: [`μ=${fmtNum(s.mean, 3)}  σ=${fmtNum(s.std, 3)}`, `范围 [${fmtNum(s.min, 2)}, ${fmtNum(s.max, 2)}]`] });
  }
  if (day.dist_after_winsorize) {
    const s = day.dist_after_winsorize;
    steps.push({ key: 'winsor', name: '去极值后', stats: s, ok: true,
      notes: [wc ? `削低 ${wc.low} / 削高 ${wc.high}` : `n=${s.n}`, `max ${fmtNum(s.max, 2)}`] });
  }
  if (day.dist_after_standardize) {
    const s = day.dist_after_standardize;
    const ok = s.mean != null && s.std != null && Math.abs(s.mean) < 0.05 && Math.abs(s.std - 1) < 0.1;
    steps.push({ key: 'std', name: '标准化后', stats: s, ok,
      notes: ok ? [`μ≈${fmtNum(s.mean, 2)}  σ≈${fmtNum(s.std, 2)}`, '符合预期'] : [`μ=${fmtNum(s.mean, 2)}  σ=${fmtNum(s.std, 2)}`, '偏离 μ≈0 σ≈1'] });
  }
  if (day.dist_after_neutralize) {
    const s = day.dist_after_neutralize;
    const dropped = (day.n_before_neutralize != null && day.n_clean != null) ? day.n_before_neutralize - day.n_clean : null;
    steps.push({ key: 'neu', name: '中性化残差', stats: s, ok: true,
      notes: [`残差 μ≈${fmtNum(s.mean, 2)}`, dropped != null ? `剔 ${dropped} 缺数据标的` : `n=${s.n}`] });
  }
  return steps;
}

function DayDrilldown({ day }) {
  if (!day) return <div style={{ padding: 20, fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--fg-3)' }}>选择左侧某个调仓日查看明细</div>;
  const steps = buildSteps(day);
  const layers = (day.layer_returns || []).map((v, i) => ({ q: `Q${i + 1}`, ret: (v == null ? 0 : v) * 100 }));
  const monotone = layers.length > 1 && layers.every((l, k) => k === 0 || l.ret >= layers[k - 1].ret - 0.05);
  const deg = day.degradation_codes || [];
  const ovTags = [
    ['日期', day.date],
    ['#池', day.n_in_universe ?? '—'],
    ['#因子值', day.n_raw_values ?? '—'],
    ['#净值', day.n_clean ?? '—'],
    ['RankIC', day.rank_ic == null ? '—' : `${day.rank_ic >= 0 ? '+' : ''}${day.rank_ic.toFixed(3)}`, day.rank_ic == null ? null : day.rank_ic > 0 ? 'ok' : 'err'],
    ['IC', day.ic == null ? '—' : `${day.ic >= 0 ? '+' : ''}${day.ic.toFixed(3)}`],
    ['换手', day.turnover == null ? '—' : `${(day.turnover * 100).toFixed(0)}%`],
    ['当日降级', deg.length ? deg.join(' · ') : '无', deg.length ? 'warn' : null],
  ];

  return (
    <div className="scroll" style={{ height: '100%', minHeight: 0 }}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 0, borderBottom: '1px solid var(--border)', background: 'var(--surface-2)' }}>
        {ovTags.map((tg, i) => (
          <div key={i} style={{ padding: '7px 12px', borderRight: '1px solid var(--border-faint)', minWidth: 70 }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--fg-3)', textTransform: 'uppercase' }}>{tg[0]}</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 500, fontVariantNumeric: 'tabular-nums', marginTop: 1, color: tg[2] === 'ok' ? 'var(--ok)' : tg[2] === 'err' ? 'var(--err)' : tg[2] === 'warn' ? 'var(--warn)' : 'var(--fg)' }}>{tg[1]}</div>
          </div>
        ))}
      </div>

      <div style={{ padding: '12px 14px', borderBottom: '1px solid var(--border)' }}>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 10 }}>
          预处理四步分布 <span style={{ color: 'var(--fg-mute)', textTransform: 'none' }}>· 原始 → 去极值 → 标准化 → 中性化残差</span>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {steps.map((s, i) => (
            <div key={s.key} style={{ display: 'grid', gridTemplateColumns: '108px 176px 1fr', alignItems: 'center', gap: 10 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-mute)' }}>{['①', '②', '③', '④'][i]}</span>
                <span style={{ fontSize: 11.5, color: 'var(--fg)', fontWeight: 500 }}>{s.name}</span>
              </div>
              <Distribution stats={s.stats} tone={s.ok ? 'info' : 'err'} />
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                {s.notes.map((n, j) => (
                  <span key={j} style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: j === 0 ? 'var(--fg-2)' : 'var(--fg-3)', fontVariantNumeric: 'tabular-nums' }}>{n}</span>
                ))}
                {s.key === 'std' && (
                  <span className={`chip ${s.ok ? 'ok' : 'err'}`} style={{ fontSize: 10 }}>{s.ok ? '✓ 正常' : '✕ 异常'}</span>
                )}
              </div>
            </div>
          ))}
          {!steps.length && <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-3)' }}>该日无分布快照</div>}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', borderBottom: '1px solid var(--border)' }}>
        <div style={{ padding: '12px 14px', borderRight: '1px solid var(--border)' }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 10 }}>分层收益 · {layers.length || 0} 组</div>
          <Quintile layers={layers} />
          {layers.length > 1 && (
            <div style={{ marginTop: 8 }}>
              <span className={`chip ${monotone ? 'ok' : 'warn'}`} style={{ fontSize: 10 }}>{monotone ? '单调递增 ✓' : '非单调 ▲'}</span>
            </div>
          )}
        </div>
        <div style={{ padding: '12px 14px' }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 10 }}>多头 / 空头组抽样</div>
          <CodeList label="多头 Q5" tone="ok" total={day.n_top ?? (day.top_names || []).length} list={day.top_names} />
          <div style={{ height: 8 }} />
          <CodeList label="空头 Q1" tone="err" total={day.n_bottom ?? (day.bottom_names || []).length} list={day.bottom_names} />
        </div>
      </div>

      <div style={{ padding: '10px 14px', fontSize: 10.5, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)', lineHeight: 1.6, display: 'flex', gap: 8 }}>
        <span style={{ color: 'var(--fg-mute)' }}>ⓘ</span>
        <span>部分“全程只记一次”的降级(如缺市值剔除 DG_SIZE_DROPPED)仅标注在首次出现的调仓日,可能波及其后多个调仓日;完整明细以页面顶部「数据降级报告」为准。</span>
      </div>
    </div>
  );
}

// ─── halted / blocked notices ────────────────────────────────────────────────
function HaltedNotice({ isFail, hasFunnel }) {
  return (
    <TPanel title={isFail ? '计算未完成 · 无逐日数据' : '运行已取消 · 无逐日数据'} meta={isFail ? 'compute aborted' : 'cancelled by user'} last>
      <div style={{ padding: '24px 20px', display: 'flex', gap: 14, alignItems: 'flex-start' }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 18, color: isFail ? 'var(--err)' : 'var(--fg-3)' }}>{isFail ? '✕' : '○'}</span>
        <div style={{ maxWidth: 620 }}>
          <div style={{ fontSize: 13, color: 'var(--fg)', marginBottom: 6 }}>{isFail ? '计算阶段出错,运行在写盘前中断。' : '计算阶段被用户主动取消。'}</div>
          <div style={{ fontSize: 11.5, color: 'var(--fg-3)', lineHeight: 1.6 }}>
            ④逐调仓日总表 / ⑤单日钻取面板依赖完整计算结果,本次未产出,因此不展示。上方①阶段流水线已标注中断位置{hasFunnel ? ',③股票池漏斗为中断前已完成的结果' : ''}。
          </div>
        </div>
      </div>
    </TPanel>
  );
}

function BlockedNotice() {
  return (
    <TPanel title="后续阶段未执行" meta="run halted at precheck" last>
      <div style={{ padding: '24px 20px', display: 'flex', gap: 14, alignItems: 'flex-start' }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 18, color: 'var(--warn)' }}>⊘</span>
        <div style={{ maxWidth: 640 }}>
          <div style={{ fontSize: 13, color: 'var(--fg)', marginBottom: 6 }}>运行被前置检查阻断,股票池 / 计算 / 写盘 / 汇总 均未执行。</div>
          <div style={{ fontSize: 11.5, color: 'var(--fg-3)', lineHeight: 1.6 }}>
            这是一次“半截运行”:只有①加载与②前置检查产生了内容(见上)。请按②中阻断项的「建议怎么修」处理后重跑,即可继续到股票池与逐日计算。
          </div>
        </div>
      </div>
    </TPanel>
  );
}

// ─── master-detail wrapper (④ + ⑤) ───────────────────────────────────────────
function MasterDetail({ t }) {
  const days = t.days || [];
  const lastDate = days.length ? days[days.length - 1].date : null;
  const [selectedDate, setSelectedDate] = React.useState(lastDate);
  React.useEffect(() => { setSelectedDate(lastDate); }, [t.run_id, lastDate]);
  const cur = days.find((d) => d.date === selectedDate) || days[days.length - 1];
  const kpis = dayKpis(days);
  const truncated = t.days_truncated ? { shown: days.length, total: t.n_days } : null;

  return (
    <div style={{ display: 'flex', height: 580, minHeight: 0 }}>
      <div style={{ flex: '0 0 46%', minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        <TPanel title="④ 逐调仓日总表" meta="每行一个调仓日 · 点选驱动右侧钻取" style={{ height: '100%' }}>
          <RebalanceTable days={days} kpis={kpis} selectedDate={cur?.date} onSelect={setSelectedDate} truncated={truncated} />
        </TPanel>
      </div>
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        <TPanel title="⑤ 单日钻取面板" meta={cur ? cur.date : ''} last style={{ height: '100%' }}>
          <DayDrilldown day={cur} />
        </TPanel>
      </div>
    </div>
  );
}

// ─── orchestrator ────────────────────────────────────────────────────────────
export default function RunScopeTab({ runId }) {
  const traceQ = useQuery({ queryKey: ['trace', runId], queryFn: () => endpoints.runTrace(runId), enabled: !!runId });
  const runQ = useQuery({ queryKey: ['run', runId], queryFn: () => endpoints.run(runId), enabled: !!runId });

  if (!runId) {
    return <div style={{ padding: 40, textAlign: 'center', color: 'var(--fg-3)', fontFamily: 'var(--font-mono)', fontSize: 13 }}>请先在上方选择一次评估运行</div>;
  }
  if (traceQ.isLoading || runQ.isLoading) return <Loading label="加载运行过程…" />;
  if (traceQ.isError) {
    return <div style={{ padding: 40, textAlign: 'center', color: 'var(--err)', fontFamily: 'var(--font-mono)', fontSize: 13 }}>加载运行过程失败 · {traceQ.error?.apiError?.message || ''}</div>;
  }

  const t = traceQ.data;
  const run = runQ.data || {};
  if (!t || t.available === false) {
    return (
      <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 40 }}>
        <div style={{ textAlign: 'center', maxWidth: 440 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 28, color: 'var(--fg-mute)', marginBottom: 14 }}>∅</div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, color: 'var(--fg)', marginBottom: 8 }}>本次运行没有过程数据</div>
          <div style={{ fontSize: 12, color: 'var(--fg-3)', lineHeight: 1.6 }}>
            {t?.reason || '这次评估在「运行透视」功能上线前执行,或过程采集当时被关闭,因此没有可供逐项核对的过程数据。重跑一次即可获得完整的运行透视。'}
          </div>
        </div>
      </div>
    );
  }

  const status = run.status || t.status;
  const level = run.degradation_level || t.degradation_level || 'none';
  const checks = preflightChecks(t);
  const uniDetail = universeDetail(t);
  const noop = noopStepSet(run);
  const blocked = (t.stages || []).some((s) => s.stage === 'preflight' && s.status === 'blocked')
    || (status === 'failed' && run.error === 'preflight_blocked');
  const hasDays = (t.days || []).length > 0;
  const halted = !blocked && !hasDays && (status === 'failed' || status === 'canceled');
  const conclusion = deriveConclusion(status, level, blocked);
  const showDegradeBanner = status === 'degraded';

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      {showDegradeBanner && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '7px 16px', background: 'var(--warn-bg)', borderBottom: '1px solid var(--border)', fontSize: 11.5, color: 'var(--fg-2)' }}>
          <span className="chip warn" style={{ fontSize: 10 }}>▲ 数据降级报告</span>
          <span>本次运行存在数据降级,完整明细见本页顶部「数据降级报告」卡片(横跨所有 Tab)。逐日归因见下方④/⑤。</span>
        </div>
      )}
      <StagePipeline t={t} conclusion={conclusion} />
      <PreflightChecklist checks={checks} />
      {blocked ? (
        <BlockedNotice />
      ) : halted ? (
        <>
          {uniDetail && <UniverseFunnelBlock detail={uniDetail} noop={noop} />}
          <HaltedNotice isFail={status === 'failed'} hasFunnel={!!uniDetail} />
        </>
      ) : (
        <>
          <UniverseFunnelBlock detail={uniDetail} noop={noop} />
          <MasterDetail t={t} />
        </>
      )}
    </div>
  );
}
