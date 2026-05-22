// 字段注册表: 3 tabs + detail drawer + migrate flow
import React, { useState, useEffect, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { TerminalShell, FilterBar, FSelect, FInput, KpiStrip } from '../components/shell';
import { DataTable, StatusChip, StatusGlyph, Drawer, Modal, Loading, ErrorState, Empty } from '../components/ui';
import { endpoints } from '../lib/api';
import { relTime, absTime, statusMeta, toneColor } from '../lib/format';
import { useAuth } from '../lib/auth';

const TABLES_8 = [
  { value: '', label: '全部表 (8)' },
  { value: 'raw.bars_daily', label: 'raw.bars_daily' },
  { value: 'raw.bars_minute', label: 'raw.bars_minute' },
  { value: 'raw.fundamentals', label: 'raw.fundamentals' },
  { value: 'raw.universe', label: 'raw.universe' },
  { value: 'raw.calendar', label: 'raw.calendar' },
  { value: 'raw.corporate_actions', label: 'raw.corporate_actions' },
  { value: 'reference.symbols', label: 'reference.symbols' },
  { value: 'reference.sectors', label: 'reference.sectors' },
];

const STATUS_OPTS = [
  { value: '', label: '全部状态' },
  { value: 'extras', label: 'extras' },
  { value: 'promoted', label: 'promoted' },
  { value: 'core', label: 'core' },
  { value: 'deprecated', label: 'deprecated' },
];

const FIELD_STATE_ORDER = ['extras', 'promoted', 'core', 'deprecated'];

// ─── Migrate Flow Modal (3 steps) ──────────────────────────────────────────
function MigrateModal({ field, onClose, onDone }) {
  const { hasRole } = useAuth();
  const [step, setStep] = useState(1); // 1=dry-run review, 2=running, 3=done
  const [dryRunData, setDryRunData] = useState(null);
  const [jobId, setJobId] = useState(null);
  const [jobData, setJobData] = useState(null);
  const [checked1, setChecked1] = useState(false);
  const [checked2, setChecked2] = useState(false);
  const [err, setErr] = useState('');
  const pollRef = useRef(null);

  const dryRunMut = useMutation({
    mutationFn: () => endpoints.migrateField(field.table_name, field.field_name, { dry_run: true }),
    onSuccess: (data) => { setDryRunData(data); },
    onError: (e) => setErr(e.apiError?.message || '干跑失败'),
  });

  const migrateMut = useMutation({
    mutationFn: () => endpoints.migrateField(field.table_name, field.field_name, { dry_run: false }),
    onSuccess: (data) => { setJobId(data.job_id); setStep(2); },
    onError: (e) => setErr(e.apiError?.message || '迁移启动失败'),
  });

  // Poll job
  useEffect(() => {
    if (!jobId || step !== 2) return;
    const poll = () => {
      endpoints.job(jobId).then(j => {
        setJobData(j);
        if (j.status === 'success' || j.status === 'failed' || j.status === 'cancelled') {
          clearInterval(pollRef.current);
          setStep(3);
          if (j.status === 'success') onDone?.();
        }
      }).catch(() => {});
    };
    poll();
    pollRef.current = setInterval(poll, 600);
    return () => clearInterval(pollRef.current);
  }, [jobId, step]);

  // Start dry run on mount
  useEffect(() => { dryRunMut.mutate(); }, []);

  const canConfirm = checked1 && checked2 && dryRunData && hasRole('admin') && !migrateMut.isPending;

  return (
    <Modal width={680} onClose={onClose}
      title={`升级到 core · ${field.table_name}.${field.field_name}`}
      footer={
        step === 1 ? (
          <>
            <button className="btn sm" data-testid="migrate-cancel-btn" onClick={onClose}>取消</button>
            <button className="btn sm danger" data-testid="migrate-confirm-btn" disabled={!canConfirm} onClick={() => migrateMut.mutate()}>
              {migrateMut.isPending ? '启动中…' : '确认执行迁移'}
            </button>
          </>
        ) : step === 3 ? (
          <button className="btn sm" onClick={onClose}>关闭</button>
        ) : null
      }>
      {step === 1 && (
        <div>
          {dryRunMut.isLoading && <Loading label="正在执行干跑…" />}
          {err && <div style={{ color: 'var(--err)', fontFamily: 'var(--font-mono)', fontSize: 11, marginBottom: 12 }}>✕ {err}</div>}
          {dryRunData && (
            <>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-3)', marginBottom: 12 }}>
                字段: <b style={{ color: 'var(--fg)' }}>{dryRunData.field}</b> · 类型: <b style={{ color: 'var(--info)' }}>{dryRunData.dtype}</b>
              </div>

              {/* Impact summary */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12, marginBottom: 16 }}>
                {[
                  { label: 'affected_files', value: dryRunData.affected_files, tone: 'warn' },
                  { label: 'total_rows', value: dryRunData.total_rows?.toLocaleString() || '—' },
                  { label: 'est_duration', value: dryRunData.estimated_duration_sec ? `${dryRunData.estimated_duration_sec}s` : '—' },
                  { label: 'disk_io', value: dryRunData.estimated_disk_io_mb ? `${dryRunData.estimated_disk_io_mb} MB` : '—' },
                ].map(c => (
                  <div key={c.label} style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 4, padding: '8px 12px' }}>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{c.label}</div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 18, fontWeight: 500, color: toneColor(c.tone), marginTop: 4 }}>{c.value}</div>
                  </div>
                ))}
              </div>

              {/* Schema diff */}
              {(dryRunData.current_schema || dryRunData.after_schema) && (
                <div style={{ marginBottom: 14 }}>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>SCHEMA CHANGE</div>
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
                    {[['当前', dryRunData.current_schema, 'var(--fg-2)'], ['迁移后', dryRunData.after_schema, 'var(--ok)']].map(([label, lines, col]) => (
                      <div key={label}>
                        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', marginBottom: 4 }}>{label}</div>
                        <pre style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: col, background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3, padding: '6px 10px', margin: 0 }}>
                          {(lines || []).join('\n')}
                        </pre>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Cast failure sample */}
              {(dryRunData.cast_failure_sample || []).length > 0 && (
                <div style={{ marginBottom: 14 }}>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--err)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>
                    CAST FAILURE SAMPLE ({dryRunData.cast_failure_sample.length})
                  </div>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                    <thead>
                      <tr style={{ borderBottom: '1px solid var(--border)' }}>
                        {['batch_id', 'value', 'would_be'].map(c => (
                          <th key={c} style={{ textAlign: 'left', padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', fontWeight: 500 }}>{c}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {dryRunData.cast_failure_sample.map((row, i) => (
                        <tr key={i} style={{ borderBottom: '1px solid var(--border-faint)' }}>
                          <td style={{ padding: '3px 8px', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-2)' }}>{row.batch_id}</td>
                          <td style={{ padding: '3px 8px', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--err)' }}>{String(row.value)}</td>
                          <td style={{ padding: '3px 8px', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--warn)' }}>{String(row.would_be)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Checkboxes */}
              <div style={{ marginTop: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11.5, cursor: 'pointer', fontFamily: 'var(--font-mono)' }}>
                  <input type="checkbox" data-testid="migrate-ack-1" checked={checked1} onChange={e => setChecked1(e.target.checked)} style={{ accentColor: 'var(--fg)', width: 14, height: 14 }} />
                  已审阅类型转换样本，确认无阻断性错误
                </label>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11.5, cursor: 'pointer', fontFamily: 'var(--font-mono)' }}>
                  <input type="checkbox" data-testid="migrate-ack-2" checked={checked2} onChange={e => setChecked2(e.target.checked)} style={{ accentColor: 'var(--fg)', width: 14, height: 14 }} />
                  已知晓此操作不可回滚
                </label>
              </div>
              {!hasRole('admin') && (
                <div style={{ color: 'var(--err)', fontSize: 11, marginTop: 10 }}>需要 admin 权限才能执行迁移到 core</div>
              )}
            </>
          )}
        </div>
      )}

      {step === 2 && (
        <div style={{ padding: 16 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 12, marginBottom: 16 }}>
            <span className="dot running" style={{ width: 8, height: 8, display: 'inline-block' }} /> 迁移进行中…
          </div>
          {jobData && (
            <>
              {jobData.progress && (
                <div style={{ marginBottom: 16 }}>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', marginBottom: 6 }}>
                    {jobData.progress.current} / {jobData.progress.total} {jobData.progress.unit || 'files'}
                  </div>
                  <div style={{ height: 8, background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 2, overflow: 'hidden' }}>
                    <div style={{
                      height: '100%', background: 'var(--info)',
                      width: `${Math.min(100, (jobData.progress.current / jobData.progress.total) * 100)}%`,
                      transition: 'width 0.3s',
                    }} />
                  </div>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', marginTop: 4 }}>
                    {((jobData.progress.current / jobData.progress.total) * 100).toFixed(1)}%
                  </div>
                </div>
              )}
              {(jobData.log || []).length > 0 && (
                <pre style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-2)', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3, padding: 10, maxHeight: 200, overflow: 'auto' }}>
                  {jobData.log.slice(-20).join('\n')}
                </pre>
              )}
            </>
          )}
        </div>
      )}

      {step === 3 && (
        <div style={{ padding: 20, textAlign: 'center' }}>
          {jobData?.status === 'success' ? (
            <>
              <div style={{ fontSize: 32, color: 'var(--ok)', marginBottom: 12 }}>●</div>
              <div style={{ fontFamily: 'var(--font-mono)', color: 'var(--ok)', fontSize: 14 }}>迁移成功</div>
              <div style={{ color: 'var(--fg-3)', fontSize: 11, marginTop: 8 }}>字段已升级到 core</div>
            </>
          ) : (
            <>
              <div style={{ fontSize: 32, color: 'var(--err)', marginBottom: 12 }}>✕</div>
              <div style={{ fontFamily: 'var(--font-mono)', color: 'var(--err)', fontSize: 14 }}>迁移失败</div>
              <div style={{ color: 'var(--fg-2)', fontSize: 11, marginTop: 8 }}>{jobData?.error || '未知错误'}</div>
            </>
          )}
        </div>
      )}
    </Modal>
  );
}

// ─── Field Detail Drawer ────────────────────────────────────────────────────
function FieldDrawer({ field, onClose, onRefresh }) {
  const { hasRole } = useAuth();
  const qc = useQueryClient();
  const [showMigrate, setShowMigrate] = useState(false);
  const [newConsumer, setNewConsumer] = useState('');
  const [editMode, setEditMode] = useState(false);
  const [editForm, setEditForm] = useState({
    description: field.description || '',
    unit: field.unit || '',
    authoritative_source: field.authoritative_source || '',
  });
  const [editErr, setEditErr] = useState('');

  const updateMut = useMutation({
    mutationFn: (body) => endpoints.updateField(field.table_name, field.field_name, body),
    onSuccess: () => { qc.invalidateQueries(['fields']); setEditMode(false); },
    onError: (e) => setEditErr(e.apiError?.message || '更新失败'),
  });

  const promoteMut = useMutation({
    mutationFn: (body) => endpoints.promoteField(field.table_name, field.field_name, body),
    onSuccess: () => { qc.invalidateQueries(['fields']); onRefresh?.(); },
  });

  const deprecateMut = useMutation({
    mutationFn: () => endpoints.deprecateField(field.table_name, field.field_name),
    onSuccess: () => { qc.invalidateQueries(['fields']); onRefresh?.(); },
  });

  const addConsumerMut = useMutation({
    mutationFn: (consumer) => endpoints.addConsumer(field.table_name, field.field_name, consumer),
    onSuccess: () => { qc.invalidateQueries(['fields']); setNewConsumer(''); },
  });

  const stateMeta = statusMeta(field.status);
  const canPromoteToPromoted = field.status === 'extras' && hasRole('editor');
  const canPromoteToExtras = field.status === 'promoted' && hasRole('editor');
  const canDeprecate = field.status !== 'deprecated' && field.status !== 'core' && hasRole('editor');
  const canMigrate = field.status === 'promoted' && hasRole('admin');
  const hasBlockers = (field.promote_blockers || []).length > 0;

  const stateLine = FIELD_STATE_ORDER.map(st => {
    const isCurrent = field.status === st;
    const m = statusMeta(st);
    let ts = null;
    if (st === 'promoted') ts = field.promoted_at;
    if (st === 'core') ts = field.core_at;
    if (st === 'deprecated') ts = field.deprecated_at;
    return { st, isCurrent, m, ts };
  });

  return (
    <>
      <Drawer width={640} onClose={onClose}>
        <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', background: 'var(--surface-2)', display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 600, color: 'var(--fg)' }}>{field.field_name}</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)', marginTop: 2 }}>{field.table_name} · {field.data_type}</div>
          </div>
          <StatusChip status={field.status} size={11} />
          <button onClick={onClose} style={{ color: 'var(--fg-3)', background: 'none', border: 'none', cursor: 'pointer', fontSize: 18, padding: '0 4px' }}>✕</button>
        </div>

        <div className="scroll" style={{ flex: 1, minHeight: 0, padding: '16px 18px', display: 'flex', flexDirection: 'column', gap: 18 }}>
          {/* State machine timeline */}
          <div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 10 }}>STATE MACHINE</div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 0 }}>
              {stateLine.map(({ st, isCurrent, m, ts }, i) => (
                <React.Fragment key={st}>
                  <div style={{ textAlign: 'center' }}>
                    <div style={{
                      width: 32, height: 32, borderRadius: '50%', border: `2px solid ${isCurrent ? toneColor(m.tone) : 'var(--border)'}`,
                      display: 'grid', placeItems: 'center',
                      background: isCurrent ? `${toneColor(m.tone)}18` : 'var(--surface-2)',
                      fontFamily: 'var(--font-mono)', fontSize: 15,
                      color: isCurrent ? toneColor(m.tone) : 'var(--fg-3)',
                      margin: '0 auto',
                    }}>{m.sym}</div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: isCurrent ? toneColor(m.tone) : 'var(--fg-3)', marginTop: 4, fontWeight: isCurrent ? 600 : 400 }}>{st}</div>
                    {ts && <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--fg-4)', marginTop: 2 }}>{relTime(ts)}</div>}
                  </div>
                  {i < stateLine.length - 1 && (
                    <div style={{ flex: 1, height: 2, background: 'var(--border)', margin: '0 6px', marginBottom: 20 }} />
                  )}
                </React.Fragment>
              ))}
            </div>
          </div>

          {/* Basic info */}
          <div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>基本信息</div>
            {editMode ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                {[['description', '描述'], ['unit', '单位'], ['authoritative_source', '权威源']].map(([k, label]) => (
                  <div key={k} style={{ display: 'grid', gridTemplateColumns: '120px 1fr', gap: 8, alignItems: 'center' }}>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)' }}>{label}</span>
                    <input value={editForm[k]} onChange={e => setEditForm(f => ({ ...f, [k]: e.target.value }))}
                      style={{ height: 26, padding: '0 8px', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)' }} />
                  </div>
                ))}
                {editErr && <div style={{ color: 'var(--err)', fontSize: 11 }}>{editErr}</div>}
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="btn sm" onClick={() => updateMut.mutate(editForm)} disabled={updateMut.isPending}>保存</button>
                  <button className="btn ghost sm" onClick={() => setEditMode(false)}>取消</button>
                </div>
              </div>
            ) : (
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11.5 }}>
                {[
                  ['field_name', field.field_name],
                  ['table_name', field.table_name],
                  ['data_type', field.data_type],
                  ['status', field.status],
                  ['description', field.description || '—'],
                  ['unit', field.unit || '—'],
                  ['authoritative_source', field.authoritative_source || '—'],
                  ['age_days', `${field.age_days ?? '—'} days`],
                  ['first_seen', field.first_seen_at ? relTime(field.first_seen_at) : '—'],
                ].map(([k, v]) => (
                  <div key={k} style={{ display: 'grid', gridTemplateColumns: '150px 1fr', padding: '4px 0', borderBottom: '1px solid var(--border-faint)' }}>
                    <span style={{ color: 'var(--fg-3)' }}>{k}</span>
                    <span style={{ color: 'var(--fg)' }}>{v}</span>
                  </div>
                ))}
                {hasRole('editor') && (
                  <button className="btn ghost sm" onClick={() => setEditMode(true)} style={{ marginTop: 10, height: 24, fontSize: 10 }}>编辑信息</button>
                )}
              </div>
            )}
          </div>

          {/* Consumers */}
          <div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>
              CONSUMERS ({(field.consumers || []).length})
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
              {(field.consumers || []).map(c => (
                <span key={c} style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 2, padding: '2px 8px', color: 'var(--fg-2)' }}>{c}</span>
              ))}
              {(field.consumers || []).length === 0 && <span style={{ color: 'var(--fg-4)', fontSize: 11 }}>暂无 consumers</span>}
            </div>
            {hasRole('editor') && (
              <div style={{ display: 'flex', gap: 8 }}>
                <input value={newConsumer} onChange={e => setNewConsumer(e.target.value)}
                  placeholder="新增 consumer 名称"
                  onKeyDown={e => { if (e.key === 'Enter' && newConsumer.trim()) addConsumerMut.mutate(newConsumer.trim()); }}
                  style={{ flex: 1, height: 26, padding: '0 8px', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)' }} />
                <button className="btn ghost sm" onClick={() => newConsumer.trim() && addConsumerMut.mutate(newConsumer.trim())}
                  disabled={!newConsumer.trim() || addConsumerMut.isPending} style={{ height: 26, fontSize: 10 }}>+ 添加</button>
              </div>
            )}
          </div>

          {/* Promote blockers */}
          {hasBlockers && (
            <div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--err)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>PROMOTE BLOCKERS</div>
              {(field.promote_blockers || []).map((b, i) => (
                <div key={i} style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--err)', padding: '3px 0' }}>✕ {b}</div>
              ))}
            </div>
          )}

          {/* Action buttons */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>操作</div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8 }}>
              {canPromoteToPromoted && (
                <button className="btn sm" onClick={() => promoteMut.mutate({ status: 'promoted' })}
                  disabled={hasBlockers || promoteMut.isPending}
                  title={hasBlockers ? (field.promote_blockers || []).join(' / ') : '升级到 promoted'}
                  style={{ opacity: hasBlockers ? 0.5 : 1 }}>
                  {promoteMut.isPending ? '升级中…' : '↑ 升级到 promoted'}
                </button>
              )}
              {canPromoteToExtras && (
                <button className="btn ghost sm" onClick={() => promoteMut.mutate({ status: 'extras' })}
                  disabled={promoteMut.isPending}>
                  ↓ 降级回 extras
                </button>
              )}
              {canMigrate && (
                <button className="btn sm" data-testid="field-migrate-btn" onClick={() => setShowMigrate(true)}
                  style={{ background: 'var(--info)', color: 'var(--bg)', borderColor: 'var(--info)' }}>
                  ↑↑ 升级到 core (迁移)
                </button>
              )}
              {canDeprecate && (
                <button className="btn ghost sm" onClick={() => deprecateMut.mutate()}
                  disabled={deprecateMut.isPending}
                  style={{ color: 'var(--err)', borderColor: 'var(--err)' }}>
                  {deprecateMut.isPending ? '处理中…' : '弃用'}
                </button>
              )}
              {field.status === 'deprecated' && hasRole('editor') && (
                <button className="btn ghost sm" onClick={() => promoteMut.mutate({ status: 'extras' })}
                  disabled={promoteMut.isPending}>
                  重新激活 (→ extras)
                </button>
              )}
            </div>
          </div>
        </div>
      </Drawer>

      {showMigrate && (
        <MigrateModal field={field} onClose={() => setShowMigrate(false)}
          onDone={() => { setShowMigrate(false); qc.invalidateQueries(['fields']); }} />
      )}
    </>
  );
}

