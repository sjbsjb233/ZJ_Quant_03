// 可观测 · 总览 (system cockpit) — /observ/overview (可观测层重构方案 §7A.2)
// 双源合流: 哨兵 SSE 实时帧 (存活/基建真相) + 后端 (告警/日志历史)。新增基建资源面板
// (磁盘/CPU/崩溃转储)、实时状态指示、后端不可达降级横幅 + 历史区空态。
import React from 'react';
import { t } from '../../lib/i18n';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { TerminalShell } from '../../components/shell';
import { Loading } from '../../components/ui';
import { endpoints } from '../../lib/api';
import { useObsStatus } from '../../lib/obsStatus';
import { fmtN } from '../../lib/format';
import { StackedBars, LvlChip, SrcMark, TraceShort, DayDivider, fmtTs, fmtTsTime } from '../logs/shared';
import { dayBoundaryLabel } from '../../lib/format';
import {
  OBS_LAYERS, ObsLayerMark, ObsHealthChip, ObsSev, ObsKap, ObsDrill,
  ObsRealtime, ObsMeter, ObsResMark, ObsDetector, ObsDegradeBanner,
  fmtUptime, agoFrom,
} from './shared';

const OVERALL = {
  ok: { word: t('系统正常'), en: 'OK', g: '●', col: 'var(--ok)', bg: 'var(--ok-bg)' },
  degraded: { word: t('系统降级'), en: 'DEGRADED', g: '◐', col: 'var(--warn)', bg: 'var(--warn-bg)' },
  down: { word: t('系统故障'), en: 'DOWN', g: '✕', col: 'var(--err)', bg: 'var(--err-bg)' },
};
const DISK_WARN = 85; const DISK_CRIT = 95;   // 与后端/哨兵默认阈值一致 (D13)

