import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { TerminalShell, TPanel, KpiStrip } from '../components/shell';
import { Loading, Empty, ErrorState, DangerConfirm, StatusChip, RelTime } from '../components/ui';
import { endpoints } from '../lib/api';
import { fmtBytes, fmtN, fmtDuration, relTime, absTime, statusMeta, toneColor } from '../lib/format';
import { useAuth } from '../lib/auth';

const RAW_TABLES = [
  'raw.bars_daily', 'raw.bars_minute', 'raw.fundamentals',
  'raw.universe', 'raw.calendar', 'raw.corporate_actions',
];

export default function Maintenance() {
  const { hasRole, user } = useAuth();
  const isAdmin = hasRole('admin');
  const qc = useQueryClient();

  // Vacuum state
  const [vacuumTables, setVacuumTables] = useState([]);
  const [vacuumDays, setVacuumDays] = useState(90);
  const [showVacuumConfirm, setShowVacuumConfirm] = useState(false);
  const [vacuumJobId, setVacuumJobId] = useState(null);

  // Compact state
  const [compactTables, setCompactTables] = useState([]);
  const [showCompactConfirm, setShowCompactConfirm] = useState(false);
  const [compactJobId, setCompactJobId] = useState(null);

  // Snapshot state
  const [snapshotJobId, setSnapshotJobId] = useState(null);
  const [confirmDeleteSnap, setConfirmDeleteSnap] = useState(null);

  // Status strip: vacuum preview with 90-day default for reclaimable bytes
  const vacuumStatus = useQuery({
    queryKey: ['vacuum-preview-status'],
    queryFn: () => endpoints.vacuumPreview({ older_than_days: 90 }),
    enabled: isAdmin,
    staleTime: 60000,
  });

  // Vacuum preview (user-configured params)
  const vacuumPreview = useQuery({
    queryKey: ['vacuum-preview', vacuumTables, vacuumDays],
    queryFn: () => endpoints.vacuumPreview({
      ...(vacuumTables.length && { tables: vacuumTables }),
      older_than_days: vacuumDays,
    }),
    enabled: isAdmin,
  });

  // Compact preview
  const compactPreview = useQuery({
    queryKey: ['compact-preview', compactTables],
    queryFn: () => endpoints.compactPreview({
      ...(compactTables.length && { tables: compactTables }),
    }),
    enabled: isAdmin,
  });

  // Snapshots list
  const snapshots = useQuery({
    queryKey: ['snapshots'],
    queryFn: endpoints.snapshots,
    enabled: isAdmin,
  });

  // Job polls
  const vacuumJob = useQuery({
    queryKey: ['job', vacuumJobId],
    queryFn: () => endpoints.job(vacuumJobId),
    enabled: !!vacuumJobId,
    refetchInterval: (q) =>
      ['success', 'failed', 'cancelled'].includes(q.state.data?.status) ? false : 700,
  });

  const compactJob = useQuery({
    queryKey: ['job', compactJobId],
    queryFn: () => endpoints.job(compactJobId),
    enabled: !!compactJobId,
    refetchInterval: (q) =>
      ['success', 'failed', 'cancelled'].includes(q.state.data?.status) ? false : 700,
  });

  const snapshotJob = useQuery({
    queryKey: ['job', snapshotJobId],
    queryFn: () => endpoints.job(snapshotJobId),
    enabled: !!snapshotJobId,
    refetchInterval: (q) =>
      ['success', 'failed', 'cancelled'].includes(q.state.data?.status) ? false : 700,
  });

  // Mutations
  const vacuumExecute = useMutation({
    mutationFn: () => endpoints.vacuumExecute({
      ...(vacuumTables.length && { tables: vacuumTables }),
      older_than_days: vacuumDays,
      confirm: 'VACUUM',
    }),
    onSuccess: (data) => {
      setVacuumJobId(data.job_id);
      setShowVacuumConfirm(false);
      qc.invalidateQueries({ queryKey: ['vacuum-preview-status'] });
    },
  });

  const compactExecute = useMutation({
    mutationFn: () => endpoints.compactExecute({
      ...(compactTables.length && { tables: compactTables }),
      confirm: 'COMPACT',
    }),
    onSuccess: (data) => {
      setCompactJobId(data.job_id);
      setShowCompactConfirm(false);
    },
  });

  const snapshotCreate = useMutation({
    mutationFn: () => endpoints.snapshot(),
    onSuccess: (data) => {
      if (data?.job_id) setSnapshotJobId(data.job_id);
      qc.invalidateQueries({ queryKey: ['snapshots'] });
    },
  });

  const snapshotDelete = useMutation({
    mutationFn: (id) => endpoints.deleteSnapshot(id),
    onSuccess: () => {
      setConfirmDeleteSnap(null);
      qc.invalidateQueries({ queryKey: ['snapshots'] });
    },
  });

  const refreshStats = useMutation({
    mutationFn: endpoints.refreshStats,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['vacuum-preview-status'] });
      qc.invalidateQueries({ queryKey: ['compact-preview'] });
    },
  });

  const cancelVacuumJob = useMutation({ mutationFn: () => endpoints.cancelJob(vacuumJobId) });
  const cancelCompactJob = useMutation({ mutationFn: () => endpoints.cancelJob(compactJobId) });

  const reclaimable = vacuumStatus.data?.total_bytes ?? 0;
  const reclaimableFiles = vacuumStatus.data?.file_count ?? 0;

  const kpiCells = [
    { label: 'RECLAIMABLE', value: fmtBytes(reclaimable), sub: `${fmtN(reclaimableFiles)} 文件` },
    { label: 'VACUUM JOB', value: vacuumJobId ? (vacuumJob.data?.status || '…') : '—', tone: vacuumJobId && vacuumJob.data?.status === 'running' ? 'info' : undefined, pulse: vacuumJob.data?.status === 'running' },
    { label: 'COMPACT JOB', value: compactJobId ? (compactJob.data?.status || '…') : '—', tone: compactJobId && compactJob.data?.status === 'running' ? 'info' : undefined, pulse: compactJob.data?.status === 'running' },
    { label: 'SNAPSHOTS', value: fmtN(snapshots.data?.total ?? 0) },
    { label: 'COMPACT PARTITIONS', value: fmtN(compactPreview.data?.partition_count ?? 0) },
  ];

  if (!isAdmin) {
    return (
      <TerminalShell
        module="data"
        current="maintenance"
        path={['data_mgmt/', 'maintenance']}
        title="维护操作"
        subtitle="maintenance · admin only"
        badge={<span className="chip warn" style={{ fontSize: 10 }}>ADMIN</span>}
      >
        <KpiStrip cells={kpiCells} height={56} />
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 40, minHeight: 0 }}>
          <div style={{ textAlign: 'center', fontFamily: 'var(--font-mono)' }}>
            <div style={{ fontSize: 48, color: 'var(--err)', marginBottom: 12 }}>403</div>
            <div style={{ fontSize: 16, color: 'var(--fg)', marginBottom: 8 }}>需要管理员权限</div>
            <div style={{ fontSize: 12, color: 'var(--fg-3)', maxWidth: 360 }}>
              维护操作仅限 admin 角色使用。当前角色: <span style={{ color: 'var(--warn)' }}>{user?.role || '—'}</span>
              <br />请联系管理员申请权限升级。
            </div>
          </div>
        </div>
      </TerminalShell>
    );
  }

  return (
    <TerminalShell
      module="data"
      current="maintenance"
      path={['data_mgmt/', 'maintenance']}
      fkeys={[
        { k: 'F1', l: 'Vacuum' },
        { k: 'F2', l: 'Compact' },
        { k: 'F3', l: '快照' },
      ]}
      title="维护操作"
      subtitle={`maintenance · admin only · ${fmtBytes(reclaimable)} reclaimable`}
      badge={<span className="chip warn" style={{ fontSize: 10 }}>ADMIN</span>}
      actions={
        <button className="btn ghost sm" onClick={() => refreshStats.mutate()} disabled={refreshStats.isPending}>
          {refreshStats.isPending ? '刷新中…' : '刷新统计'}
        </button>
      }
    >
      <KpiStrip cells={kpiCells} height={56} />

      <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1.3fr 1fr', minHeight: 0 }}>
        {/* Left: operation cards */}
        <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0, borderRight: '1px solid var(--border)' }}>
          {/* Vacuum card */}
          <TPanel title="VACUUM 旧版本" meta="删除超保留期文件" noBorderRight noBorderBottom={false}
            action={
              <button className="btn ghost sm" style={{ height: 20, fontSize: 10, color: 'var(--err)' }}
                onClick={() => setShowVacuumConfirm(true)}
                disabled={vacuumJob.data?.status === 'running'}>
                ⚠ 立即执行
              </button>
            }>
            <div style={{ padding: '10px 14px', display: 'flex', flexDirection: 'column', gap: 10 }}>
              {/* Config */}
              <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
                <div>
                  <label style={{ fontSize: 10, color: 'var(--fg-3)', display: 'block', marginBottom: 3, fontFamily: 'var(--font-mono)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>older_than_days</label>
                  <input type="number" value={vacuumDays} min={1} max={365}
                    onChange={e => setVacuumDays(Number(e.target.value))}
                    style={{ width: 80, height: 24, padding: '0 8px', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)' }} />
                </div>
                <div style={{ flex: 1 }}>
                  <label style={{ fontSize: 10, color: 'var(--fg-3)', display: 'block', marginBottom: 3, fontFamily: 'var(--font-mono)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>目标表（空=全部）</label>
                  <TableMultiSelect selected={vacuumTables} onChange={setVacuumTables} />
                </div>
              </div>

              {/* Preview table */}
              {vacuumPreview.isLoading ? <Loading label="计算预览…" /> : vacuumPreview.data && (
                <div>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>
                    预览 · {fmtN(vacuumPreview.data.file_count)} 个文件 · {fmtBytes(vacuumPreview.data.total_bytes)}
                  </div>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--font-mono)', fontSize: 10.5 }}>
                    <thead>
                      <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
                        {['表名', '文件数', '大小'].map((h, i) => (
                          <th key={h} style={{ padding: '4px 8px', textAlign: i > 0 ? 'right' : 'left', fontSize: 9, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {(vacuumPreview.data.by_table || []).map((r, i) => (
                        <tr key={r.table || i} style={{ borderBottom: '1px solid var(--border-faint)', height: 22 }}>
                          <td style={{ padding: '0 8px', color: 'var(--fg)' }}>{r.table}</td>
                          <td style={{ padding: '0 8px', textAlign: 'right', color: 'var(--fg-2)', fontVariantNumeric: 'tabular-nums' }}>{fmtN(r.files)}</td>
                          <td style={{ padding: '0 8px', textAlign: 'right', color: 'var(--fg-2)' }}>{fmtBytes(r.bytes)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Job progress */}
              {vacuumJobId && <JobProgress job={vacuumJob.data} onCancel={() => cancelVacuumJob.mutate()} label="Vacuum" />}
            </div>
          </TPanel>

          {/* Compact card */}
          <TPanel title="COMPACT 文件" meta="合并小文件" noBorderRight last
            action={
              <button className="btn ghost sm" style={{ height: 20, fontSize: 10 }}
                onClick={() => setShowCompactConfirm(true)}
                disabled={compactJob.data?.status === 'running'}>
                立即执行
              </button>
            }>
            <div style={{ padding: '10px 14px', display: 'flex', flexDirection: 'column', gap: 10 }}>
              <div>
                <label style={{ fontSize: 10, color: 'var(--fg-3)', display: 'block', marginBottom: 3, fontFamily: 'var(--font-mono)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>目标表（空=全部）</label>
                <TableMultiSelect selected={compactTables} onChange={setCompactTables} />
              </div>

              {/* Compact preview */}
              {compactPreview.isLoading ? <Loading label="计算预览…" /> : compactPreview.data && (
                <div>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>
                    预览 · {fmtN(compactPreview.data.partition_count)} 个分区待合并
                  </div>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--font-mono)', fontSize: 10.5 }}>
                    <thead>
                      <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
                        {['表名', '分区数'].map((h, i) => (
                          <th key={h} style={{ padding: '4px 8px', textAlign: i > 0 ? 'right' : 'left', fontSize: 9, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {(compactPreview.data.by_table || []).map((r, i) => (
                        <tr key={r.table || i} style={{ borderBottom: '1px solid var(--border-faint)', height: 22 }}>
                          <td style={{ padding: '0 8px', color: 'var(--fg)' }}>{r.table}</td>
                          <td style={{ padding: '0 8px', textAlign: 'right', color: 'var(--fg-2)', fontVariantNumeric: 'tabular-nums' }}>{r.partitions?.length ?? 0}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Job progress */}
              {compactJobId && <JobProgress job={compactJob.data} onCancel={() => cancelCompactJob.mutate()} label="Compact" />}
            </div>
          </TPanel>
        </div>

        {/* Right: Snapshots */}
        <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <TPanel title="SNAPSHOTS" meta={`${fmtN(snapshots.data?.total ?? 0)} total`} last
            action={
              <button className="btn sm" style={{ height: 20, fontSize: 10, background: 'var(--fg)', color: 'var(--bg)', borderColor: 'var(--fg)' }}
                onClick={() => snapshotCreate.mutate()}
                disabled={snapshotCreate.isPending}>
                {snapshotCreate.isPending ? '创建中…' : '+ 立即创建快照'}
              </button>
            }>
            {snapshotJobId && snapshotJob.data && (
              <div style={{ padding: '8px 14px', borderBottom: '1px solid var(--border)' }}>
                <JobProgress job={snapshotJob.data} label="Snapshot" />
              </div>
            )}
            {snapshots.isLoading ? <Loading /> :
             snapshots.error ? <ErrorState error={snapshots.error} onRetry={snapshots.refetch} /> :
             !(snapshots.data?.items?.length) ? <Empty label="暂无快照" /> : (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--font-mono)', fontSize: 10.5 }}>
                <thead style={{ position: 'sticky', top: 0, background: 'var(--surface)' }}>
                  <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
                    {['snapshot_id', '大小', '文件数', '创建时间', ''].map((h, i) => (
                      <th key={i} style={{ padding: '5px 8px', textAlign: i >= 1 && i <= 3 ? 'right' : 'left', fontSize: 9, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {snapshots.data.items.map((s, i) => (
                    <tr key={s.snapshot_id} style={{ borderBottom: '1px solid var(--border-faint)', height: 26, background: i % 2 === 0 ? 'transparent' : 'rgba(0,0,0,0.012)' }}>
                      <td style={{ padding: '0 8px', color: 'var(--fg)', fontFamily: 'var(--font-mono)', fontSize: 10 }}>
                        <span title={s.snapshot_id}>{s.snapshot_id.slice(0, 16)}…</span>
                      </td>
                      <td style={{ padding: '0 8px', textAlign: 'right', color: 'var(--fg-2)' }}>{fmtBytes(s.size)}</td>
                      <td style={{ padding: '0 8px', textAlign: 'right', color: 'var(--fg-2)', fontVariantNumeric: 'tabular-nums' }}>{fmtN(s.file_count)}</td>
                      <td style={{ padding: '0 8px', textAlign: 'right', color: 'var(--fg-3)', fontSize: 10 }}>
                        <span title={absTime(s.created_at)}>{relTime(s.created_at)}</span>
                      </td>
                      <td style={{ padding: '0 8px', textAlign: 'right' }}>
                        <button className="btn ghost sm" style={{ height: 18, fontSize: 9, color: 'var(--err)' }}
                          onClick={() => setConfirmDeleteSnap(s)}>
                          删除
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </TPanel>
        </div>
      </div>

      {/* Vacuum DangerConfirm */}
      {showVacuumConfirm && (
        <DangerConfirm
          phrase="VACUUM"
          title="⚠ 确认执行 VACUUM"
          confirmLabel="确认执行 VACUUM"
          busy={vacuumExecute.isPending}
          onCancel={() => setShowVacuumConfirm(false)}
          onConfirm={() => vacuumExecute.mutate()}
        >
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-2)', marginBottom: 12, lineHeight: 1.6 }}>
            此操作将删除超过 <b style={{ color: 'var(--fg)' }}>{vacuumDays} 天</b> 的旧版本数据。
          </div>
          <div style={{ background: 'var(--err-bg, rgba(192,57,43,0.08))', border: '1px solid var(--err)', borderRadius: 3, padding: '10px 12px', marginBottom: 12, fontFamily: 'var(--font-mono)', fontSize: 11 }}>
            <div style={{ color: 'var(--err)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>影响范围</div>
            <div style={{ color: 'var(--fg)', lineHeight: 1.6 }}>
              · 删除 <b>{fmtN(vacuumPreview.data?.file_count ?? 0)} 个文件</b> 跨 <b>{(vacuumPreview.data?.by_table || []).length} 张表</b><br />
              · 释放约 <b>{fmtBytes(vacuumPreview.data?.total_bytes ?? 0)}</b><br />
              · 删除后<b style={{ color: 'var(--err)' }}>不可恢复</b>
            </div>
          </div>
          {vacuumExecute.error && (
            <div style={{ marginBottom: 8, color: 'var(--err)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>
              ✕ {vacuumExecute.error?.apiError?.message || '执行失败'}
            </div>
          )}
        </DangerConfirm>
      )}

      {/* Compact DangerConfirm */}
      {showCompactConfirm && (
        <DangerConfirm
          phrase="COMPACT"
          title="确认执行 COMPACT"
          confirmLabel="确认执行 COMPACT"
          busy={compactExecute.isPending}
          onCancel={() => setShowCompactConfirm(false)}
          onConfirm={() => compactExecute.mutate()}
        >
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-2)', marginBottom: 12, lineHeight: 1.6 }}>
            此操作将合并小文件以提升查询速度。
          </div>
          <div style={{ background: 'var(--info-bg, rgba(26,120,200,0.08))', border: '1px solid var(--info)', borderRadius: 3, padding: '10px 12px', marginBottom: 12, fontFamily: 'var(--font-mono)', fontSize: 11 }}>
            <div style={{ color: 'var(--info)', fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>影响范围</div>
            <div style={{ color: 'var(--fg)', lineHeight: 1.6 }}>
              · 合并 <b>{fmtN(compactPreview.data?.partition_count ?? 0)} 个分区</b> 跨 <b>{(compactPreview.data?.by_table || []).length} 张表</b><br />
              · 操作期间查询性能可能短暂下降
            </div>
          </div>
          {compactExecute.error && (
            <div style={{ marginBottom: 8, color: 'var(--err)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>
              ✕ {compactExecute.error?.apiError?.message || '执行失败'}
            </div>
          )}
        </DangerConfirm>
      )}

      {/* Delete snapshot confirm */}
      {confirmDeleteSnap && (
        <DangerConfirm
          phrase={null}
          title="删除快照"
          confirmLabel="确认删除"
          busy={snapshotDelete.isPending}
          onCancel={() => setConfirmDeleteSnap(null)}
          onConfirm={() => snapshotDelete.mutate(confirmDeleteSnap.snapshot_id)}
        >
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-2)', lineHeight: 1.6 }}>
            将删除快照 <b style={{ color: 'var(--fg)' }}>{confirmDeleteSnap.snapshot_id.slice(0, 20)}…</b>
            ({fmtBytes(confirmDeleteSnap.size)} · {fmtN(confirmDeleteSnap.file_count)} 文件)。<br />
            此操作<b style={{ color: 'var(--err)' }}>不可恢复</b>。
          </div>
        </DangerConfirm>
      )}
    </TerminalShell>
  );
}

// Inline multi-select for tables using checkboxes in a compact dropdown-ish widget
function TableMultiSelect({ selected, onChange }) {
  const [open, setOpen] = useState(false);
  const toggle = (t) => onChange(selected.includes(t) ? selected.filter(x => x !== t) : [...selected, t]);
  return (
    <div style={{ position: 'relative', display: 'inline-block' }}>
      <button
        className="btn ghost sm"
        style={{ height: 24, fontSize: 10, minWidth: 200, display: 'flex', alignItems: 'center', gap: 6 }}
        onClick={() => setOpen(o => !o)}>
        {selected.length ? selected.join(', ').slice(0, 40) : '全部表'} ▾
      </button>
      {open && (
        <>
          <div onClick={() => setOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 10 }} />
          <div style={{
            position: 'absolute', top: '100%', left: 0, zIndex: 11,
            background: 'var(--surface)', border: '1px solid var(--border-strong)',
            borderRadius: 3, padding: 8, minWidth: 220,
            boxShadow: '0 4px 20px rgba(0,0,0,0.15)',
          }}>
            <div style={{ marginBottom: 6 }}>
              <button className="btn ghost sm" style={{ height: 18, fontSize: 9 }} onClick={() => onChange([])}>清除全部</button>
            </div>
            {RAW_TABLES.map(t => (
              <label key={t} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0', fontFamily: 'var(--font-mono)', fontSize: 10.5, cursor: 'pointer', color: 'var(--fg-2)' }}>
                <input type="checkbox" checked={selected.includes(t)} onChange={() => toggle(t)} style={{ accentColor: 'var(--fg)' }} />
                {t}
              </label>
            ))}
          </div>
        </>
      )}
    </div>
  );
}

// Job progress display
function JobProgress({ job, onCancel, label }) {
  if (!job) return null;
  const m = statusMeta(job.status);
  const color = toneColor(m.tone);
  const prog = job.progress;
  const pct = prog?.total ? Math.round((prog.current / prog.total) * 100) : null;

  return (
    <div style={{
      padding: '8px 12px',
      background: job.status === 'failed' ? 'var(--err-bg, rgba(192,57,43,0.08))' : 'var(--info-bg, rgba(26,120,200,0.06))',
      border: `1px solid ${color}`, borderRadius: 3,
      fontFamily: 'var(--font-mono)', fontSize: 11,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: prog ? 6 : 0 }}>
        {job.status === 'running' && <span className="dot running" style={{ width: 6, height: 6 }} />}
        <span style={{ color }}>{m.sym} {label} · {job.status}</span>
        <span style={{ color: 'var(--fg-3)', fontSize: 10, flex: 1 }}>#{job.job_id?.slice(0, 8)}</span>
        {['running', 'pending'].includes(job.status) && onCancel && (
          <button className="btn ghost sm" style={{ height: 18, fontSize: 9, color: 'var(--err)' }} onClick={onCancel}>取消</button>
        )}
      </div>
      {prog && (
        <>
          <div style={{ height: 4, background: 'var(--border)', borderRadius: 2, overflow: 'hidden', marginBottom: 4 }}>
            <div style={{ height: '100%', background: color, width: `${pct ?? 0}%`, transition: 'width 0.3s' }} />
          </div>
          <div style={{ color: 'var(--fg-3)', fontSize: 9.5 }}>
            {fmtN(prog.current)} / {fmtN(prog.total)} {prog.unit || ''}{pct !== null && ` · ${pct}%`}
          </div>
        </>
      )}
      {job.error && (
        <div style={{ color: 'var(--err)', marginTop: 4, fontSize: 10 }}>✕ {job.error}</div>
      )}
      {job.result?.freed_bytes && (
        <div style={{ color: 'var(--ok)', marginTop: 4, fontSize: 10 }}>✓ 释放 {fmtBytes(job.result.freed_bytes)}</div>
      )}
    </div>
  );
}