// ─── 字段列表 tab ────────────────────────────────────────────────────────────
function FieldListTab({ tableFilter, statusFilter, setTableFilter, setStatusFilter }) {
  const [selected, setSelected] = useState(null);
  const [search, setSearch] = useState('');

  const fieldsQ = useQuery({
    queryKey: ['fields', { table: tableFilter, status: statusFilter }],
    queryFn: () => endpoints.fields({ table: tableFilter || undefined, status: statusFilter || undefined }),
    staleTime: 30000,
  });

  const items = (fieldsQ.data?.items || []).filter(f =>
    !search || f.field_name.includes(search) || (f.description || '').includes(search)
  );

  const cols = [
    {
      key: 'field_name', label: 'column', mono: true, color: 'var(--fg)',
      render: v => <span style={{ fontWeight: 500 }}>{v}</span>,
    },
    { key: 'table_name', label: 'table', mono: true, color: 'var(--fg-2)', fontSize: 10.5 },
    {
      key: 'status', label: 'status', width: 90,
      render: v => <StatusChip status={v} size={10} />,
    },
    { key: 'data_type', label: 'type', mono: true, color: 'var(--fg-2)', fontSize: 10 },
    { key: 'unit', label: 'unit', mono: true, color: 'var(--fg-3)', fontSize: 10.5, render: v => v || '—' },
    {
      key: 'age_days', label: 'age', align: 'right', mono: true,
      render: (v, row) => (
        <span style={{ color: row.status === 'extras' && v > 90 ? 'var(--err)' : 'var(--fg-3)' }}>
          {v ?? '—'}d
        </span>
      ),
    },
    {
      key: 'consumers', label: 'consumers', align: 'right', mono: true,
      render: v => <span style={{ color: 'var(--fg-3)' }}>{(v || []).length}</span>,
    },
    {
      key: '_note', label: 'note', fontSize: 10.5,
      render: (_, row) => {
        if (row.promote_blockers?.length > 0) {
          return <span style={{ color: 'var(--err)', fontSize: 10 }}>✕ {row.promote_blockers[0]}</span>;
        }
        if (row.can_promote_to?.length > 0 && row.status !== 'core') {
          return <span style={{ color: 'var(--info)', fontSize: 10 }}>→ can promote to {row.can_promote_to.join(', ')}</span>;
        }
        return <span style={{ color: 'var(--fg-4)' }}>—</span>;
      },
    },
  ];

  if (fieldsQ.isLoading) return <Loading />;
  if (fieldsQ.error) return <ErrorState error={fieldsQ.error} onRetry={fieldsQ.refetch} />;

  return (
    <>
      <div style={{ padding: '6px 14px', borderBottom: '1px solid var(--border)', background: 'var(--surface)', display: 'flex', gap: 8, alignItems: 'center', flexShrink: 0 }}>
        <FInput label="search" placeholder="field_name / desc" width={180} value={search} onChange={setSearch} />
        <FSelect label="table" value={tableFilter} options={TABLES_8} onChange={setTableFilter} width={180} />
        <FSelect label="status" value={statusFilter} options={STATUS_OPTS} onChange={setStatusFilter} width={130} />
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', marginLeft: 'auto' }}>
          {items.length} fields
        </span>
      </div>
      <div style={{ flex: 1, minHeight: 0, overflow: 'auto', position: 'relative' }}>
        {items.length === 0 ? (
          <Empty label="暂无字段数据" />
        ) : (
          <DataTable columns={cols} rows={items} rowKey={r => `${r.table_name}.${r.field_name}`}
            onRowClick={r => setSelected(r)} height={28}
            rowStyle={(r) => r.status === 'deprecated' ? { opacity: 0.55 } : {}} />
        )}
        {selected && (
          <FieldDrawer field={selected} onClose={() => setSelected(null)} onRefresh={() => setSelected(null)} />
        )}
      </div>
    </>
  );
}