export default function ObsOverview() {
  const nav = useNavigate();
  const obs = useObsStatus();
  const summary = useQuery({ queryKey: ['logsSummary'], queryFn: endpoints.logsSummary, refetchInterval: 60_000, enabled: obs.backendOk });
  const topo = useQuery({ queryKey: ['monitorTopology'], queryFn: endpoints.monitorTopology, refetchInterval: 30_000, enabled: obs.backendOk });
  const firing = useQuery({
    queryKey: ['monitorAlerts', 'firing'],
    queryFn: () => endpoints.monitorAlerts({ status: 'firing', page_size: 6 }),
    refetchInterval: 30_000, enabled: obs.backendOk,
  });

  const { layers, infra, source, backendOk } = obs;
  const beDown = !backendOk || layers.backend?.reachable === false || layers.backend?.state === 'error';
  const overall = OVERALL[obs.status] || OVERALL.ok;
  const s = summary.data;
  const alerts = firing.data?.items || [];
  const counts = {
    down: Object.values(layers).filter((l) => l.state === 'error').length,
    degraded: Object.values(layers).filter((l) => l.state === 'warn').length,
    ok: Object.values(layers).filter((l) => l.state === 'ok').length,
  };
  const toTrace = (tid) => tid && nav(`/observ/trace/${tid}`);

  return (
    <TerminalShell
      module="observ" current="overview"
      path={['observ/', 'overview']}
      fkeys={[
        { k: 'F1', l: t('告警'), onClick: () => nav('/observ/alerts') },
        { k: 'F2', l: t('拓扑'), onClick: () => nav('/observ/topology') },
        { k: 'F3', l: t('日志'), onClick: () => nav('/observ/logs') },
        { k: 'F4', l: t('任务'), onClick: () => nav('/observ/tasks') },
        { k: 'F5', l: t('刷新'), onClick: () => { obs.refetch(); summary.refetch(); topo.refetch(); firing.refetch(); } },
      ]}
      title={t('总览')}
      subtitle={beDown ? t('系统健康驾驶舱 · 后端不可达 · 实时存活仍在更新') : t('系统健康驾驶舱 · 全局一眼 · 双源合流 (哨兵实时 + 后端裁决)')}
      badge={<ObsRealtime state={source} detail={source === 'delayed' ? t('后端轮询') : source === 'stale' ? t('后端故障') : ''} />}
      actions={<button className="btn ghost sm" onClick={() => { obs.refetch(); summary.refetch(); topo.refetch(); }}>{t('↻ 刷新')}</button>}
      statusMiddle={<span>overall: <span style={{ color: overall.col }}>{overall.en}</span></span>}
    >
      <div className="scroll" style={{ flex: 1, minHeight: 0, overflow: 'auto', background: 'var(--bg)' }}>
        {/* §7A.2 后端不可达降级横幅 */}
        {beDown && <ObsDegradeBanner scope="overview" />}

        {/* ① overall health banner */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 20, padding: '14px 18px', background: overall.bg, borderBottom: `2px solid ${overall.col}` }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ width: 34, height: 34, borderRadius: '50%', background: overall.col, color: 'var(--surface)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 18, fontWeight: 700 }}>{overall.g}</span>
            <div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 20, fontWeight: 600, color: overall.col, letterSpacing: '-0.01em' }}>{overall.word}</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)', marginTop: 1 }}>
                {overall.en} · {t('{n} 层故障', { n: counts.down })} · {t('{n} 层降级', { n: counts.degraded })} · {t('{n} 层正常', { n: counts.ok })}{beDown ? t(' · 实时存活由哨兵(外层)持续上报') : ''}
              </div>
            </div>
          </div>
          <div style={{ width: 1, height: 34, background: overall.col, opacity: 0.4 }} />
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1 }}>
            {beDown ? (
              <>
                <span className="chip err" style={{ fontSize: 12, height: 22, fontWeight: 600 }}>{t('✕ 后端层不可达')}</span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11.5, color: 'var(--fg-2)' }}>{t('历史类数据(告警/日志/任务)暂不可读 · 实时存活与基建见下')}</span>
              </>
            ) : obs.firing > 0 ? (
              <>
                <span className="chip err" style={{ fontSize: 12, height: 22, fontWeight: 600 }}>{t('⚠ 当前 {n} 条活跃告警', { n: obs.firing })}</span>
                {alerts[0] && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11.5, color: 'var(--fg-2)' }}>{t('主因')} <span style={{ color: 'var(--fg)', fontWeight: 500 }}>{alerts[0].title}</span></span>}
              </>
            ) : <span className="chip ok" style={{ fontSize: 12, height: 22 }}>{t('✓ 无活跃告警')}</span>}
          </div>
          {beDown ? <ObsDetector kind="external" />
            : <button className="btn sm" style={{ background: overall.col, borderColor: overall.col, color: 'var(--surface)', fontWeight: 500 }} onClick={() => nav('/observ/alerts')}>{t('查看全部告警 →')}</button>}
        </div>

        {/* ② five layer status cards — 新增「可达/不可达」行 */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', borderBottom: '1px solid var(--border-strong)' }}>
          <LayerCard layer="frontend" L={layers.frontend} metrics={[
            [t('24h 崩溃'), layers.frontend?.health?.crash_24h ?? '—', layers.frontend?.health?.crash_24h ? 'warn' : null],
          ]} />
          <LayerCard layer="backend" L={layers.backend} highlight metrics={[
            [t('运行时长'), fmtUptime(layers.backend?.health?.uptime_s)],
            [t('近15m 错误'), layers.backend?.health?.err_rate_15m ?? '—', layers.backend?.health?.err_rate_15m ? 'warn' : null],
            ['CPU', layers.backend?.cpu_pct != null ? `${layers.backend.cpu_pct}%` : '—', (layers.backend?.cpu_pct ?? 0) >= 90 ? 'err' : (layers.backend?.cpu_pct ?? 0) >= 70 ? 'warn' : null],
          ]} />
          <LayerCard layer="data" L={layers.data} highlight metrics={[
            [t('运行时长'), fmtUptime(layers.data?.health?.uptime_s), layers.data?.state === 'error' ? 'err' : null],
            [t('重启次数'), layers.data?.restart_count ?? '—', layers.data?.restart_count ? 'err' : null],
            [t('被强杀'), layers.data?.oom_killed ? 'OOM' : '—', layers.data?.oom_killed ? 'err' : null],
            [t('上次成功'), agoFrom(layers.data?.health?.last_job_success_at)],
            ['CPU', layers.data?.cpu_pct != null ? `${layers.data.cpu_pct}%` : '—', (layers.data?.cpu_pct ?? 0) >= 90 ? 'err' : null],
            [t('内存'), layers.data?.mem_pct != null ? `${layers.data.mem_pct}%` : '—', (layers.data?.mem_pct ?? 0) >= 85 ? 'err' : (layers.data?.mem_pct ?? 0) >= 70 ? 'warn' : null],
          ]} />
          <LayerCard layer="factor" L={layers.factor} metrics={[
            ['worker', layers.factor?.health?.worker === 'alive' ? t('存活') : (layers.factor?.health?.worker || '—'), layers.factor?.health?.worker === 'alive' ? 'ok' : null],
            ['CPU', layers.factor?.cpu_pct != null ? `${layers.factor.cpu_pct}%` : '—', (layers.factor?.cpu_pct ?? 0) >= 90 ? 'err' : (layers.factor?.cpu_pct ?? 0) >= 70 ? 'warn' : null],
            [t('内存'), layers.factor?.mem_pct != null ? `${layers.factor.mem_pct}%` : '—', (layers.factor?.mem_pct ?? 0) >= 85 ? 'err' : (layers.factor?.mem_pct ?? 0) >= 70 ? 'warn' : null],
          ]} />
          <LayerCard layer="monitor" L={layers.monitor} last noReach metrics={[
            [t('哨兵'), obs.sentinelOnline ? t('在线') : t('未上线'), obs.sentinelOnline ? 'ok' : 'warn'],
            [t('实时'), source === 'live' ? t('哨兵 SSE') : source === 'delayed' ? t('轮询回退') : t('中断'), source === 'live' ? 'ok' : source === 'delayed' ? 'warn' : 'err'],
            [t('评估器'), layers.monitor?.health?.eval_last_run ? t('在线') : '—', layers.monitor?.health?.eval_last_run ? 'ok' : null],
          ]} />
        </div>

        {/* §7A.2 基建资源面板 — 本次核心新增 (磁盘/CPU/崩溃转储) */}
        <InfraResourcePanel obs={obs} topo={topo.data} />

        {/* ③ active alerts strip / 后端故障空态 */}
        {beDown ? (
          <UnavailableBand label={t('活跃告警 · 历史台账')} note={t('告警历史依赖后端 · 暂不可用 · 实时存活见上方各层卡与基建资源')} link={t('告警页')} onClick={() => nav('/observ/alerts')} />
        ) : (
          <div style={{ background: 'var(--surface)', borderBottom: '1px solid var(--border-strong)' }}>
            <div style={{ padding: '7px 14px', display: 'flex', alignItems: 'center', gap: 10, borderBottom: '1px solid var(--border)' }}>
              <ObsKap>{t('活跃告警 · 触发中')}</ObsKap>
              <span className="chip err" style={{ fontSize: 10, height: 15 }}>{obs.firing}</span>
              <div style={{ flex: 1 }} />
              <ObsDrill onClick={() => nav('/observ/alerts')}>{t('告警页')}</ObsDrill>
            </div>
            {alerts.length === 0 ? (
              <div style={{ padding: 16, textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-3)' }}>{t('无触发中告警 🎉')}</div>
            ) : alerts.map((a) => <AlertStripRow key={a.id} a={a} onClick={() => nav('/observ/alerts')} />)}
          </div>
        )}

        {/* ④ histogram + tops + ⑤ recent + ⑥ pipeline (history plane) */}
        {beDown ? (
          <UnavailableBand label={t('24h 日志量 · TOP 错误 · 近期错误 · 管道')} note={t('日志聚合依赖后端 · 暂不可用')} link={t('日志页')} onClick={() => nav('/observ/logs')} tall />
        ) : summary.isLoading ? <div style={{ padding: 30 }}><Loading label={t('加载日志聚合…')} /></div>
          : s && (
            <>
              <div style={{ display: 'grid', gridTemplateColumns: '1.5fr 1fr', borderBottom: '1px solid var(--border-strong)' }}>
                <div style={{ borderRight: '1px solid var(--border)', background: 'var(--surface)' }}>
                  <div style={{ padding: '7px 14px', display: 'flex', alignItems: 'center', gap: 10, background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}>
                    <ObsKap>{t('24h 日志量 · 按级别堆叠')}</ObsKap><span style={{ flex: 1 }} /><Legend />
                  </div>
                  <div style={{ padding: '16px 18px 12px' }}>
                    <div style={{ display: 'flex', alignItems: 'flex-end' }}>
                      <StackedBars data={s.timeline_24h} width={620} height={186} />
                    </div>
                  </div>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', background: 'var(--surface)' }}>
                  <div style={{ padding: '7px 14px', display: 'flex', alignItems: 'center', gap: 8, background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}>
                    <ObsKap>{t('今日 TOP 错误')}</ObsKap><span style={{ flex: 1 }} /><span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>{t('按事件分组')}</span>
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
                      {!s.top_errors?.length && <tr><td style={{ padding: 16, textAlign: 'center', color: 'var(--fg-3)' }}>{t('今日无 ERROR')}</td></tr>}
                    </tbody>
                  </table>
                </div>
              </div>

              <div style={{ background: 'var(--surface)', borderBottom: '1px solid var(--border-strong)' }}>
                <div style={{ padding: '7px 14px', display: 'flex', alignItems: 'center', gap: 10, background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}>
                  <ObsKap>{t('近期错误 · 最近 20 条')}</ObsKap><span style={{ flex: 1 }} />
                  <ObsDrill onClick={() => nav('/observ/logs?level=ERROR,CRITICAL')}>{t('全部 ERROR')}</ObsDrill>
                </div>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
                  <thead><tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
                    {['time', 'lvl', 'layer', 'event', t('摘要'), 'route', 'actor', 'trace'].map((c) => (
                      <th key={c} style={{ textAlign: 'left', padding: '5px 10px', fontSize: 9, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{c}</th>
                    ))}
                  </tr></thead>
                  <tbody>
                    {(s.recent_errors || []).map((r, i) => {
                      const divider = dayBoundaryLabel(s.recent_errors[i - 1]?.ts, r.ts);
                      return (
                        <React.Fragment key={r.id}>
                          {divider && <DayDivider label={divider} colSpan={8} />}
                          <tr onClick={() => toTrace(r.trace_id)} style={{ borderBottom: '1px solid var(--border-faint)', height: 25, cursor: 'pointer', background: i % 2 ? 'rgba(0,0,0,0.012)' : 'transparent' }}>
                            <td title={fmtTs(r.ts)} style={{ padding: '0 10px', color: 'var(--fg-2)' }}>{fmtTsTime(r.ts)}</td>
                            <td style={{ padding: '0 10px' }}><LvlChip lvl={r.level} mini /></td>
                            <td style={{ padding: '0 10px' }}><SrcMark src={r.source} /></td>
                            <td style={{ padding: '0 10px', color: 'var(--fg)' }}>{r.event}</td>
                            <td style={{ padding: '0 10px', color: 'var(--fg-2)', maxWidth: 460, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.message || r.exc_message || '—'}</td>
                            <td style={{ padding: '0 10px', color: 'var(--fg-3)', fontSize: 10.5 }}>{r.route || '—'}</td>
                            <td style={{ padding: '0 10px', color: r.actor === 'system' ? 'var(--info)' : 'var(--fg-2)' }}>{r.actor || '—'}</td>
                            <td style={{ padding: '0 10px' }}><TraceShort id={r.trace_id} /></td>
                          </tr>
                        </React.Fragment>
                      );
                    })}
                    {!s.recent_errors?.length && <tr><td colSpan={8} style={{ padding: 20, textAlign: 'center', color: 'var(--fg-3)' }}>{t('近期无 ERROR 🎉')}</td></tr>}
                  </tbody>
                </table>
              </div>

              {/* pipeline health row */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 24, padding: '10px 18px', background: 'var(--surface-2)', fontFamily: 'var(--font-mono)', fontSize: 11, flexWrap: 'wrap' }}>
                <ObsKap>{t('管道健康')}</ObsKap>
                <span style={{ color: 'var(--fg-mute)' }}>│</span>
                <span><span className={`dot ${s.health.db_sink === 'ok' ? 'ok' : 'err'}`} /> {t('日志落库:')} <span style={{ color: s.health.db_sink === 'ok' ? 'var(--ok)' : 'var(--err)' }}>{s.health.db_sink}</span></span>
                <span><span className={`dot ${s.health.file_sink === 'ok' ? 'ok' : 'warn'}`} /> {t('日志落盘:')} <span style={{ color: s.health.file_sink === 'ok' ? 'var(--ok)' : 'var(--warn)' }}>{s.health.file_sink}</span></span>
                <span><span className="dot info" /> {t('实时连接:')} <span style={{ color: 'var(--fg)' }}>{s.health.sse_clients}</span></span>
                <span><span className="dot ok" /> {t('异步队列:')} <span style={{ color: 'var(--fg)' }}>{s.health.queue_size} / {fmtN(s.health.queue_max ?? 10000)}</span></span>
                {s.health.dropped > 0 && <span style={{ color: 'var(--err)' }}>dropped: {s.health.dropped}</span>}
                <span style={{ flex: 1 }} />
                <ObsRealtime state={source} mini />
              </div>
            </>
          )}
      </div>
    </TerminalShell>
  );
}

// §7A.2 基建资源面板: 磁盘水位 / CPU 占用 / 崩溃转储 ──────────────────────────
function InfraResourcePanel({ obs, topo }) {
  const disks = obs.infra?.disk || [];
  const coreDumps = obs.infra?.core_dumps || [];
  // CPU: prefer per-container from topology (backend 在线), else per-layer from frame
  const cpus = (topo?.containers || [])
    .filter((c) => c.cpu_pct != null)
    .map((c) => ({ name: c.name, layer: c.layer, pct: Math.round(c.cpu_pct), killed: c.reachable === false }));
  const cpuFallback = ['frontend', 'backend', 'data', 'factor', 'monitor']
    .map((k) => ({ name: k, layer: k, pct: obs.layers[k]?.cpu_pct != null ? Math.round(obs.layers[k].cpu_pct) : null, killed: obs.layers[k]?.reachable === false }))
    .filter((c) => c.pct != null || c.killed);
  const cpuList = cpus.length ? cpus : cpuFallback;

  return (
    <div style={{ background: 'var(--surface)', borderBottom: '1px solid var(--border-strong)' }}>
      <div style={{ padding: '7px 14px', display: 'flex', alignItems: 'center', gap: 10, background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}>
        <ObsKap>{t('基建资源 · 主机 + 容器')}</ObsKap>
        <span className="chip" style={{ fontSize: 9, height: 14, background: 'var(--info-bg)', color: 'var(--info)' }}>NEW</span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>{t('· 磁盘 / CPU / 崩溃转储 · 非第六个层 · 哨兵 (docker.sock + statvfs) 采集')}</span>
        <span style={{ flex: 1 }} />
        <ObsRealtime state={obs.source} mini />
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1.05fr 1.1fr 0.95fr' }}>
        {/* 磁盘水位 */}
        <div style={{ padding: '12px 16px', borderRight: '1px solid var(--border)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 12 }}>
            <ObsResMark res="disk" tone={disks.some((d) => d.state === 'error') ? 'err' : disks.some((d) => d.state === 'warn') ? 'warn' : 'ok'} />
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11.5, color: 'var(--fg)', fontWeight: 500 }}>{t('磁盘水位')}</span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-4)' }}>{t('主机级')}</span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 11 }}>
            {disks.length === 0 ? <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-mute)' }}>{t('哨兵未上报磁盘 (未挂载宿主分区)')}</span>
              : disks.map((d) => <ObsMeter key={d.mount} label={d.mount} pct={d.used_pct} warnAt={DISK_WARN} dangerAt={DISK_CRIT} />)}
          </div>
          {disks.some((d) => d.state === 'error') && <div style={{ marginTop: 11, fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--err)' }}>{t('⚠ 分区超阈值 ({p}%) · 触发 D13', { p: DISK_CRIT })}</div>}
        </div>
        {/* CPU 占用 */}
        <div style={{ padding: '12px 16px', borderRight: '1px solid var(--border)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 12 }}>
            <ObsResMark res="cpu" tone={cpuList.some((c) => (c.pct ?? 0) >= 90) ? 'err' : cpuList.some((c) => (c.pct ?? 0) >= 70) ? 'warn' : 'ok'} />
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11.5, color: 'var(--fg)', fontWeight: 500 }}>{t('CPU 占用')}</span>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-4)' }}>{t('各容器')}</span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {cpuList.length === 0 ? <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-mute)' }}>{t('无 CPU 数据')}</span>
              : cpuList.map((c) => (
                <div key={c.name} style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
                  <ObsLayerMark layer={c.layer} />
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-2)', width: 116, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</span>
                  <ObsMeter pct={c.pct ?? 0} killed={c.killed} warnAt={70} dangerAt={90} />
                </div>
              ))}
          </div>
        </div>
        {/* 崩溃转储 */}
        <div style={{ padding: '12px 16px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 12 }}>
            <ObsResMark res="dump" tone={coreDumps.length ? 'err' : 'ok'} />
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11.5, color: 'var(--fg)', fontWeight: 500 }}>{t('崩溃转储')}</span>
            <span style={{ flex: 1 }} />
            {coreDumps.length > 0 && <span className="chip err" style={{ fontSize: 9.5, height: 15 }}>▲ {coreDumps.length}</span>}
          </div>
          {coreDumps.length === 0 ? (
            <div style={{ padding: '14px 12px', textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-3)', border: '1px dashed var(--border-strong)', borderRadius: 4 }}>
              {t('无崩溃转储 ✓')}
            </div>
          ) : coreDumps.map((d) => (
            <div key={d.container} style={{ padding: '10px 12px', marginBottom: 6, background: 'var(--err-bg)', border: '1px solid var(--err)', borderRadius: 4, boxShadow: '0 0 0 3px color-mix(in oklch, var(--err) 12%, transparent)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 7 }}>
                <span style={{ color: 'var(--err)', fontWeight: 600, fontFamily: 'var(--font-mono)', fontSize: 11.5 }}>{d.container}</span>
                <span style={{ flex: 1 }} />
                <ObsDetector kind="external" mini />
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '3px 10px', fontFamily: 'var(--font-mono)', fontSize: 10.5 }}>
                <span style={{ color: 'var(--fg-3)' }}>{t('可写层')}</span><span style={{ color: 'var(--err)', fontWeight: 600 }}>{d.rw_size_gb} GiB</span>
                <span style={{ color: 'var(--fg-3)' }}>{t('判定')}</span><span style={{ color: 'var(--fg-2)' }}>{t('SizeRw 突增 → 疑似 core-dump (D15)')}</span>
              </div>
            </div>
          ))}
          <div style={{ marginTop: 8, fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-4)' }}>{t('平时此处为「无」· 出现即醒目警示(上次事故盲点)')}</div>
        </div>
      </div>
    </div>
  );
}

function AlertStripRow({ a, onClick }) {
  const res = a.rule_id === 'D13.disk' ? 'disk' : a.rule_id === 'D14.cpu_saturation' ? 'cpu' : a.rule_id === 'D15.core_dump' ? 'dump' : null;
  const det = a.detail?.detector_owner === 'sentinel' || ['L0.unreachable', 'D1.oom_killed', 'D2.restart_storm', 'D3.unhealthy', 'D4.mem_pressure', 'D7.deadman', 'D13.disk', 'D14.cpu_saturation', 'D15.core_dump'].includes(a.rule_id) ? 'external' : 'internal';
  return (
    <div onClick={onClick} style={{ display: 'grid', gridTemplateColumns: '92px 24px 70px 1fr 150px 132px', gap: 10, alignItems: 'center', padding: '8px 14px', borderBottom: '1px solid var(--border-faint)', cursor: 'pointer', fontFamily: 'var(--font-mono)', fontSize: 11.5 }}>
      <ObsSev sev={a.severity} mini />
      <span style={{ display: 'inline-flex', justifyContent: 'center' }}>{res ? <ObsResMark res={res} tone={a.severity === 'warn' ? 'warn' : 'err'} /> : <span style={{ color: 'var(--fg-mute)' }}>—</span>}</span>
      <ObsLayerMark layer={a.layer} full />
      <span style={{ color: 'var(--fg)', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.title}</span>
      <span style={{ color: 'var(--fg-3)', fontSize: 10.5 }}>{a.target || '—'}</span>
      <span style={{ display: 'flex', alignItems: 'center', gap: 7 }}><ObsDetector kind={det} mini /><span style={{ color: 'var(--fg-3)', fontSize: 10 }}>{agoFrom(a.first_seen)}</span></span>
    </div>
  );
}

function UnavailableBand({ label, note, link, onClick, tall }) {
  return (
    <div style={{ background: 'var(--surface)', borderBottom: '1px solid var(--border-strong)' }}>
      <div style={{ padding: '7px 14px', display: 'flex', alignItems: 'center', gap: 10, background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}>
        <ObsKap>{label}</ObsKap>
        <span className="chip neutral" style={{ fontSize: 9.5, height: 15 }}>{t('暂不可用')}</span>
        <span style={{ flex: 1 }} />
        <ObsDrill onClick={onClick}>{link}</ObsDrill>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 10, padding: tall ? '34px 16px' : '20px 16px', color: 'var(--fg-3)', fontFamily: 'var(--font-mono)', fontSize: 11.5 }}>
        <span style={{ width: 22, height: 22, borderRadius: '50%', border: '1.5px solid var(--border-strong)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', color: 'var(--fg-4)', fontSize: 12 }}>✕</span>
        <span>{note}</span>
      </div>
    </div>
  );
}

// layer status card — 新增 reach 「可达/不可达」行
function LayerCard({ layer, L = {}, metrics, highlight, last, noReach }) {
  const state = L.state;                 // undefined ⇒ 未知 (两路皆断时), 绝不伪绿
  const unknown = !state;
  const dn = state === 'error'; const dg = state === 'warn';
  const reach = L.reachable;
  const [rw, rc] = reach === false ? [t('不可达'), 'var(--err)'] : reach === true ? [t('可达'), 'var(--ok)'] : ['—', 'var(--fg-3)'];
  return (
    <div style={{
      borderRight: last ? 'none' : '1px solid var(--border)',
      background: dn ? 'var(--err-bg)' : dg ? 'var(--warn-bg)' : 'var(--surface)',
      padding: '11px 14px', display: 'flex', flexDirection: 'column', gap: 9,
      boxShadow: highlight && (dn || dg) ? `inset 0 -2px 0 ${dn ? 'var(--err)' : 'var(--warn)'}` : 'none',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
        <ObsLayerMark layer={layer} size={18} />
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5, color: 'var(--fg)', fontWeight: 500 }}>{OBS_LAYERS[layer].label}</span>
        <span style={{ flex: 1 }} />
        {unknown
          ? <span className="chip neutral" style={{ fontSize: 9.5, height: 15 }}>{t('未知')}</span>
          : <ObsHealthChip state={state === 'error' ? 'down' : state === 'warn' ? 'degraded' : 'ok'} mini />}
      </div>
      {!noReach && (
        <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontFamily: 'var(--font-mono)', fontSize: 10, paddingBottom: 6, borderBottom: '1px solid var(--border-faint)' }}>
          <span style={{ width: 5, height: 5, borderRadius: '50%', background: rc, animation: reach === false ? 'pulse 1.5s ease-in-out infinite' : 'none' }} />
          <span style={{ color: 'var(--fg-3)' }}>{t('探测')}</span>
          <span style={{ color: rc, fontWeight: 500 }}>{rw}</span>
          <span style={{ flex: 1 }} />
          <span style={{ color: 'var(--fg-4)', fontSize: 9 }}>{t('哨兵')}</span>
        </div>
      )}
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
      <span><span style={{ display: 'inline-block', width: 8, height: 8, background: 'var(--info)', opacity: 0.55, marginRight: 4, verticalAlign: 'middle' }} />{t('普通')}</span>
      <span><span style={{ display: 'inline-block', width: 8, height: 8, background: 'var(--warn)', marginRight: 4, verticalAlign: 'middle' }} />{t('警示')}</span>
      <span><span style={{ display: 'inline-block', width: 8, height: 8, background: 'var(--err)', marginRight: 4, verticalAlign: 'middle' }} />{t('严重')}</span>
    </span>
  );
}
