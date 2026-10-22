// 可观测 · 任务 — /observ/tasks (可观测层重构方案 §7.3⑦)
// 死人开关可视化: 定时任务上次/上次成功/距今/下次 + keeper 任务台账。专治静默故障。
import React from 'react';
import { t } from '../../lib/i18n';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { TerminalShell } from '../../components/shell';
import { Loading, ErrorState, Empty } from '../../components/ui';
import { endpoints } from '../../lib/api';
import { fmtTs } from '../logs/shared';
import { ObsJobChip, ObsKap, ObsLive, ObsDetector, agoFrom } from './shared';

// 调度任务展示名 (真实任务名 crawl/quality/maintenance, §7.3⑦注)
const TASK_META = {
  crawl: { label: t('采集 · crawl'), icon: '⇣' },
  quality: { label: t('质检 · quality'), icon: '✓' },
  maintenance: { label: t('维护 · maintenance'), icon: '⚒' },
};

function hoursSince(ts) {
  if (!ts) return Infinity;
  const s = String(ts).endsWith('Z') || String(ts).includes('+') ? ts : `${ts}Z`;
  return (Date.now() - (Date.parse(s) || 0)) / 3.6e6;
}

export default function ObsTasks() {
  const nav = useNavigate();
  const q = useQuery({ queryKey: ['monitorTasks'], queryFn: () => endpoints.monitorTasks(), refetchInterval: 30_000 });
  const data = q.data;
  const deadman = data?.deadman_hours ?? 25;
  const crons = Object.entries(data?.scheduler_tasks || {});
  const jobs = data?.jobs || [];
  const overdueCount = crons.filter(([, t]) => hoursSince(t.last_success_at) > deadman).length;

  return (
    <TerminalShell
      module="observ" current="tasks"
      path={['observ/', 'tasks']}
      fkeys={[
        { k: 'F1', l: t('总览'), onClick: () => nav('/observ/overview') },
        { k: 'F2', l: t('告警'), onClick: () => nav('/observ/alerts') },
        { k: 'F5', l: t('刷新'), onClick: () => q.refetch() },
      ]}
      title={t('任务')}
      subtitle={t('tasks · 自治定时任务是否按时「成功」跑过 · 专治静默故障 (死人开关)')}
      badge={overdueCount > 0
        ? <span className="chip err" style={{ fontSize: 10 }}>{t('⚠ {n} 超期未成功', { n: overdueCount })}</span>
        : <ObsLive>{t('正常')}</ObsLive>}
      actions={<button className="btn ghost sm" onClick={() => q.refetch()}>{t('↻ 刷新')}</button>}
    >
      {q.isLoading ? <Loading label={t('加载任务…')} />
        : q.error ? <ErrorState error={q.error} onRetry={q.refetch} />
          : !data?.keeper ? (
            <div style={{ flex: 1, display: 'grid', placeItems: 'center', background: 'var(--bg)' }}>
              <Empty label={t('数据策管服务未连接')} hint={t('KEEPER_BASE 未设置 (本地写模式)；定时任务台账不可用')} />
            </div>
          ) : (
            <div className="scroll" style={{ flex: 1, minHeight: 0, overflow: 'auto', background: 'var(--bg)' }}>
              {/* 存活检测归属说明 (§7A.6): D7 死人开关的检测已移到哨兵直采 keeper /health;
                  本页任务台账 / 历史仍读 backend /monitor/tasks (keeper 数据), 展示不变。 */}
              <div style={{ padding: '10px 14px 0', display: 'flex', alignItems: 'center', gap: 8, fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)' }}>
                <ObsDetector kind="external" mini />
                <span>{t('存活检测 (死人开关) 由哨兵直采 keeper 负责;本页台账/历史仍读后端 (keeper 数据)。')}</span>
              </div>

              {/* scheduled-task cards */}
              <div style={{ padding: '14px 14px 6px' }}>
                <div style={{ marginBottom: 10 }}><ObsKap>{t('定时任务 · 死人开关 (>{h}h 无成功即超期)', { h: deadman })}</ObsKap></div>
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
                  {crons.length === 0 && <div style={{ gridColumn: '1 / -1', padding: 16, textAlign: 'center', color: 'var(--fg-3)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>{t('暂无定时任务记录')}</div>}
                  {crons.map(([name, t]) => <CronCard key={name} name={name} t={t} deadman={deadman} />)}
                </div>
              </div>

              {/* ledger */}
              <div style={{ padding: '8px 14px 16px' }}>
                <div style={{ background: 'var(--surface)', border: '1px solid var(--border-strong)', borderRadius: 5, overflow: 'hidden' }}>
                  <div style={{ padding: '7px 14px', display: 'flex', alignItems: 'center', gap: 10, background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}>
                    <ObsKap>{t('任务台账')}</ObsKap>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>· {t('{n} 条', { n: jobs.length })} · {t('含调度自治运行')}</span>
                  </div>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
                    <thead><tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
                      {[t('类型'), t('状态'), t('开始'), t('结束'), t('耗时'), t('发起方'), 'trace', ''].map((c) => (
                        <th key={c} style={{ textAlign: c === t('耗时') ? 'right' : 'left', padding: '6px 12px', fontSize: 9, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{c}</th>
                      ))}
                    </tr></thead>
                    <tbody>
                      {jobs.length === 0 && <tr><td colSpan={8} style={{ padding: 20, textAlign: 'center', color: 'var(--fg-3)' }}>{t('暂无任务记录')}</td></tr>}
                      {jobs.map((j) => {
                        const bad = j.status === 'failed' || j.status === 'timeout';
                        return (
                          <tr key={j.job_id} onClick={() => j.trace_id && nav(`/observ/trace/${j.trace_id}`)} style={{ borderBottom: '1px solid var(--border-faint)', height: 30, cursor: 'pointer', background: bad ? 'var(--err-bg)' : 'transparent', borderLeft: bad ? '2px solid var(--err)' : '2px solid transparent' }}>
                            <td style={{ padding: '0 12px', color: 'var(--fg)', fontWeight: 500 }}>{j.type}</td>
                            <td style={{ padding: '0 12px' }}><ObsJobChip state={j.status} mini /></td>
                            <td style={{ padding: '0 12px', color: 'var(--fg-2)' }}>{fmtTs(j.started_at)}</td>
                            <td style={{ padding: '0 12px', color: 'var(--fg-2)' }}>{j.finished_at ? fmtTs(j.finished_at) : '—'}</td>
                            <td style={{ padding: '0 12px', textAlign: 'right', color: bad ? 'var(--err)' : 'var(--fg-2)', fontVariantNumeric: 'tabular-nums' }}>{duration(j)}</td>
                            <td style={{ padding: '0 12px', color: j.initiated_by === 'scheduler' ? 'var(--info)' : 'var(--fg-2)' }}>{j.initiated_by}</td>
                            <td style={{ padding: '0 12px', color: 'var(--info)', fontSize: 10.5 }}>{j.trace_id ? `t_${j.trace_id.replace(/^t_/, '').slice(-6)}` : '—'}</td>
                            <td style={{ padding: '0 12px', textAlign: 'right', color: 'var(--info)', fontSize: 10 }}>Trace →</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}
    </TerminalShell>
  );
}

function CronCard({ name, t, deadman }) {
  const meta = TASK_META[name] || { label: name, icon: '◷' };
  const overdue = hoursSince(t.last_success_at) > deadman;
  return (
    <div style={{
      background: overdue ? 'var(--err-bg)' : 'var(--surface)',
      border: `1px solid ${overdue ? 'var(--err)' : 'var(--border-strong)'}`,
      borderRadius: 5, padding: '13px 15px',
      boxShadow: overdue ? '0 0 0 3px color-mix(in oklch, var(--err) 14%, transparent)' : 'none',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 12 }}>
        <span style={{ width: 26, height: 26, borderRadius: 5, background: overdue ? 'var(--err)' : 'var(--surface-3)', color: overdue ? 'var(--surface)' : 'var(--fg-2)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 14 }}>{meta.icon}</span>
        <div style={{ flex: 1 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--fg)', fontWeight: 600 }}>{meta.label}</div>
        </div>
        {overdue
          ? <span className="chip err" style={{ fontSize: 10.5, height: 19, fontWeight: 600 }}>{t('⚠ 超期未成功')}</span>
          : <span className="chip ok" style={{ fontSize: 10.5, height: 19 }}>{t('✓ 正常')}</span>}
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
        {[
          [t('上次运行'), t.last_run_at ? fmtTs(t.last_run_at) : t('从未'), false],
          [t('上次成功'), t.last_success_at ? fmtTs(t.last_success_at) : t('从未'), overdue],
          [t('距今'), t.last_success_at ? agoFrom(t.last_success_at) : '—', overdue],
        ].map(([k, v, hot]) => (
          <div key={k} style={{ display: 'flex', justifyContent: 'space-between', fontFamily: 'var(--font-mono)', fontSize: 11 }}>
            <span style={{ color: 'var(--fg-3)' }}>{k}</span>
            <span style={{ color: hot ? 'var(--err)' : 'var(--fg)', fontWeight: hot ? 600 : 500 }}>{v}</span>
          </div>
        ))}
      </div>
      {overdue && (
        <div style={{ marginTop: 11, paddingTop: 10, borderTop: '1px solid var(--err)', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--err)' }}>
          {t('▲ 已超过死人开关阈值 (>{h}h) · 数据写入链路疑似中断', { h: deadman })}
        </div>
      )}
    </div>
  );
}

function duration(j) {
  if (!j.started_at) return '—';
  const start = Date.parse(String(j.started_at).endsWith('Z') ? j.started_at : `${j.started_at}Z`);
  const end = j.finished_at ? Date.parse(String(j.finished_at).endsWith('Z') ? j.finished_at : `${j.finished_at}Z`) : Date.now();
  const sec = Math.max(0, Math.round((end - start) / 1000));
  if (sec < 60) return `${sec}s${j.finished_at ? '' : '…'}`;
  return `${Math.floor(sec / 60)}m${String(sec % 60).padStart(2, '0')}s${j.finished_at ? '' : '…'}`;
}