// ─── 时间旅行查询 tab ─────────────────────────────────────────────────────────
function TimeTravelTab() {
  const [asOf, setAsOf] = useState('');
  const [table, setTable] = useState('raw.bars_daily');
  const [result, setResult] = useState(null);

  const travelMut = useMutation({
    mutationFn: () => endpoints.fieldsTimeTravel(asOf, table),
    onSuccess: (data) => setResult(data),
  });

  const handleRun = () => {
    if (!asOf) return;
    travelMut.mutate();
  };

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      {/* Config bar */}
      <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', background: 'var(--surface)', display: 'flex', gap: 10, alignItems: 'center', flexShrink: 0 }}>
        <FInput label="as_of" placeholder="YYYY-MM-DD" value={asOf} onChange={setAsOf} width={130} />
        <FSelect label="table" value={table} options={TABLES_8.slice(1)} onChange={setTable} width={200} />
        <button className="btn sm" onClick={handleRun} disabled={!asOf || travelMut.isPending}
          style={{ background: 'var(--fg)', color: 'var(--bg)', borderColor: 'var(--fg)' }}>
          {travelMut.isPending ? '查询中…' : '查询'}
        </button>
      </div>

      {travelMut.isPending && <Loading label="正在查询时间旅行快照…" />}
      {travelMut.isError && <ErrorState error={travelMut.error} onRetry={handleRun} />}

      {!result && !travelMut.isPending && (
        <div style={{ flex: 1, display: 'grid', placeItems: 'center' }}>
          <div style={{ textAlign: 'center', color: 'var(--fg-3)', fontFamily: 'var(--font-mono)' }}>
            <div style={{ fontSize: 28, marginBottom: 10, color: 'var(--fg-4)' }}>⟳</div>
            <div>输入日期 + 选择表后点击查询</div>
          </div>
        </div>
      )}

      {result && (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div style={{ padding: '8px 14px', background: 'var(--surface-2)', borderBottom: '1px solid var(--border)', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-3)', flexShrink: 0 }}>
            快照时间: <b style={{ color: 'var(--fg)' }}>{result.as_of}</b> · 表: <b style={{ color: 'var(--fg)' }}>{result.table}</b> · {result.fields?.length ?? 0} 个字段
          </div>

          {/* Added / Removed / Changed */}
          {((result.added || []).length > 0 || (result.removed || []).length > 0 || (result.changed || []).length > 0) && (
            <div style={{ padding: '12px 14px', borderBottom: '1px solid var(--border)', display: 'flex', gap: 20, flexShrink: 0 }}>
              {result.added?.length > 0 && (
                <div>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--ok)', textTransform: 'uppercase', marginBottom: 4 }}>+ 新增 ({result.added.length})</div>
                  {result.added.map(f => <div key={f} style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ok)' }}>{f}</div>)}
                </div>
              )}
              {result.removed?.length > 0 && (
                <div>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--err)', textTransform: 'uppercase', marginBottom: 4 }}>- 移除 ({result.removed.length})</div>
                  {result.removed.map(f => <div key={f} style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--err)' }}>{f}</div>)}
                </div>
              )}
              {result.changed?.length > 0 && (
                <div>
                  <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--warn)', textTransform: 'uppercase', marginBottom: 4 }}>~ 变更 ({result.changed.length})</div>
                  {result.changed.map(c => (
                    <div key={c.field} style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--warn)' }}>
                      {c.field}: {c.from} → {c.to}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
            <DataTable
              columns={[
                { key: 'field_name', label: 'field', mono: true, color: 'var(--fg)', render: v => <span style={{ fontWeight: 500 }}>{v}</span> },
                { key: 'data_type', label: 'type', mono: true, color: 'var(--fg-2)', fontSize: 10 },
                {
                  key: 'status_at_date', label: '状态 (as of)', render: v => <StatusChip status={v} size={10} />,
                },
              ]}
              rows={result.fields || []}
              rowKey={r => r.field_name}
              height={28}
            />
          </div>
        </div>
      )}
    </div>
  );
}

// ─── 陈旧 extras tab ──────────────────────────────────────────────────────────
function StaleExtrasTab() {
  const { hasRole } = useAuth();
  const qc = useQueryClient();
  const [days, setDays] = useState('90');

  const staleQ = useQuery({
    queryKey: ['fieldsStale', days],
    queryFn: () => endpoints.fieldsStale(parseInt(days) || 90),
    staleTime: 60000,
  });

  const promoteMut = useMutation({
    mutationFn: ({ table, field }) => endpoints.promoteField(table, field, { status: 'promoted' }),
    onSuccess: () => qc.invalidateQueries(['fieldsStale', days]),
  });

  const deprecateMut = useMutation({
    mutationFn: ({ table, field }) => endpoints.deprecateField(table, field),
    onSuccess: () => qc.invalidateQueries(['fieldsStale', days]),
  });

  const items = staleQ.data?.items || [];

  const cols = [
    { key: 'field_name', label: 'field', mono: true, color: 'var(--fg)', render: v => <span style={{ fontWeight: 500 }}>{v}</span> },
    { key: 'table_name', label: 'table', mono: true, color: 'var(--fg-2)', fontSize: 10.5 },
    { key: 'data_type', label: 'type', mono: true, color: 'var(--fg-3)', fontSize: 10 },
    {
      key: 'age_days', label: 'age', align: 'right', mono: true,
      render: v => <span style={{ color: 'var(--err)', fontWeight: 500 }}>{v}d</span>,
    },
    {
      key: '_actions', label: '',
      render: (_, row) => hasRole('editor') ? (
        <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
          <button className="btn ghost sm" style={{ height: 20, fontSize: 10, color: 'var(--info)' }}
            onClick={e => { e.stopPropagation(); promoteMut.mutate({ table: row.table_name, field: row.field_name }); }}
            disabled={promoteMut.isPending}>升级</button>
          <button className="btn ghost sm" style={{ height: 20, fontSize: 10, color: 'var(--err)' }}
            onClick={e => { e.stopPropagation(); deprecateMut.mutate({ table: row.table_name, field: row.field_name }); }}
            disabled={deprecateMut.isPending}>弃用</button>
        </div>
      ) : null,
    },
  ];

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
      <div style={{ padding: '8px 14px', borderBottom: '1px solid var(--border)', background: 'var(--surface)', display: 'flex', gap: 10, alignItems: 'center', flexShrink: 0 }}>
        <FInput label="陈旧阈值 (days)" placeholder="90" value={days} onChange={setDays} width={80} />
        <button className="btn ghost sm" onClick={() => staleQ.refetch()} style={{ height: 24, fontSize: 10 }}>刷新</button>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--warn)', marginLeft: 'auto' }}>
          {items.length} 个陈旧 extras (超过 {staleQ.data?.days ?? days} 天)
        </span>
      </div>
      {staleQ.isLoading ? <Loading /> : staleQ.error ? <ErrorState error={staleQ.error} onRetry={staleQ.refetch} /> : (
        items.length === 0
          ? <Empty label={`无超过 ${days} 天的陈旧 extras`} hint="适当降低阈值可查看更多" />
          : <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
              <DataTable columns={cols} rows={items} rowKey={r => `${r.table_name}.${r.field_name}`} height={28} />
            </div>
      )}
    </div>
  );
}

// ─── Main Fields page ────────────────────────────────────────────────────────
export default function Fields() {
  const [mainTab, setMainTab] = useState('list');
  const [tableFilter, setTableFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');

  const fieldsQ = useQuery({
    queryKey: ['fields', {}],
    queryFn: () => endpoints.fields({}),
    staleTime: 60000,
  });

  const allItems = fieldsQ.data?.items || [];
  const total = allItems.length;
  const core = allItems.filter(f => f.status === 'core').length;
  const promoted = allItems.filter(f => f.status === 'promoted').length;
  const extras = allItems.filter(f => f.status === 'extras').length;
  const candidates = allItems.filter(f => f.status === 'extras' && (f.can_promote_to || []).length > 0).length;

  const tabs = [
    { k: 'list', l: '字段列表' },
    { k: 'timetravel', l: '时间旅行查询' },
    { k: 'stale', l: '陈旧 extras' },
  ];

  return (
    <TerminalShell
      module="data" current="fields"
      path={['data_mgmt/', 'fields']}
      fkeys={[
        { k: 'F1', l: '字段列表', onClick: () => setMainTab('list'), active: mainTab === 'list' },
        { k: 'F2', l: '时间旅行', onClick: () => setMainTab('timetravel'), active: mainTab === 'timetravel' },
        { k: 'F3', l: '陈旧', onClick: () => setMainTab('stale'), active: mainTab === 'stale' },
      ]}
      title="字段注册表"
      subtitle={`fields · ${total} total · ${core} core · ${promoted} promoted · ${extras} extras`}
    >
      {/* KPI Strip */}
      <KpiStrip cells={[
        { label: 'TOTAL FIELDS', value: String(total), sub: `${(fieldsQ.data?.total || total)} total` },
        { label: 'CORE', value: String(core), sub: total ? `${((core / total) * 100).toFixed(1)}%` : '—', tone: 'ok' },
        { label: 'PROMOTED', value: String(promoted), sub: total ? `${((promoted / total) * 100).toFixed(1)}%` : '—', tone: 'info' },
        { label: 'EXTRAS', value: String(extras), sub: total ? `${((extras / total) * 100).toFixed(1)}%` : '—', tone: extras > 0 ? 'warn' : undefined },
        { label: 'PROMOTE CANDIDATES', value: String(candidates), sub: 'can promote', tone: candidates > 0 ? 'warn' : undefined },
      ]} height={56} />

      {/* Tab bar */}
      <div style={{
        height: 30, padding: '0 14px',
        background: 'var(--surface-2)',
        borderBottom: '1px solid var(--border)',
        display: 'flex', alignItems: 'flex-end',
        flexShrink: 0,
      }}>
        {tabs.map(t => (
          <button key={t.k} onClick={() => setMainTab(t.k)} style={{
            height: 30, padding: '0 14px',
            background: mainTab === t.k ? 'var(--surface)' : 'transparent',
            border: 'none',
            borderTop: mainTab === t.k ? '1px solid var(--fg)' : '1px solid transparent',
            borderLeft: mainTab === t.k ? '1px solid var(--border)' : 'none',
            borderRight: mainTab === t.k ? '1px solid var(--border)' : 'none',
            fontFamily: 'var(--font-mono)', fontSize: 11,
            color: mainTab === t.k ? 'var(--fg)' : 'var(--fg-3)',
            cursor: 'pointer', marginBottom: -1,
          }}>{t.l}</button>
        ))}
      </div>

      {mainTab === 'list' && (
        <FieldListTab tableFilter={tableFilter} statusFilter={statusFilter}
          setTableFilter={setTableFilter} setStatusFilter={setStatusFilter} />
      )}
      {mainTab === 'timetravel' && <TimeTravelTab />}
      {mainTab === 'stale' && <StaleExtrasTab />}
    </TerminalShell>
  );
}
