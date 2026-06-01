// /factors/tasks — queue dashboard: running / queued (per lane) / completed,
// with ETA, real-time progress, manual boost + cancel, and a live-log drawer.
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { endpoints, runLogStreamUrl } from '../../lib/api';
import { TerminalShell, FilterBar, FSelect, KpiStrip } from '../../components/shell';
import { Empty } from '../../components/ui';
import { DrawerHeader } from './RunConfigDrawer';
import RunConfigDrawer from './RunConfigDrawer';
import LogPanel from '../logs/LogPanel';
import { RunStatusBadge } from './shared';

export default function FactorTasks() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const [laneFilter, setLaneFilter] = React.useState('');
  const [statusFilter, setStatusFilter] = React.useState('');
  const [drawer, setDrawer] = React.useState(null);  // { kind: 'logs' | 'newrun', runId, factorId }

  const tasksQ = useQuery({
    queryKey: ['tasks.list', laneFilter, statusFilter],
    queryFn: () => endpoints.tasks({ lane: laneFilter || undefined, status: statusFilter || undefined, limit: 100 }),
    refetchInterval: 3000,
  });
  const statsQ = useQuery({
    queryKey: ['queue.stats'],
    queryFn: () => endpoints.queueStats(),
    refetchInterval: 5000,
  });

  const tasks = tasksQ.data?.items || [];
  const running = tasks.filter((t) => t.status === 'running');
  const queuedI = tasks.filter((t) => t.status === 'queued' && t.lane === 'interactive');
  const queuedD = tasks.filter((t) => t.status === 'queued' && t.lane === 'routine');
  const recent = tasks.filter((t) => ['success', 'failed', 'canceled'].includes(t.status));

  const stats = statsQ.data || {};
  const lanes = stats.lanes || {};

  return (
    <TerminalShell
      module="factor" current="queue"
      path={['factor_mgmt/', 'queue']}
      title="任务队列"
      subtitle={`queue · 运行 ${running.length} · 排队 ${queuedI.length + queuedD.length} · 完成 ${stats.today_done || 0}`}
      badge={<span style={{ color: 'var(--info)', fontFamily: 'var(--font-mono)', fontSize: 10 }}><span className="dot running" /> LIVE</span>}
      actions={
        <>
          <button className="btn ghost sm" onClick={() => statsQ.refetch()}>刷新</button>
          <button className="btn sm" data-testid="queue-newrun" style={{ background: 'var(--fg)', color: 'var(--bg)', borderColor: 'var(--fg)' }}
            onClick={() => setDrawer({ kind: 'newrun' })}>+ 新建运行</button>
        </>
      }
      filterBar={
        <FilterBar applied={[laneFilter, statusFilter].filter(Boolean)} onClear={() => { setLaneFilter(''); setStatusFilter(''); }}>
          <FSelect label="lane" value={laneFilter} onChange={setLaneFilter} width={130} options={[
            { value: '', label: '全部' }, { value: 'interactive', label: 'interactive' }, { value: 'routine', label: 'routine' },
          ]} />
          <FSelect label="status" value={statusFilter} onChange={setStatusFilter} width={130} options={[
            { value: '', label: '全部' }, { value: 'queued', label: 'queued' }, { value: 'running', label: 'running' },
            { value: 'success', label: 'success' }, { value: 'failed', label: 'failed' }, { value: 'canceled', label: 'canceled' },
          ]} />
        </FilterBar>
      }
    >
      <KpiStrip cells={[
        { label: 'LANE · 交互', value: `${lanes.interactive?.running || 0} / ${lanes.interactive?.queued || 0}`, sub: 'running/queue', tone: 'info' },
        { label: 'LANE · 日常', value: `${lanes.routine?.running || 0} / ${lanes.routine?.queued || 0}`, sub: 'running/queue' },
        { label: 'TODAY DONE', value: stats.today_done || 0, sub: '', tone: 'ok' },
        { label: 'TODAY FAILED', value: stats.today_failed || 0, sub: '', tone: stats.today_failed ? 'err' : 'neutral' },
        { label: '并发上限', value: stats.concurrency || 4, sub: `预留交互 ${stats.reserve_interactive || 1}` },
        { label: 'AVG WAIT', value: stats.avg_wait_sec != null ? `${stats.avg_wait_sec}s` : '—', sub: 'last hour' },
        { label: 'NEXT EVAL', value: stats.next_cron_at ? stats.next_cron_at.slice(11, 16) : '—', sub: stats.next_cron_count ? `daily cron · ${stats.next_cron_count} 因子` : '未配置' },
        { label: 'WORKERS', value: `${stats.workers_busy || 0} / ${stats.workers_total || 0}`, sub: stats.worker_host || 'local', tone: 'ok' },
      ]} />

      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        <div className="scroll" style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <Section title="RUNNING · 正在执行" count={running.length} accent="info">
            {running.length === 0 && <PlainEmpty label="当前无运行任务" />}
            {running.map((t) => (
              <RunningCard key={t.task_id} t={t}
                onCancel={() => endpoints.cancelTask(t.task_id).then(() => qc.invalidateQueries({ queryKey: ['tasks.list'] }))}
                onLogs={() => setDrawer({ kind: 'logs', runId: t.run_id, factorId: t.factor_id })} />
            ))}
          </Section>
          <Section title="QUEUED · 交互车道" count={queuedI.length} accent="info" meta="优先 · 用户手动发起">
            {queuedI.length === 0 && <PlainEmpty label="交互车道无排队" />}
            {queuedI.map((t) => (
              <QueuedCard key={t.task_id} t={t}
                onBoost={() => endpoints.boostTask(t.task_id).then(() => qc.invalidateQueries({ queryKey: ['tasks.list'] }))}
                onCancel={() => endpoints.cancelTask(t.task_id).then(() => qc.invalidateQueries({ queryKey: ['tasks.list'] }))} />
            ))}
          </Section>
          <Section title="QUEUED · 日常车道" count={queuedD.length} meta="后台慢跑 · 不抢资源">
            {queuedD.length === 0 && <PlainEmpty label="日常车道无排队" />}
            {queuedD.map((t) => (
              <QueuedCard key={t.task_id} t={t}
                onBoost={() => endpoints.boostTask(t.task_id).then(() => qc.invalidateQueries({ queryKey: ['tasks.list'] }))}
                onCancel={() => endpoints.cancelTask(t.task_id).then(() => qc.invalidateQueries({ queryKey: ['tasks.list'] }))} />
            ))}
          </Section>
          <Section title="COMPLETED · 已完成" count={recent.length}>
            {recent.length === 0 && <PlainEmpty label="今日尚无完成的任务" />}
            {recent.length > 0 && (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
                <thead><tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
                  {['task_id', 'kind', 'factor', 'lane', 'started', 'duration', 'status', 'user', ''].map((c, i) => (
                    <th key={i} style={{ textAlign: 'left', padding: '6px 12px', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase' }}>{c}</th>
                  ))}
                </tr></thead>
                <tbody>
                  {recent.map((t) => (
                    <React.Fragment key={t.task_id}>
                      <tr style={{ borderBottom: t.error ? 'none' : '1px solid var(--border-faint)', height: 26 }}>
                        <td style={{ padding: '0 12px', fontWeight: 500, color: 'var(--fg)' }}>{t.task_id}</td>
                        <td style={{ padding: '0 12px', color: 'var(--fg-2)' }}>{t.task_type}</td>
                        <td style={{ padding: '0 12px', color: 'var(--fg-2)' }}>{t.factor_id || '—'}</td>
                        <td style={{ padding: '0 12px', color: t.lane === 'interactive' ? 'var(--info)' : 'var(--fg-3)' }}>{t.lane}</td>
                        <td style={{ padding: '0 12px', color: 'var(--fg-3)' }}>{t.started_at?.replace('T', ' ').slice(0, 19)}</td>
                        <td style={{ padding: '0 12px', color: 'var(--fg-2)' }}>{t.runtime_sec ? `${t.runtime_sec}s` : t.started_at && t.finished_at ? `${Math.round((new Date(t.finished_at) - new Date(t.started_at)) / 1000)}s` : '—'}</td>
                        <td style={{ padding: '0 12px' }}>
                          {taskOutcomeBadge(t)}
                        </td>
                        <td style={{ padding: '0 12px', color: 'var(--fg-3)' }}>{t.initiated_by}</td>
                        <td style={{ padding: '0 12px', textAlign: 'right', fontSize: 10, color: 'var(--fg-3)' }}>
                          {t.status === 'failed' ? (
                            <>
                              <span onClick={() => endpoints.retryRun(t.run_id).then(() => qc.invalidateQueries({ queryKey: ['tasks.list'] }))}
                                style={{ color: 'var(--info)', marginRight: 10, cursor: 'pointer' }}>重试</span>
                              <span onClick={() => setDrawer({ kind: 'logs', runId: t.run_id, factorId: t.factor_id })}
                                style={{ cursor: 'pointer' }}>查看日志 →</span>
                            </>
                          ) : t.factor_id ? (
                            <span style={{ color: 'var(--info)', cursor: 'pointer' }}
                              onClick={() => nav(`/factors/library/${t.factor_id}`)}>跳结果 →</span>
                          ) : null}
                        </td>
                      </tr>
                      {t.error && (
                        <tr style={{ borderBottom: '1px solid var(--border-faint)' }}>
                          <td colSpan={9} style={{ padding: '4px 12px 8px 32px', background: 'var(--err-bg)', color: 'var(--err)', fontSize: 10.5 }}>
                            ✕ {t.error}
                          </td>
                        </tr>
                      )}
                    </React.Fragment>
                  ))}
                </tbody>
              </table>
            )}
          </Section>
        </div>

        {drawer?.kind === 'logs' && <LogsDrawer runId={drawer.runId} factorId={drawer.factorId} onClose={() => setDrawer(null)} />}
        {drawer?.kind === 'newrun' && (
          <RunConfigDrawer factorId={drawer.factorId || 'mom'} onClose={() => setDrawer(null)}
            onSubmitted={() => { setDrawer(null); qc.invalidateQueries({ queryKey: ['tasks.list'] }); }} />
        )}
      </div>
    </TerminalShell>
  );
}

// ─── helpers ─────────────────────────────────────────────────────────────
function statusBadge(s) {
  const map = { success: ['ok', '● ok'], failed: ['err', '✕ failed'], canceled: ['neutral', '⊘ canceled'] };
  const [tone, label] = map[s] || ['neutral', s];
  return <span style={{ color: `var(--${tone})` }}>{label}</span>;
}

// Reflect the *run* outcome (degraded / preflight-blocked) on the completed
// task row — the task may be "success" while the run is degraded (设计 §11.1).
function taskOutcomeBadge(t) {
  if (t.run_id && (t.run_status || t.degradation_count)) {
    return (
      <RunStatusBadge run={{
        status: t.run_status || t.status,
        error: t.run_error,
        degradation_level: t.degradation_level,
        degradation_count: t.degradation_count,
        has_critical: t.has_critical,
      }} />
    );
  }
  return statusBadge(t.status);
}

function Section({ title, count, meta, accent, children }) {
  return (
    <section>
      <header style={{
        padding: '6px 14px', background: 'var(--surface-2)',
        borderBottom: '1px solid var(--border)', borderTop: '1px solid var(--border)',
        display: 'flex', alignItems: 'center', gap: 10, position: 'sticky', top: 0, zIndex: 2,
      }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{title}</span>
        <span style={{
          fontFamily: 'var(--font-mono)', fontSize: 9.5,
          color: accent === 'info' ? 'var(--info)' : 'var(--fg-3)',
          background: accent === 'info' ? 'var(--info-bg)' : 'var(--surface-3)',
          padding: '0 5px', borderRadius: 2,
        }}>{count}</span>
        {meta && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)' }}>· {meta}</span>}
      </header>
      <div>{children}</div>
    </section>
  );
}

function PlainEmpty({ label }) {
  return <div style={{ padding: '14px 16px', color: 'var(--fg-3)', fontFamily: 'var(--font-mono)', fontSize: 10.5 }}>{label}</div>;
}

function RunningCard({ t, onCancel, onLogs }) {
  const pct = t.progress_total > 0 ? Math.min(100, Math.round(100 * t.progress_current / t.progress_total)) : 0;
  return (
    <div data-testid={`task-card-${t.task_id}`} style={{
      padding: '12px 16px', borderBottom: '1px solid var(--border-faint)',
      display: 'grid', gridTemplateColumns: '1fr auto', gap: 10,
      background: 'rgba(57, 121, 196, 0.04)',
    }}>
      <div style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4 }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: 'var(--info)', fontFamily: 'var(--font-mono)', fontSize: 10, textTransform: 'uppercase' }}>
            <span className="dot running" /> running
          </span>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 500, color: 'var(--fg)' }}>{t.task_id}</span>
          <span className="chip outline" style={{ height: 16, fontSize: 9.5 }}>{t.task_type}</span>
          <span className="chip info" style={{ height: 16, fontSize: 9.5 }}>{t.lane}</span>
          <span style={{ color: 'var(--fg-3)', fontFamily: 'var(--font-mono)', fontSize: 10.5 }}>· {t.factor_id || '(no factor)'}</span>
          <span style={{ color: 'var(--fg-3)', fontFamily: 'var(--font-mono)', fontSize: 10.5 }}>· {t.initiated_by}</span>
        </div>
        <div data-testid={`task-${t.task_id}-progress`} data-progress={pct} style={{ height: 6, background: 'var(--surface-3)', borderRadius: 3, overflow: 'hidden', marginTop: 6 }}>
          <div style={{
            width: `${pct}%`, height: '100%', background: 'var(--info)',
            backgroundImage: 'repeating-linear-gradient(45deg, transparent 0 6px, rgba(255,255,255,.18) 6px 12px)',
            transition: 'width .3s',
          }} />
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4, fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>
          <span>started {t.started_at?.slice(11, 16)} · 已运行 <span style={{ color: 'var(--fg)' }}>{t.runtime_sec ?? 0}s</span></span>
          <span><span style={{ color: 'var(--fg)' }}>{pct}%</span>{t.remaining_sec != null && <> · 剩余 <span style={{ color: 'var(--fg)' }}>~{t.remaining_sec}s</span></>}</span>
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6 }}>
        <button className="btn ghost sm" data-testid={`task-${t.task_id}-logs`} onClick={onLogs} style={{ fontSize: 10 }}>实时日志</button>
        <button className="btn ghost sm" data-testid={`task-${t.task_id}-cancel`} onClick={onCancel} style={{ fontSize: 10, color: 'var(--err)' }}>取消</button>
      </div>
    </div>
  );
}

