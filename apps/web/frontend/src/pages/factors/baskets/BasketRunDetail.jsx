// ─────────────────────────────────────────────────────────────────────────────
// BasketRunDetail.jsx — §7 篮子运行详情页 (单次大任务). Aggregate progress +
// child subtask list (corr subtask pinned last + "等待全部评估完成"). Polls while
// queued/running. Ported from the Claude Design handoff (fl-run-detail.jsx).
// ─────────────────────────────────────────────────────────────────────────────
import React from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { endpoints } from '../../../lib/api';
import { TerminalShell } from '../../../components/shell';
import { Loading, ErrorState, Empty } from '../../../components/ui';
import { Badge, SectionHead, degradeValue } from './kit';

export default function BasketRunDetail() {
  const { id, rid } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const runQ = useQuery({
    queryKey: ['basket.run', id, rid],
    queryFn: () => endpoints.basketRun(id, rid),
    refetchInterval: (q) => (['queued', 'running'].includes(q.state.data?.status_raw) ? 2000 : false),
  });

  const cancelMut = useMutation({
    mutationFn: () => endpoints.cancelBasketRun(id, rid),
    onSuccess: () => { runQ.refetch(); qc.invalidateQueries({ queryKey: ['basket.runs', id] }); },
  });

  const run = runQ.data;
  const live = ['queued', 'running'].includes(run?.status_raw);

  const done = run?.done ?? run?.progress?.done ?? 0;
  const total = run?.total ?? run?.progress?.total ?? 0;
  const pct = total ? Math.round((done / total) * 100) : 0;

  // children ordered by i (seq_no); corr child pinned last.
  const children = [...(run?.children || [])].sort((a, c) => {
    if (a.type === 'corr' && c.type !== 'corr') return 1;
    if (c.type === 'corr' && a.type !== 'corr') return -1;
    return (a.i ?? 0) - (c.i ?? 0);
  });

  return (
    <TerminalShell
      module="factor"
      current="queue"
      path={['factor_mgmt/', { label: 'queue/', to: '/factors/tasks' }, rid]}
      title="篮子运行详情"
      subtitle={run ? `${run.mode} · ${run.trigger} · ${run.status}` : rid}
      actions={live ? (
        <button className="btn danger sm" disabled={cancelMut.isPending} onClick={() => cancelMut.mutate()}>
          {cancelMut.isPending ? '取消中…' : '取消整代'}
        </button>
      ) : null}
    >
      {runQ.isLoading ? (
        <Loading />
      ) : runQ.isError ? (
        <ErrorState error={runQ.error} onRetry={runQ.refetch} />
      ) : !run ? (
        <Empty label="运行不存在" />
      ) : (
        <>
          {/* ── header: badges + window/as_of + progress ── */}
          <div style={{ padding: '12px 18px', background: 'var(--surface)', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <Badge group="mode" value={run.mode} sm />
              <Badge group="trigger" value={run.trigger} sm />
              <Badge group="run" value={run.status} />
              <span style={{ color: 'var(--fg-mute)' }}>·</span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)' }}>窗口 {run.win} · as_of {run.asOf}</span>
            </div>
            <div style={{ marginTop: 12, display: 'flex', alignItems: 'center', gap: 14 }}>
              <div style={{ flex: 1, height: 10, background: 'var(--surface-3)', borderRadius: 5, overflow: 'hidden', border: '1px solid var(--border)' }}>
                <div style={{ width: `${pct}%`, height: '100%', background: live ? 'var(--info)' : (run.status_raw === 'failed' ? 'var(--err)' : 'var(--ok)'), transition: 'width .3s' }} />
              </div>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--fg)', fontWeight: 500 }}>{done}/{total}</span>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)' }}>
                {live
                  ? (run.est_total_sec != null ? `预计 ${fmtDur(run.est_total_sec)}` : '运行中…')
                  : `耗时 ${run.dur || (run.duration_sec != null ? fmtDur(run.duration_sec) : '—')}`}
              </span>
            </div>
          </div>

          <div className="scroll" style={{ flex: 1, minHeight: 0, background: 'var(--bg)', padding: 16 }}>
            <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
              <SectionHead title="子任务" meta="按执行顺序 · 相关性固定末尾" />
              {children.length === 0 ? (
                <Empty label="无子任务" />
              ) : (
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11.5 }}>
                  <thead>
                    <tr style={{ background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}>
                      {['#', '条目', '状态', '用时 / 剩余', '降级'].map((h, i) => (
                        <th key={h} style={{ textAlign: i === 0 ? 'center' : 'left', padding: '6px 14px', fontSize: 9.5, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', fontFamily: 'var(--font-mono)' }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {children.map((ch) => {
                      const isCorr = ch.type === 'corr';
                      const waiting = isCorr && ch.gate === 'waiting';
                      return (
                        <tr key={ch.i ?? `${ch.type}-${ch.item_id}`} style={{ borderBottom: '1px solid var(--border-faint)', height: 34, background: isCorr ? 'var(--surface-2)' : 'transparent' }}>
                          <td style={{ padding: '0 14px', textAlign: 'center', fontFamily: 'var(--font-mono)', color: 'var(--fg-4)' }}>{ch.i ?? '—'}</td>
                          <td style={{ padding: '0 14px' }}>
                            {isCorr ? (
                              <span style={{ fontWeight: 500, color: 'var(--fg-2)' }}>◇ {ch.label}</span>
                            ) : (
                              <span
                                onClick={() => navigate(`/factors/baskets/${id}/items/${ch.item_id}`)}
                                style={{ fontWeight: 500, color: 'var(--fg)', cursor: 'pointer', textDecoration: 'underline', textDecorationColor: 'var(--border-strong)', textUnderlineOffset: 2 }}
                              >
                                {ch.label}
                              </span>
                            )}
                          </td>
                          <td style={{ padding: '0 14px' }}>
                            {waiting
                              ? <Badge group="wait" value="等待全部评估完成" sm />
                              : <Badge group="run" value={ch.status} sm />}
                          </td>
                          <td style={{ padding: '0 14px', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)' }}>{ch.used || ch.remain || '—'}</td>
                          <td style={{ padding: '0 14px' }}>
                            {ch.degrade && ch.degrade !== 'none'
                              ? <Badge group="degrade" value={degradeValue(ch.degrade)} sm />
                              : <span style={{ color: 'var(--fg-mute)' }}>—</span>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>

            <div style={{ marginTop: 14 }}>
              <Link to={`/factors/baskets/${id}`} className="btn ghost sm" style={{ textDecoration: 'none' }}>← 返回篮子</Link>
            </div>
          </div>
        </>
      )}
    </TerminalShell>
  );
}

function fmtDur(sec) {
  if (sec == null || Number.isNaN(Number(sec))) return '—';
  const s = Math.round(Number(sec));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r ? `${m}m${r}s` : `${m}m`;
}
