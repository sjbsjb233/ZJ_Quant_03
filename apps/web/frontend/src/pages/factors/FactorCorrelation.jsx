// /factors/correlation — multi-factor decorrelation analysis.
import React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { endpoints } from '../../lib/api';
import { TerminalShell, TPanel } from '../../components/shell';
import { Empty, Loading } from '../../components/ui';
import { Heatmap } from './charts';
import { fmtNum, useUniverses, MiniError } from './shared';

export default function FactorCorrelation() {
  const qc = useQueryClient();
  const [selected, setSelected] = React.useState([]);
  const [universeId, setUniverseId] = React.useState('preset:hs300');
  const [method, setMethod] = React.useState('ic_series');
  const [threshold, setThreshold] = React.useState(0.7);
  const [neutSize, setNeutSize] = React.useState(true);
  const [neutInd, setNeutInd] = React.useState(true);
  const [classification, setClassification] = React.useState('SW');
  const [level, setLevel] = React.useState(1);
  const [taskId, setTaskId] = React.useState(null);

  const factorsQ = useQuery({
    queryKey: ['factors.search'],
    queryFn: () => endpoints.factors({ page_size: 200 }),
  });
  const universes = useUniverses();

  const submit = useMutation({
    mutationFn: () => endpoints.submitCorrelation({
      factor_ids: selected,
      run_config: {
        universe_id: universeId,
        neutralize_size: neutSize,
        neutralize_industry: neutInd,
        classification_system: classification,
        industry_level: level,
      },
      method,
      threshold,
    }),
    onSuccess: (resp) => setTaskId(resp.task_id),
  });

  const resultQ = useQuery({
    queryKey: ['correlation', taskId],
    queryFn: () => endpoints.correlationResult(taskId),
    enabled: !!taskId,
    refetchInterval: (data) => (data?.status === 'success' ? false : 2500),
  });
  const r = resultQ.data?.status === 'success' ? resultQ.data : null;

  const toggle = (id) => setSelected((s) => s.includes(id) ? s.filter((x) => x !== id) : [...s, id]);

  return (
    <TerminalShell
      module="factor" current="correlation"
      path={['factor_mgmt/', 'correlation']}
      title="因子相关性"
      subtitle={`multi-factor decorrelation · ${selected.length} 因子 · ${method === 'ic_series' ? 'IC 序列相关' : '因子值截面相关'}`}
      actions={
        <>
          {taskId && r && (
            <button className="btn ghost sm"
              onClick={() => window.open(`/api/v1/factors/correlation/${taskId}?format=csv`, '_blank')}>导出 CSV</button>
          )}
          <button className="btn sm" data-testid="corr-submit" disabled={selected.length < 2 || submit.isLoading}
            onClick={() => submit.mutate()}
            style={{ background: 'var(--fg)', color: 'var(--bg)', borderColor: 'var(--fg)' }}>
            {submit.isLoading ? '提交中…' : '+ 新建分析 → 队列'}
          </button>
        </>
      }
    >
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        {/* CONFIG */}
        <div style={{ flexShrink: 0, borderBottom: '1px solid var(--border-strong)' }}>
          <TPanel title="ANALYSIS CONFIG · 提交后会进任务队列" meta={taskId ? `task ${taskId}` : '尚未提交'} last>
            <div style={{ padding: '14px 18px', display: 'grid', gridTemplateColumns: '1.4fr 1fr 1fr 1fr', gap: 18 }}>
              {/* selected factors */}
              <div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', marginBottom: 6 }}>选定因子 · {selected.length}</div>
                <div data-testid="corr-factor-chips" style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginBottom: 8 }}>
                  {selected.map((fid) => (
                    <span key={fid} className="chip info" style={{ height: 18, fontSize: 10, padding: '0 6px' }}>
                      {fid}
                      <span onClick={() => toggle(fid)} style={{ color: 'var(--fg-3)', marginLeft: 3, cursor: 'pointer' }}>✕</span>
                    </span>
                  ))}
                </div>
                <details>
                  <summary data-testid="corr-add-factor" style={{ color: 'var(--info)', fontFamily: 'var(--font-mono)', fontSize: 10.5, cursor: 'pointer' }}>+ 添加</summary>
                  <div style={{ marginTop: 6, maxHeight: 220, overflow: 'auto', border: '1px solid var(--border)', borderRadius: 3 }}>
                    {factorsQ.isError && <MiniError error={factorsQ.error} onRetry={() => factorsQ.refetch()} label="加载因子列表失败" />}
                    {(factorsQ.data?.items || []).map((f) => (
                      <div key={f.factor_id} onClick={() => toggle(f.factor_id)} style={{
                        padding: '4px 10px', display: 'grid', gridTemplateColumns: '20px 130px 1fr',
                        gap: 6, fontFamily: 'var(--font-mono)', fontSize: 11, cursor: 'pointer',
                        background: selected.includes(f.factor_id) ? 'var(--ok-bg)' : 'transparent',
                      }}>
                        <span>{selected.includes(f.factor_id) ? '✓' : '○'}</span>
                        <span style={{ color: 'var(--fg)', fontWeight: 500 }}>{f.factor_id}</span>
                        <span style={{ color: 'var(--fg-3)' }}>{f.display_name}</span>
                      </div>
                    ))}
                  </div>
                </details>
              </div>

              <div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', marginBottom: 6 }}>口径</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  <Row k="股票池">
                    <select value={universeId} onChange={(e) => setUniverseId(e.target.value)} style={input}>
                      {(universes.data?.items || []).map((u) => <option key={u.universe_id} value={u.universe_id}>{u.name}</option>)}
                    </select>
                  </Row>
                </div>
              </div>

              <div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', marginBottom: 6 }}>中性化</div>
                <Row k="市值中性化">
                  <Toggle on={neutSize} onClick={() => setNeutSize(!neutSize)} />
                </Row>
                <Row k="行业中性化">
                  <Toggle on={neutInd} onClick={() => setNeutInd(!neutInd)} />
                </Row>
                {neutInd && (
                  <>
                    <Row k="行业体系">
                      <select value={classification} onChange={(e) => setClassification(e.target.value)} style={input}>
                        <option value="SW">申万</option><option value="CITIC">中信</option>
                      </select>
                    </Row>
                    <Row k="行业级别">
                      <select value={level} onChange={(e) => setLevel(Number(e.target.value))} style={input}>
                        <option value={1}>一级</option><option value={2}>二级</option><option value={3}>三级</option>
                      </select>
                    </Row>
                  </>
                )}
              </div>

              <div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', marginBottom: 6 }}>相关性类型</div>
                <div data-testid="corr-type-toggle" style={{ display: 'inline-flex', border: '1px solid var(--border)', borderRadius: 3, overflow: 'hidden', marginBottom: 12 }}>
                  {[['ic_series', 'IC 序列相关'], ['factor_value', '因子值截面相关']].map(([v, l]) => (
                    <span key={v} data-testid={`corr-type-${v}`} onClick={() => setMethod(v)} style={{
                      padding: '4px 10px', fontFamily: 'var(--font-mono)', fontSize: 10.5,
                      background: method === v ? 'var(--fg)' : 'var(--surface)',
                      color: method === v ? 'var(--bg)' : 'var(--fg-2)',
                      borderRight: v !== 'factor_value' ? '1px solid var(--border)' : 'none', cursor: 'pointer',
                    }}>{l}</span>
                  ))}
                </div>
                <Row k="阈值 thr">
                  <input data-testid="corr-threshold" type="number" step="0.05" value={threshold} onChange={(e) => setThreshold(Number(e.target.value) || 0.7)} style={{ ...input, width: 80 }} />
                </Row>
                {submit.isError && <div className="chip err" style={{ marginTop: 6, padding: '4px 8px', height: 'auto' }}>{submit.error?.apiError?.message}</div>}
                {taskId && resultQ.data?.status !== 'success' && (
                  <div style={{ marginTop: 6, fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--info)' }}>
                    <span className="dot running" /> task {taskId.slice(-8)} · {resultQ.data?.status || 'queued'} · 进度 {resultQ.data?.progress_current ?? 0}/{resultQ.data?.progress_total ?? '?'}
                  </div>
                )}
              </div>
            </div>
          </TPanel>
        </div>

        {/* RESULTS */}
        <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
          <div style={{ flex: 1.4, minWidth: 0, borderRight: '1px solid var(--border)' }}>
            <TPanel title="CORRELATION MATRIX" meta={method === 'ic_series' ? 'IC 序列' : '因子值截面'} last
              action={r && (
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>
                  thr <span style={{ color: 'var(--fg)' }}>|r| ≥ {threshold.toFixed(2)}</span>
                </span>
              )}>
              {!taskId && <Empty label="选定因子并提交计算 → 队列后,这里展示热力图" />}
              {taskId && !r && <Loading label="任务运行中,自动刷新结果…" />}
              {r && (
                <div data-testid="corr-heatmap" style={{ padding: '14px 18px', display: 'grid', placeItems: 'center', minHeight: 0 }}>
                  <Heatmap matrix={r.matrix} labels={r.labels} width={680} height={620} threshold={threshold} />
                </div>
              )}
            </TPanel>
          </div>

          <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
            <TPanel title="冗余候选 · 建议保留谁" meta={r?.high_pairs ? `${r.high_pairs.length} 对` : '—'}>
              {r?.high_pairs?.length ? r.high_pairs.map((p, i) => (
                <div key={i} data-testid={`corr-pair-${p.a}-${p.b}`} style={{ padding: '12px 16px', borderBottom: '1px solid var(--border-faint)' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--err)', fontWeight: 500 }}>|r| = {Math.abs(p.r).toFixed(2)}</span>
                    <span className="chip err" style={{ height: 16, fontSize: 9.5 }}>冗余</span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'stretch', gap: 8 }}>
                    <div style={{ flex: 1, padding: '8px 10px', border: '1px solid var(--ok)', borderRadius: 4, background: 'var(--ok-bg)' }}>
                      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--ok)', textTransform: 'uppercase' }}>保留</div>
                      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 500, color: 'var(--fg)', marginTop: 2 }}>{p.keep}</div>
                      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>{p.why}</div>
                    </div>
                    <div style={{ display: 'grid', placeItems: 'center', color: 'var(--fg-3)', fontFamily: 'var(--font-mono)', fontSize: 14 }}>vs</div>
                    <div style={{ flex: 1, padding: '8px 10px', border: '1px solid var(--border)', borderRadius: 4, background: 'var(--surface-2)', opacity: 0.7 }}>
                      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase' }}>移除候选</div>
                      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 500, color: 'var(--fg-2)', marginTop: 2, textDecoration: 'line-through' }}>{p.drop}</div>
                    </div>
                  </div>
                  <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
                    <div style={{ flex: 1 }} />
                    <button className="btn sm" data-testid={`corr-pair-${p.a}-${p.b}-disable`} onClick={() => {
                      endpoints.patchFactor(p.drop, { status: 'disabled' }).then(() => qc.invalidateQueries({ queryKey: ['factors.search'] }));
                    }} style={{ fontSize: 10, background: 'var(--err)', color: '#fff', borderColor: 'var(--err)' }}>停用 {p.drop}</button>
                  </div>
                </div>
              )) : <div style={{ padding: 18, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>暂无 |r| ≥ {threshold} 的冗余对</div>}
            </TPanel>

            <TPanel title="DECORRELATION 概要" last>
              {r ? (
                <div style={{ padding: '12px 16px', fontFamily: 'var(--font-mono)', fontSize: 11 }}>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                    {[
                      ['因子对总数', r.summary?.pairs_total ?? 0],
                      ['|r| ≥ 0.7 (冗余)', r.summary?.ge_07 ?? 0, 'err'],
                      ['|r| ≥ 0.5 (强)', r.summary?.ge_05 ?? 0, 'warn'],
                      ['|r| ≥ 0.3 (中)', r.summary?.ge_03 ?? 0],
                      ['平均 |r|', fmtNum(r.summary?.mean_abs_r, 2)],
                      ['最大 |r|', fmtNum(r.summary?.max_abs_r, 2), 'err'],
                    ].map(([k, v, tone], i) => (
                      <div key={i} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0', borderBottom: '1px solid var(--border-faint)' }}>
                        <span style={{ color: 'var(--fg-3)' }}>{k}</span>
                        <span style={{ color: tone === 'err' ? 'var(--err)' : tone === 'warn' ? 'var(--warn)' : 'var(--fg)', fontWeight: 500 }}>{v}</span>
                      </div>
                    ))}
                  </div>
                  <div style={{ marginTop: 12, padding: '8px 10px', background: 'var(--info-bg)', borderRadius: 3, color: 'var(--fg-2)', lineHeight: 1.55, fontSize: 10.5 }}>
                    <span style={{ color: 'var(--info)', fontWeight: 500 }}>ⓘ READ</span> · 一般认为 |r| ≥ 0.7 的两个因子在说同一件事,保留表现更好的那个;0.5 ≤ |r| {'<'} 0.7 可考虑正交化。
                  </div>
                </div>
              ) : <div style={{ padding: 14, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)', fontSize: 10.5 }}>结果就绪后展示</div>}
            </TPanel>
          </div>
        </div>
      </div>
    </TerminalShell>
  );
}

const input = {
  height: 22, padding: '0 7px', background: 'var(--surface-2)', border: '1px solid var(--border)',
  borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)', flex: 1, minWidth: 0,
};

function Row({ k, children }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8, fontSize: 11, fontFamily: 'var(--font-mono)', padding: '3px 0' }}>
      <span style={{ color: 'var(--fg-3)' }}>{k}</span>
      <div style={{ display: 'flex', gap: 4, alignItems: 'center', minWidth: 0 }}>{children}</div>
    </div>
  );
}

function Toggle({ on, onClick }) {
  return (
    <span onClick={onClick} style={{
      width: 28, height: 14, borderRadius: 7, cursor: 'pointer',
      background: on ? 'var(--ok)' : 'var(--surface-3)',
      border: `1px solid ${on ? 'var(--ok)' : 'var(--border)'}`,
      position: 'relative', display: 'inline-block',
    }}>
      <span style={{ position: 'absolute', top: 1, [on ? 'right' : 'left']: 1, width: 10, height: 10, borderRadius: '50%', background: '#fff' }} />
    </span>
  );
}
