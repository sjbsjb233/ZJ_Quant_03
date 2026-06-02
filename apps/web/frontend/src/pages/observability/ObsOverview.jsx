// 可观测 · 总览 (system cockpit) — /observ/overview (可观测层重构方案 §7.3①)
// 各层状态 + 活跃告警 + 24h 量 + 管道健康。取代并扩展旧 LogsHub。
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { TerminalShell } from '../../components/shell';
import { Loading, ErrorState } from '../../components/ui';
import { endpoints } from '../../lib/api';
import { fmtN } from '../../lib/format';
import { StackedBars, LvlChip, SrcMark, TraceShort, fmtTsTime } from '../logs/shared';
import {
  OBS_LAYERS, ObsLayerMark, ObsHealthChip, ObsSev, ObsKap, ObsDrill, ObsLive,
  fmtUptime, agoFrom,
} from './shared';

const OVERALL = {
  ok: { word: '系统正常', en: 'OK', g: '●', tone: 'ok', col: 'var(--ok)', bg: 'var(--ok-bg)' },
  degraded: { word: '系统降级', en: 'DEGRADED', g: '◐', tone: 'warn', col: 'var(--warn)', bg: 'var(--warn-bg)' },
  down: { word: '系统故障', en: 'DOWN', g: '✕', tone: 'err', col: 'var(--err)', bg: 'var(--err-bg)' },
};