function QueuedCard({ t, onBoost, onCancel }) {
  const eta = t.eta || {};
  return (
    <div data-testid={`task-card-${t.task_id}`} style={{
      padding: '8px 16px', borderBottom: '1px solid var(--border-faint)',
      display: 'grid', gridTemplateColumns: '28px 1fr auto auto auto', gap: 14, alignItems: 'center',
      fontFamily: 'var(--font-mono)', fontSize: 11,
    }}>
      <div style={{
        width: 22, height: 22, borderRadius: '50%', background: 'var(--surface-3)', border: '1px solid var(--border)',
        display: 'grid', placeItems: 'center', fontSize: 11, fontWeight: 500, color: 'var(--fg-2)',
      }}>{eta.queue_position ?? '?'}</div>
      <div style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontWeight: 500, color: 'var(--fg)' }}>{t.task_id}</span>
          <span className="chip outline" style={{ height: 14, fontSize: 9 }}>{t.task_type}</span>
          <span style={{ color: 'var(--fg-3)' }}>· {t.factor_id || '(no factor)'}</span>
          <span style={{ color: 'var(--fg-3)' }}>· {t.initiated_by}</span>
        </div>
      </div>
      <span data-testid={`task-${t.task_id}-eta`} style={{ color: 'var(--fg-3)' }}>预计等待 <span style={{ color: 'var(--fg)' }}>~{eta.est_wait_sec ?? '?'}s</span></span>
      <span style={{ color: 'var(--fg-3)' }}>完成 ~<span style={{ color: 'var(--fg)' }}>{eta.est_finish_at?.slice(11, 16) || '?'}</span></span>
      <div style={{ display: 'flex', gap: 4 }}>
        <button className="btn ghost sm" data-testid={`task-${t.task_id}-boost`} onClick={onBoost} style={{ fontSize: 9.5, height: 18, padding: '0 5px' }}>提升</button>
        <button className="btn ghost sm" data-testid={`task-${t.task_id}-cancel`} onClick={onCancel} style={{ fontSize: 9.5, height: 18, padding: '0 5px', color: 'var(--err)' }}>取消</button>
      </div>
    </div>
  );
}

// Run-log drawer with two views (V3 §4.4 D):
//   • 结构化 — the run's app_log rows by trace_id (run.start→stage→done, durations),
//     via the embedded <LogPanel source="factor"> with its own SSE live toggle;
//   • 原始   — the raw runs/<run_id>.log file tail (the pre-existing view).
function LogsDrawer({ runId, factorId, onClose }) {
  const nav = useNavigate();
  const [view, setView] = React.useState('structured');   // 'structured' | 'raw'
  const runQ = useQuery({
    queryKey: ['run', runId],
    queryFn: () => endpoints.run(runId),
    enabled: !!runId,
  });
  const traceId = runQ.data?.trace_id || '';

  // raw file-tail state
  const [lines, setLines] = React.useState([]);
  const [grep, setGrep] = React.useState('');
  const [levelFilter, setLevelFilter] = React.useState(new Set(['INFO', 'WARN', 'ERROR']));
  const [paused, setPaused] = React.useState(false);
  const bodyRef = React.useRef(null);

  React.useEffect(() => {
    if (!runId || view !== 'raw') return undefined;        // only tail when raw tab is open
    const es = new EventSource(runLogStreamUrl(runId));
    es.onmessage = (e) => {
      const text = e.data || '';
      // parse "<ts>  <LEVEL>  <msg>"
      const m = text.match(/^([\d\-T:.Z]+)\s+(\w+)\s+(.*)$/);
      const line = m ? { ts: m[1], level: m[2], msg: m[3] } : { ts: '', level: 'INFO', msg: text };
      setLines((prev) => [...prev.slice(-499), line]);
    };
    es.onerror = () => es.close();
    return () => es.close();
  }, [runId, view]);

  React.useEffect(() => {
    if (!paused && bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, [lines, paused]);

  const filtered = lines.filter((l) => levelFilter.has(l.level)).filter((l) => !grep || l.msg.toLowerCase().includes(grep.toLowerCase()));

  const toggleLevel = (l) => {
    const n = new Set(levelFilter);
    n.has(l) ? n.delete(l) : n.add(l);
    setLevelFilter(n);
  };

  const Tab = ({ id, children }) => (
    <span data-testid={`logsdrawer-tab-${id}`} onClick={() => setView(id)} style={{
      padding: '3px 10px', cursor: 'pointer', fontFamily: 'var(--font-mono)', fontSize: 10.5,
      borderRadius: 3, background: view === id ? 'var(--fg)' : 'var(--surface)',
      color: view === id ? 'var(--bg)' : 'var(--fg-2)', border: '1px solid var(--border)',
    }}>{children}</span>
  );

  return (
    <aside style={{ width: 560, flexShrink: 0, background: 'var(--surface)', display: 'flex', flexDirection: 'column', minHeight: 0, borderLeft: '1px solid var(--border)' }}>
      <DrawerHeader title={`运行日志 · ${runId || '—'}`} subtitle={factorId} onClose={onClose} />
      <div style={{
        height: 30, padding: '0 16px', borderBottom: '1px solid var(--border)', background: 'var(--surface-2)',
        display: 'flex', alignItems: 'center', gap: 8,
      }}>
        <Tab id="structured">结构化 · app_log</Tab>
        <Tab id="raw">原始 · file tail</Tab>
        <span style={{ flex: 1 }} />
        {traceId && (
          <span onClick={() => nav(`/data/logs/trace/${traceId}`)} title="看整条 trace"
            style={{ cursor: 'pointer', fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--info)' }}>
            trace: t_{traceId.replace(/^t_/, '').slice(-6)} →
          </span>
        )}
      </div>

      {view === 'structured' ? (
        traceId ? (
          <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, padding: 8 }}>
            <LogPanel source="factor" lockSource traceId={traceId} embed
              onTrace={(t) => nav(`/data/logs/trace/${t}`)} />
          </div>
        ) : (
          <div style={{ flex: 1, display: 'grid', placeItems: 'center', padding: 24, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>
            {runQ.isLoading ? '加载运行信息…' : '该运行无 trace_id,请切换到「原始」视图'}
          </div>
        )
      ) : (
        <>
          <div style={{
            height: 28, padding: '0 16px', borderBottom: '1px solid var(--border)', background: 'var(--surface-2)',
            display: 'flex', alignItems: 'center', gap: 10, fontFamily: 'var(--font-mono)', fontSize: 10,
          }}>
            <span style={{ color: 'var(--fg-3)' }}>level:</span>
            {['DEBUG', 'INFO', 'WARN', 'ERROR'].map((l) => (
              <span key={l} onClick={() => toggleLevel(l)} style={{
                padding: '1px 6px', borderRadius: 2, cursor: 'pointer',
                background: levelFilter.has(l) ? 'var(--surface)' : 'transparent',
                color: levelFilter.has(l) ? 'var(--fg)' : 'var(--fg-4)',
                border: `1px solid ${levelFilter.has(l) ? 'var(--border)' : 'transparent'}`,
              }}>{l}</span>
            ))}
            <span style={{ color: 'var(--fg-mute)' }}>│</span>
            <input placeholder="grep…" value={grep} onChange={(e) => setGrep(e.target.value)} style={{
              flex: 1, height: 20, padding: '0 7px', background: 'var(--surface)', border: '1px solid var(--border)',
              borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg)',
            }} />
          </div>
          <div ref={bodyRef} className="scroll" style={{
            flex: 1, minHeight: 0, padding: '8px 0', background: 'var(--surface-3)',
            fontFamily: 'var(--font-mono)', fontSize: 11, lineHeight: 1.55, color: 'var(--fg-2)',
          }}>
            {filtered.map((l, i) => {
              const c = l.level === 'WARN' ? 'var(--warn)' : l.level === 'ERROR' ? 'var(--err)' : l.level === 'INFO' ? 'var(--info)' : 'var(--fg-3)';
              return (
                <div key={i} style={{ padding: '1px 16px', display: 'grid', gridTemplateColumns: '180px 56px 1fr', gap: 8 }}>
                  <span style={{ color: 'var(--fg-4)' }}>{l.ts?.slice(0, 19)}</span>
                  <span style={{ color: c, fontWeight: 500 }}>{l.level}</span>
                  <span>{l.msg}</span>
                </div>
              );
            })}
            {!filtered.length && <div style={{ padding: 12, color: 'var(--fg-4)' }}><span className="dot running" /> 等待日志…</div>}
          </div>
          <footer style={{
            padding: '6px 16px', borderTop: '1px solid var(--border)', background: 'var(--surface-2)',
            display: 'flex', alignItems: 'center', fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)',
          }}>
            <span>{filtered.length} / {lines.length} lines · auto-scroll: <span style={{ color: paused ? 'var(--warn)' : 'var(--ok)' }}>{paused ? 'paused' : 'on'}</span></span>
            <div style={{ flex: 1 }} />
            <span onClick={() => setPaused(!paused)} style={{ cursor: 'pointer', color: 'var(--fg-2)' }}>{paused ? '继续' : '暂停'}</span>
          </footer>
        </>
      )}
    </aside>
  );
}