export default function ObsOverview() {
  const nav = useNavigate();
  const health = useQuery({ queryKey: ['monitorHealth'], queryFn: endpoints.monitorHealth, refetchInterval: 30_000 });
  const summary = useQuery({ queryKey: ['logsSummary'], queryFn: endpoints.logsSummary, refetchInterval: 60_000 });
  const firing = useQuery({
    queryKey: ['monitorAlerts', 'firing'],
    queryFn: () => endpoints.monitorAlerts({ status: 'firing', page_size: 5 }),
    refetchInterval: 30_000,
  });

  const h = health.data;
  const s = summary.data;
  const layers = h?.layers || {};
  const states = Object.values(layers).map((l) => l.state);
  const counts = {
    down: states.filter((x) => x === 'down').length,
    degraded: states.filter((x) => x === 'degraded').length,
    ok: states.filter((x) => x === 'ok').length,
  };
  const overall = OVERALL[h?.status || 'ok'] || OVERALL.ok;
  const alerts = firing.data?.items || [];

  const toTrace = (tid) => tid && nav(`/observ/trace/${tid}`);

  return (
    <TerminalShell
      module="observ" current="overview"
      path={['observ/', 'overview']}
      fkeys={[
        { k: 'F1', l: '告警', onClick: () => nav('/observ/alerts') },
        { k: 'F2', l: '拓扑', onClick: () => nav('/observ/topology') },
        { k: 'F3', l: '日志', onClick: () => nav('/observ/logs') },
        { k: 'F4', l: '任务', onClick: () => nav('/observ/tasks') },
        { k: 'F5', l: '刷新', onClick: () => { health.refetch(); summary.refetch(); firing.refetch(); } },
      ]}
      title="总览"
      subtitle="系统健康驾驶舱 · 全局一眼 · 监控层裁决 (30s 刷新)"
      badge={<ObsLive>实时</ObsLive>}
      actions={<button className="btn ghost sm" onClick={() => { health.refetch(); summary.refetch(); }}>↻ 刷新</button>}
      statusMiddle={h && <span>overall: <span style={{ color: overall.col }}>{overall.en}</span></span>}
    >
      {health.isLoading ? <Loading label="加载系统健康…" />
        : health.error ? <ErrorState error={health.error} onRetry={health.refetch} />
          : (
            <div className="scroll" style={{ flex: 1, minHeight: 0, overflow: 'auto', background: 'var(--bg)' }}>
              {/* ① overall health banner */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 20, padding: '14px 18px', background: overall.bg, borderBottom: `2px solid ${overall.col}` }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <span style={{ width: 34, height: 34, borderRadius: '50%', background: overall.col, color: 'var(--surface)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18, fontWeight: 700 }}>{overall.g}</span>
                  <div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 20, fontWeight: 600, color: overall.col, letterSpacing: '-0.01em' }}>{overall.word}</div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)', marginTop: 1 }}>
                      {overall.en} · {counts.down} 层故障 · {counts.degraded} 层降级 · {counts.ok} 层正常
                    </div>
                  </div>
                </div>
                <div style={{ width: 1, height: 34, background: overall.col, opacity: 0.4 }} />
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1 }}>
                  {h.alerts?.firing > 0 ? (
                    <>
                      <span className="chip err" style={{ fontSize: 12, height: 22, fontWeight: 600 }}>⚠ 当前 {h.alerts.firing} 条活跃告警</span>
                      {alerts[0] && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11.5, color: 'var(--fg-2)' }}>主因 <span style={{ color: 'var(--fg)', fontWeight: 500 }}>{alerts[0].title}</span></span>}
                    </>
                  ) : <span className="chip ok" style={{ fontSize: 12, height: 22 }}>✓ 无活跃告警</span>}
                </div>
                <button className="btn sm" style={{ background: overall.col, borderColor: overall.col, color: 'var(--surface)', fontWeight: 500 }} onClick={() => nav('/observ/alerts')}>查看全部告警 →</button>
              </div>

              {/* ② five layer status cards */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', borderBottom: '1px solid var(--border-strong)' }}>
                <LayerCard layer="frontend" state={layers.frontend?.state} metrics={[
                  ['24h 崩溃', layers.frontend?.crash_24h ?? 0, layers.frontend?.crash_24h ? 'warn' : null],
                ]} />
                <LayerCard layer="backend" state={layers.backend?.state} metrics={[
                  ['运行时长', fmtUptime(layers.backend?.uptime_s)],
                  ['近15m 错误', layers.backend?.err_rate_15m ?? 0, layers.backend?.err_rate_15m ? 'warn' : null],
                ]} />
                <LayerCard layer="data" state={layers.data?.state} highlight metrics={[
                  ['运行时长', fmtUptime(layers.data?.uptime_s), layers.data?.state === 'down' ? 'err' : null],
                  ['重启次数', layers.data?.restart_count ?? '—', layers.data?.restart_count ? 'err' : null],
                  ['被强杀', layers.data?.oom_killed ? 'OOM' : '—', layers.data?.oom_killed ? 'err' : null],
                  ['上次维护成功', agoFrom(layers.data?.last_job_success_at), null],
                  ['运行任务', layers.data?.jobs_running ?? '—'],
                  ['内存', layers.data?.mem_pct != null ? `${layers.data.mem_pct}%` : '—', layers.data?.mem_pct >= 85 ? 'err' : layers.data?.mem_pct >= 70 ? 'warn' : null],
                ]} />
                <LayerCard layer="factor" state={layers.factor?.state} metrics={[
                  ['worker', layers.factor?.worker === 'alive' ? '存活' : (layers.factor?.worker || '—'), layers.factor?.worker === 'alive' ? 'ok' : null],
                  ['内存', layers.factor?.mem_pct != null ? `${layers.factor.mem_pct}%` : '—', layers.factor?.mem_pct >= 85 ? 'err' : layers.factor?.mem_pct >= 70 ? 'warn' : null],
                  ['重启', layers.factor?.restart_count ?? '—', layers.factor?.restart_count ? 'warn' : null],
                ]} />
                <LayerCard layer="monitor" state={layers.monitor?.state} last metrics={[
                  ['哨兵', layers.monitor?.sentinel_last_seen ? '在线' : '未上线', layers.monitor?.sentinel_last_seen ? 'ok' : 'warn'],
                  ['评估器', layers.monitor?.eval_last_run ? '在线' : '—', layers.monitor?.eval_last_run ? 'ok' : null],
                ]} />
              </div>

              {/* ③ active alerts strip */}
              <div style={{ background: 'var(--surface)', borderBottom: '1px solid var(--border-strong)' }}>
                <div style={{ padding: '7px 14px', display: 'flex', alignItems: 'center', gap: 10, borderBottom: '1px solid var(--border)' }}>
                  <ObsKap>活跃告警 · 触发中</ObsKap>
                  <span className="chip err" style={{ fontSize: 10, height: 15 }}>{h.alerts?.firing || 0}</span>
                  <div style={{ flex: 1 }} />
                  <ObsDrill onClick={() => nav('/observ/alerts')}>告警页</ObsDrill>
                </div>
                {alerts.length === 0 ? (
                  <div style={{ padding: 16, textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-3)' }}>无触发中告警 🎉</div>
                ) : alerts.map((a) => (
                  <div key={a.id} onClick={() => nav('/observ/alerts')} style={{ display: 'grid', gridTemplateColumns: '92px 70px 1fr 200px 130px', gap: 12, alignItems: 'center', padding: '8px 14px', borderBottom: '1px solid var(--border-faint)', cursor: 'pointer', fontFamily: 'var(--font-mono)', fontSize: 11.5 }}>
                    <ObsSev sev={a.severity} mini />
                    <ObsLayerMark layer={a.layer} full />
                    <span style={{ color: 'var(--fg)', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.title}</span>
                    <span style={{ color: 'var(--fg-3)', fontSize: 10.5 }}>{a.target || '—'}</span>
                    <span style={{ color: 'var(--fg-3)', fontSize: 10.5 }}>触发 {agoFrom(a.first_seen)} · ×{a.count}</span>
                  </div>
                ))}
              </div>

              {/* ④ histogram + tops + ⑤ recent + ⑥ pipeline (reuse logs summary) */}
              {summary.isLoading ? <div style={{ padding: 30 }}><Loading label="加载日志聚合…" /></div>
                : s && (
                  <>
                    <div style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr', borderBottom: '1px solid var(--border-strong)' }}>
                      <div style={{ borderRight: '1px solid var(--border)', background: 'var(--surface)' }}>
                        <div style={{ padding: '7px 14px', display: 'flex', alignItems: 'center', gap: 10, background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}>
                          <ObsKap>24h 日志量 · 按级别堆叠</ObsKap>
                          <span style={{ flex: 1 }} />
                          <Legend />
                        </div>
                        <div style={{ padding: '16px 18px 12px' }}>
                          <div style={{ display: 'flex', alignItems: 'flex-end' }}>
                            <StackedBars data={s.timeline_24h} width={620} height={186} />
                          </div>
                        </div>
                      </div>
                      <div style={{ display: 'flex', flexDirection: 'column', background: 'var(--surface)' }}>
                        <div style={{ padding: '7px 14px', display: 'flex', alignItems: 'center', gap: 8, background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}>
                          <ObsKap>今日 TOP 错误</ObsKap><span style={{ flex: 1 }} /><span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>按事件分组</span>
                        </div>
                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
                          <tbody>
                            {(s.top_errors || []).slice(0, 6).map((r, i) => (
                              <tr key={i} style={{ borderBottom: '1px solid var(--border-faint)', height: 26, cursor: 'pointer' }}
                                onClick={() => nav(`/observ/logs?event=${encodeURIComponent(r.event)}`)}>
                                <td style={{ padding: '0 8px 0 14px', color: 'var(--fg)' }}>{r.event}</td>
                                <td style={{ padding: '0 8px', textAlign: 'right', color: 'var(--err)', fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>{r.count}</td>
                                <td style={{ padding: '0 14px 0 8px' }}><TraceShort id={r.last_trace_id} onClick={(e) => { e.stopPropagation(); toTrace(r.last_trace_id); }} /></td>
                              </tr>
                            ))}
                            {!s.top_errors?.length && <tr><td style={{ padding: 16, textAlign: 'center', color: 'var(--fg-3)' }}>今日无 ERROR</td></tr>}
                          </tbody>
                        </table>
                      </div>
                    </div>

                    {/* recent errors */}
                    <div style={{ background: 'var(--surface)', borderBottom: '1px solid var(--border-strong)' }}>
                      <div style={{ padding: '7px 14px', display: 'flex', alignItems: 'center', gap: 10, background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}>
                        <ObsKap>近期错误 · 最近 20 条</ObsKap><span style={{ flex: 1 }} />
                        <ObsDrill onClick={() => nav('/observ/logs?level=ERROR,CRITICAL')}>全部 ERROR</ObsDrill>
                      </div>
                      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
                        <thead><tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
                          {['time', 'lvl', 'layer', 'event', '摘要', 'route', 'actor', 'trace'].map((c) => (
                            <th key={c} style={{ textAlign: 'left', padding: '5px 10px', fontSize: 9, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{c}</th>
                          ))}
                        </tr></thead>
                        <tbody>
                          {(s.recent_errors || []).map((r, i) => (
                            <tr key={r.id} onClick={() => toTrace(r.trace_id)} style={{ borderBottom: '1px solid var(--border-faint)', height: 25, cursor: 'pointer', background: i % 2 ? 'rgba(0,0,0,0.012)' : 'transparent' }}>
                              <td style={{ padding: '0 10px', color: 'var(--fg-2)' }}>{fmtTsTime(r.ts)}</td>
                              <td style={{ padding: '0 10px' }}><LvlChip lvl={r.level} mini /></td>
                              <td style={{ padding: '0 10px' }}><SrcMark src={r.source} /></td>
                              <td style={{ padding: '0 10px', color: 'var(--fg)' }}>{r.event}</td>
                              <td style={{ padding: '0 10px', color: 'var(--fg-2)', maxWidth: 460, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.message || r.exc_message || '—'}</td>
                              <td style={{ padding: '0 10px', color: 'var(--fg-3)', fontSize: 10.5 }}>{r.route || '—'}</td>
                              <td style={{ padding: '0 10px', color: r.actor === 'system' ? 'var(--info)' : 'var(--fg-2)' }}>{r.actor || '—'}</td>
                              <td style={{ padding: '0 10px' }}><TraceShort id={r.trace_id} /></td>
                            </tr>
                          ))}
                          {!s.recent_errors?.length && <tr><td colSpan={8} style={{ padding: 20, textAlign: 'center', color: 'var(--fg-3)' }}>近期无 ERROR 🎉</td></tr>}
                        </tbody>
                      </table>
                    </div>

                    {/* pipeline health row */}
                    <div style={{ display: 'flex', alignItems: 'center', gap: 24, padding: '10px 18px', background: 'var(--surface-2)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>
                      <ObsKap>管道健康</ObsKap>
                      <span style={{ color: 'var(--fg-mute)' }}>│</span>
                      <span><span className={`dot ${s.health.db_sink === 'ok' ? 'ok' : 'err'}`} /> 日志落库: <span style={{ color: s.health.db_sink === 'ok' ? 'var(--ok)' : 'var(--err)' }}>{s.health.db_sink}</span></span>
                      <span><span className={`dot ${s.health.file_sink === 'ok' ? 'ok' : 'warn'}`} /> 日志落盘: <span style={{ color: s.health.file_sink === 'ok' ? 'var(--ok)' : 'var(--warn)' }}>{s.health.file_sink}</span></span>
                      <span><span className="dot info" /> 实时连接: <span style={{ color: 'var(--fg)' }}>{s.health.sse_clients}</span></span>
                      <span><span className="dot ok" /> 异步队列: <span style={{ color: 'var(--fg)' }}>{s.health.queue_size} / {fmtN(s.health.queue_max ?? 10000)}</span></span>
                      {s.health.dropped > 0 && <span style={{ color: 'var(--err)' }}>dropped: {s.health.dropped}</span>}
                      <span style={{ flex: 1 }} />
                      <span style={{ color: 'var(--fg-3)' }}>保留: <span style={{ color: 'var(--fg-2)' }}>app={s.health.retention_days}d</span></span>
                    </div>
                  </>
                )}
            </div>
          )}
    </TerminalShell>
  );
}

function LayerCard({ layer, state = 'ok', metrics, highlight, last }) {
  const dn = state === 'down'; const dg = state === 'degraded';
  return (
    <div style={{
      borderRight: last ? 'none' : '1px solid var(--border)',
      background: dn ? 'var(--err-bg)' : dg ? 'var(--warn-bg)' : 'var(--surface)',
      padding: '11px 14px', display: 'flex', flexDirection: 'column', gap: 9,
      boxShadow: highlight && dn ? 'inset 0 -2px 0 var(--err)' : 'none',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
        <ObsLayerMark layer={layer} size={18} />
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5, color: 'var(--fg)', fontWeight: 500 }}>{OBS_LAYERS[layer].label}</span>
        <span style={{ flex: 1 }} />
        <ObsHealthChip state={state} mini />
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
        {metrics.map(([k, v, tone], i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', fontFamily: 'var(--font-mono)', fontSize: 11 }}>
            <span style={{ color: 'var(--fg-3)', fontSize: 10.5 }}>{k}</span>
            <span style={{ color: tone === 'err' ? 'var(--err)' : tone === 'warn' ? 'var(--warn)' : tone === 'ok' ? 'var(--ok)' : 'var(--fg)', fontWeight: tone ? 600 : 500, fontVariantNumeric: 'tabular-nums' }}>{v}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Legend() {
  return (
    <span style={{ display: 'inline-flex', gap: 10, fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)' }}>
      <span><span style={{ display: 'inline-block', width: 8, height: 8, background: 'var(--info)', opacity: 0.55, marginRight: 4, verticalAlign: 'middle' }} />普通</span>
      <span><span style={{ display: 'inline-block', width: 8, height: 8, background: 'var(--warn)', marginRight: 4, verticalAlign: 'middle' }} />警示</span>
      <span><span style={{ display: 'inline-block', width: 8, height: 8, background: 'var(--err)', marginRight: 4, verticalAlign: 'middle' }} />严重</span>
    </span>
  );
}
