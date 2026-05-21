// 批次详情 – full detail page for a single batch
import React, { useState } from 'react';
import { useNavigate, useParams, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { TerminalShell, TPanel } from '../components/shell';
import { StatusChip, StatusGlyph, CopyableId, Loading, ErrorState, Empty, Modal, DangerConfirm } from '../components/ui';
import { endpoints } from '../lib/api';
import { useAuth } from '../lib/auth';
import { fmtCompact, fmtDuration, fmtN, absTime, relTime, statusMeta, toneColor } from '../lib/format';

const TABS = [
  { id: 'overview', l: '基本信息' },
  { id: 'preview', l: '写入预览' },
  { id: 'quality', l: '质量检查' },
  { id: 'lineage', l: 'Lineage' },
  { id: 'params', l: 'Parameters' },
];

export default function BatchDetail() {
  const { batchId } = useParams();
  const nav = useNavigate();
  const qc = useQueryClient();
  const { hasRole } = useAuth();
  const [tab, setTab] = useState('overview');
  const [showInvalidate, setShowInvalidate] = useState(false);
  const [invalidateReason, setInvalidateReason] = useState('');
  const [showSupersede, setShowSupersede] = useState(false);
  const [newBatchId, setNewBatchId] = useState('');

  const batchQ = useQuery({
    queryKey: ['batch', batchId],
    queryFn: () => endpoints.batch(batchId),
    enabled: !!batchId,
  });

  const rowsQ = useQuery({
    queryKey: ['batchRows', batchId],
    queryFn: () => endpoints.batchRows(batchId, 100),
    enabled: tab === 'preview' && !!batchId,
  });

  const qualityQ = useQuery({
    queryKey: ['batchQuality', batchId],
    queryFn: () => endpoints.batchQuality(batchId),
    enabled: tab === 'quality' && !!batchId,
  });

  const lineageQ = useQuery({
    queryKey: ['batchLineage', batchId],
    queryFn: () => endpoints.batchLineage(batchId),
    enabled: tab === 'lineage' && !!batchId,
  });

  const invalidateMut = useMutation({
    mutationFn: () => endpoints.invalidateBatch(batchId, invalidateReason),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['batch', batchId] });
      qc.invalidateQueries({ queryKey: ['batches'] });
      setShowInvalidate(false);
      setInvalidateReason('');
    },
  });

  const supersedeMut = useMutation({
    mutationFn: () => endpoints.supersede(batchId, newBatchId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['batch', batchId] });
      qc.invalidateQueries({ queryKey: ['batches'] });
      setShowSupersede(false);
      setNewBatchId('');
    },
  });

  const b = batchQ.data;

  if (batchQ.isLoading) {
    return (
      <TerminalShell module="data" current="batches" path={['data_mgmt/', 'batches/', batchId || '…']} title="批次详情">
        <Loading />
      </TerminalShell>
    );
  }
  if (batchQ.error) {
    return (
      <TerminalShell module="data" current="batches" path={['data_mgmt/', 'batches/', batchId || '…']} title="批次详情">
        <ErrorState error={batchQ.error} onRetry={batchQ.refetch} />
      </TerminalShell>
    );
  }

  const sm = statusMeta(b?.status);
  const canEdit = hasRole('editor');
  const canInvalidate = canEdit && b?.status !== 'invalidated' && b?.status !== 'running';
  const canSupersede = canEdit && b?.status === 'success';

  const headerCells = [
    { l: 'STATUS', v: b?.status?.toUpperCase(), tone: sm.tone },
    { l: 'TABLE', v: b?.target_table },
    { l: 'SOURCE', v: b?.source },
    { l: 'ROWS', v: b?.row_count != null ? fmtN(b.row_count) : '—' },
    { l: 'PARTITIONS', v: b?.partition_count ?? '—' },
    { l: 'DURATION', v: fmtDuration(b?.duration_sec) },
    { l: 'STARTED', v: b?.started_at ? absTime(b.started_at).slice(11) : '—', sub: relTime(b?.started_at) },
    { l: 'FINISHED', v: b?.finished_at ? absTime(b.finished_at).slice(11) : b?.status === 'running' ? '运行中…' : '—' },
  ];

  function copyRerunCommand() {
    const cmd = `python -m zjq.ingest run --batch-id ${batchId}`;
    navigator.clipboard?.writeText(cmd);
  }

  const qualityCount = qualityQ.data?.total ?? 0;
  const qualityWarn = qualityQ.data?.by_level?.warn ?? 0;
  const qualityErr = qualityQ.data?.by_level?.error ?? 0;

  return (
    <TerminalShell
      module="data"
      current="batches"
      path={['data_mgmt/', 'batches/', batchId]}
      fkeys={[
        { k: 'F1', l: '作废', active: showInvalidate, onClick: () => canInvalidate && setShowInvalidate(true) },
        { k: 'F2', l: '关联替换', onClick: () => canSupersede && setShowSupersede(true) },
        { k: 'F3', l: '重跑', onClick: copyRerunCommand },
        { k: '⌘C', l: '复制 ID' },
      ]}
      title={batchId}
      subtitle={`${b?.target_table} ← ${b?.source} · started ${b?.started_at ? absTime(b.started_at) : '—'}`}
      badge={b ? <span className={`chip ${sm.tone}`} style={{ fontSize: 10 }}>{b.status.toUpperCase()}</span> : null}
      actions={
        <>
          <button className="btn ghost sm" onClick={copyRerunCommand}>复制重跑命令</button>
          <button
            className="btn ghost sm"
            style={{ color: 'var(--warn)', borderColor: canSupersede ? 'var(--warn)' : undefined }}
            disabled={!canSupersede}
            onClick={() => setShowSupersede(true)}
          >关联替换</button>
          <button
            className="btn ghost sm"
            style={{ color: 'var(--err)', borderColor: canInvalidate ? 'var(--err)' : undefined }}
            disabled={!canInvalidate}
            onClick={() => setShowInvalidate(true)}
          >⚠ 作废</button>
          <button className="btn ghost sm" onClick={() => nav(-1)}>关闭</button>
        </>
      }
    >
      {/* Header strip */}
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(8, 1fr)',
        background: 'var(--surface)', borderBottom: '1px solid var(--border-strong)',
        height: 58, flexShrink: 0,
      }}>
        {headerCells.map((s, i) => (
          <div key={i} style={{
            padding: '6px 14px',
            borderRight: i < 7 ? '1px solid var(--border-faint)' : 'none',
            display: 'flex', flexDirection: 'column', justifyContent: 'space-between',
          }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{s.l}</span>
            <div>
              <div style={{
                fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 500,
                color: s.tone ? toneColor(s.tone) : 'var(--fg)',
                fontVariantNumeric: 'tabular-nums',
              }}>{s.v}</div>
              {s.sub && <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', marginTop: 2 }}>{s.sub}</div>}
            </div>
          </div>
        ))}
      </div>

      {/* Tab bar */}
      <div style={{
        height: 32, padding: '0 14px',
        background: 'var(--surface-2)', borderBottom: '1px solid var(--border)',
        display: 'flex', alignItems: 'flex-end', gap: 0,
        flexShrink: 0,
      }}>
        {TABS.map((t) => {
          let count = null;
          let countTone = null;
          if (t.id === 'quality' && tab === 'quality' && qualityQ.data) {
            count = qualityQ.data.total;
            countTone = qualityErr ? 'err' : qualityWarn ? 'warn' : null;
          }
          return (
            <button key={t.id} onClick={() => setTab(t.id)} style={{
              height: 32, padding: '0 14px',
              background: tab === t.id ? 'var(--surface)' : 'transparent',
              border: 'none',
              borderTop: tab === t.id ? '1px solid var(--fg)' : '1px solid transparent',
              borderLeft: tab === t.id ? '1px solid var(--border)' : 'none',
              borderRight: tab === t.id ? '1px solid var(--border)' : 'none',
              fontFamily: 'var(--font-mono)', fontSize: 11,
              color: tab === t.id ? 'var(--fg)' : 'var(--fg-3)',
              cursor: 'pointer', marginBottom: -1,
              display: 'inline-flex', alignItems: 'center', gap: 6,
            }}>
              {t.l}
              {count != null && (
                <span style={{
                  fontFamily: 'var(--font-mono)', fontSize: 9,
                  color: countTone === 'err' ? 'var(--err)' : countTone === 'warn' ? 'var(--warn)' : 'var(--fg-3)',
                  background: countTone === 'err' ? 'var(--err-bg)' : countTone === 'warn' ? 'var(--warn-bg)' : 'var(--surface-3)',
                  padding: '0 4px', borderRadius: 2,
                }}>{count}</span>
              )}
            </button>
          );
        })}
        <div style={{ flex: 1 }} />
      </div>

      {/* Tab body */}
      <div className="scroll" style={{ flex: 1, minHeight: 0 }}>
        {tab === 'overview' && b && <OverviewTab b={b} />}
        {tab === 'preview' && <PreviewTab q={rowsQ} />}
        {tab === 'quality' && <QualityTab q={qualityQ} />}
        {tab === 'lineage' && <LineageTab q={lineageQ} batchId={batchId} />}
        {tab === 'params' && b && <ParamsTab b={b} />}
      </div>

      {/* Invalidate confirm */}
      {showInvalidate && (
        <DangerConfirm
          title="作废批次"
          onCancel={() => { setShowInvalidate(false); setInvalidateReason(''); }}
          onConfirm={() => invalidateMut.mutate()}
          busy={invalidateMut.isPending}
          confirmLabel="确认作废"
        >
          <div style={{ fontSize: 12, color: 'var(--fg-2)', marginBottom: 12 }}>
            作废批次 <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--fg)' }}>{batchId}</span>。
            该操作不可逆，当前视图将不再使用该批次数据。
          </div>
          <div style={{ marginBottom: 6, fontSize: 11, color: 'var(--fg-3)' }}>
            作废原因 <span style={{ color: 'var(--err)' }}>（至少 10 个字符，必填）</span>:
          </div>
          <textarea
            value={invalidateReason}
            onChange={(e) => setInvalidateReason(e.target.value)}
            rows={3}
            placeholder="请说明作废原因…"
            style={{
              width: '100%', padding: '6px 10px', background: 'var(--surface-2)',
              border: `1px solid ${invalidateReason.length >= 10 ? 'var(--ok)' : 'var(--border)'}`,
              borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 11,
              color: 'var(--fg)', resize: 'vertical',
            }}
          />
          {invalidateMut.error && (
            <div style={{ color: 'var(--err)', fontSize: 11, marginTop: 8 }}>
              {invalidateMut.error?.apiError?.message || String(invalidateMut.error)}
            </div>
          )}
        </DangerConfirm>
      )}

      {/* Supersede modal */}
      {showSupersede && (
        <Modal
          title="关联替换"
          width={480}
          onClose={() => { setShowSupersede(false); setNewBatchId(''); }}
          footer={
            <>
              <button className="btn sm" onClick={() => { setShowSupersede(false); setNewBatchId(''); }}>取消</button>
              <button
                className="btn sm"
                disabled={!newBatchId.trim() || supersedeMut.isPending}
                onClick={() => supersedeMut.mutate()}
              >{supersedeMut.isPending ? '执行中…' : '确认替换'}</button>
            </>
          }
        >
          <div style={{ fontSize: 12, color: 'var(--fg-2)', marginBottom: 12 }}>
            将当前批次 <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--fg)' }}>{batchId}</span> 标记为被以下批次替代:
          </div>
          <label style={{ fontSize: 11, color: 'var(--fg-3)', display: 'block', marginBottom: 4 }}>新批次 ID:</label>
          <input
            value={newBatchId}
            onChange={(e) => setNewBatchId(e.target.value)}
            placeholder="b_2026_…"
            style={{
              width: '100%', height: 30, padding: '0 10px', background: 'var(--surface-2)',
              border: '1px solid var(--border)', borderRadius: 3,
              fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)',
            }}
          />
          {supersedeMut.error && (
            <div style={{ color: 'var(--err)', fontSize: 11, marginTop: 8 }}>
              {supersedeMut.error?.apiError?.message || String(supersedeMut.error)}
            </div>
          )}
        </Modal>
      )}
    </TerminalShell>
  );
}

// ── Tab: 基本信息 ──────────────────────────────────────────────────────────────
function KV({ k, v }) {
  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '160px 1fr',
      padding: '6px 0', borderBottom: '1px solid var(--border-faint)', alignItems: 'baseline',
    }}>
      <span style={{ fontSize: 11, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)' }}>{k}</span>
      <span style={{ fontSize: 12, color: 'var(--fg)', fontFamily: 'var(--font-mono)', wordBreak: 'break-all' }}>{v}</span>
    </div>
  );
}

function OverviewTab({ b }) {
  return (
    <div style={{ maxWidth: 760, padding: '16px 20px' }}>
      <KV k="batch_id" v={<CopyableId value={b.batch_id} length={40} />} />
      <KV k="status" v={<StatusChip status={b.status} />} />
      <KV k="target_table" v={<Link to={`/data/tables/${b.target_table}`} style={{ color: 'var(--info)', textDecoration: 'underline dotted' }}>{b.target_table}</Link>} />
      <KV k="source" v={b.source} />
      <KV k="started_at" v={<span title={relTime(b.started_at)}>{absTime(b.started_at)}</span>} />
      <KV k="finished_at" v={b.finished_at ? <span title={relTime(b.finished_at)}>{absTime(b.finished_at)}</span> : (b.status === 'running' ? '运行中…' : '—')} />
      <KV k="duration_sec" v={fmtDuration(b.duration_sec)} />
      <KV k="row_count" v={b.row_count != null ? fmtN(b.row_count) : '—'} />
      <KV k="partition_count" v={b.partition_count ?? '—'} />
      <KV k="partition_keys" v={b.partition_keys?.join(', ') || '—'} />
      <KV k="parameters_hash" v={b.parameters_hash || '—'} />
      {b.error_message && <KV k="error_message" v={<span style={{ color: 'var(--err)' }}>{b.error_message}</span>} />}
      {b.superseded_by && (
        <KV k="superseded_by" v={
          <Link to={`/data/batches/${b.superseded_by}`} style={{ color: 'var(--info)', textDecoration: 'underline dotted' }}>
            {b.superseded_by}
          </Link>
        } />
      )}
      {b.supersedes && (
        <KV k="supersedes" v={
          <Link to={`/data/batches/${b.supersedes}`} style={{ color: 'var(--info)', textDecoration: 'underline dotted' }}>
            {b.supersedes}
          </Link>
        } />
      )}
      {b.invalidated_at && <KV k="invalidated_at" v={absTime(b.invalidated_at)} />}
      {b.invalidated_reason && <KV k="invalidated_reason" v={b.invalidated_reason} />}
      {b.notes && <KV k="notes" v={b.notes} />}
    </div>
  );
}

// ── Tab: 写入预览 ──────────────────────────────────────────────────────────────
function PreviewTab({ q }) {
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorState error={q.error} onRetry={q.refetch} />;
  if (!q.data) return <Empty label="暂无数据" />;
  const { columns, items, total } = q.data;
  return (
    <div>
      <div style={{
        fontSize: 11, color: 'var(--fg-3)', padding: '8px 14px',
        background: 'var(--surface-2)', borderBottom: '1px solid var(--border)',
        fontFamily: 'var(--font-mono)',
      }}>
        ⓘ 这是该批次原始写入，可能已被当前视图覆盖。显示前 {items.length} / {fmtN(total)} 行。
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
          <thead style={{ position: 'sticky', top: 0, background: 'var(--surface)', zIndex: 1 }}>
            <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
              {columns.map((c) => (
                <th key={c.name} style={{
                  textAlign: 'left', padding: '6px 10px',
                  fontSize: 9.5, color: 'var(--fg-3)', fontWeight: 500,
                  textTransform: 'uppercase', letterSpacing: '0.04em', whiteSpace: 'nowrap',
                }}>
                  {c.name}
                  <span style={{ color: 'var(--fg-4)', marginLeft: 4, fontSize: 8.5, textTransform: 'lowercase', letterSpacing: 0 }}>{c.type}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map((row, ri) => (
              <tr key={ri} style={{ borderBottom: '1px solid var(--border-faint)', height: 26 }}>
                {columns.map((c) => {
                  const val = row[c.name];
                  const isInternal = c.name.startsWith('_');
                  const displayVal = val === null || val === undefined ? '∅' : (typeof val === 'object' ? JSON.stringify(val) : String(val));
                  return (
                    <td key={c.name} style={{
                      padding: '0 10px',
                      color: isInternal ? 'var(--fg-3)' : 'var(--fg-2)',
                      fontSize: isInternal ? 10 : 11,
                      fontVariantNumeric: 'tabular-nums',
                      maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>
                      {displayVal}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Tab: 质量检查 ──────────────────────────────────────────────────────────────
function QualityTab({ q }) {
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorState error={q.error} onRetry={q.refetch} />;
  const items = q.data?.items || [];
  if (items.length === 0) return <Empty label="该批次无质量问题" hint="所有质量检查均通过" />;
  return (
    <div>
      <div style={{
        padding: '6px 14px', fontSize: 11, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)',
        background: 'var(--surface-2)', borderBottom: '1px solid var(--border)',
      }}>
        共 {q.data.total} 条 · warn: {q.data.by_level?.warn ?? 0} · error: {q.data.by_level?.error ?? 0}
      </div>
      {items.map((item) => {
        const isErr = item.level === 'error';
        const color = isErr ? 'var(--err)' : 'var(--warn)';
        return (
          <div key={item.id} style={{
            padding: '10px 14px', borderBottom: '1px solid var(--border-faint)',
            display: 'flex', alignItems: 'flex-start', gap: 12,
            background: item.acknowledged ? 'transparent' : (isErr ? 'rgba(204,67,52,0.03)' : 'rgba(216,153,0,0.03)'),
          }}>
            <span style={{ color, fontFamily: 'var(--font-mono)', fontSize: 14, flexShrink: 0, marginTop: 1 }}>
              {isErr ? '✕' : '⚠'}
            </span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 3 }}>
                <span style={{ fontFamily: 'var(--font-mono)', fontWeight: 500, color: 'var(--fg)' }}>{item.check_name}</span>
                <span className={`chip ${isErr ? 'err' : 'warn'}`} style={{ fontSize: 9, height: 14 }}>{item.level}</span>
                {item.acknowledged && <span style={{ fontSize: 10, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)' }}>已确认</span>}
              </div>
              <div style={{ fontSize: 11, color: 'var(--fg-2)', fontFamily: 'var(--font-mono)' }}>{item.message}</div>
              {item.count != null && (
                <div style={{ fontSize: 10, color: 'var(--fg-3)', marginTop: 3, fontFamily: 'var(--font-mono)' }}>
                  触发 {fmtN(item.count)} 行
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ── Tab: Lineage ───────────────────────────────────────────────────────────────
function LineageTab({ q, batchId }) {
  if (q.isLoading) return <Loading />;
  if (q.error) return <ErrorState error={q.error} onRetry={q.refetch} />;
  const nodes = q.data?.nodes || [];
  const currentId = q.data?.current_node_id;
  if (nodes.length === 0) return <Empty label="无 Lineage 数据" />;
  return (
    <div style={{ padding: '20px 24px' }}>
      <div style={{ fontSize: 11, color: 'var(--fg-3)', marginBottom: 20, fontFamily: 'var(--font-mono)' }}>
        该批次链上共 {nodes.length} 个节点。当前活跃节点已高亮。
      </div>
      <div style={{ display: 'flex', alignItems: 'stretch', gap: 0, overflowX: 'auto' }}>
        {nodes.map((n, i) => {
          const isCurrent = n.batch_id === currentId || n.is_current;
          const sm = statusMeta(n.status);
          return (
            <React.Fragment key={n.batch_id}>
              <div
                onClick={() => { if (n.batch_id !== batchId) window.location.assign(`/data/batches/${n.batch_id}`); }}
                style={{
                  minWidth: 190, padding: '12px 14px',
                  border: '1px solid',
                  borderColor: isCurrent ? 'var(--fg)' : 'var(--border)',
                  borderRadius: 4,
                  background: isCurrent ? 'var(--surface-2)' : 'var(--surface)',
                  opacity: n.status === 'invalidated' ? 0.7 : 1,
                  cursor: n.batch_id !== batchId ? 'pointer' : 'default',
                  display: 'flex', flexDirection: 'column', gap: 6,
                }}
                onMouseEnter={(e) => { if (n.batch_id !== batchId) e.currentTarget.style.background = 'var(--hover)'; }}
                onMouseLeave={(e) => { e.currentTarget.style.background = isCurrent ? 'var(--surface-2)' : 'var(--surface)'; }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{ color: toneColor(sm.tone), fontSize: 10, fontFamily: 'var(--font-mono)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                    {sm.sym} {n.status}
                  </span>
                  <span style={{ color: 'var(--fg-3)', fontSize: 10, fontFamily: 'var(--font-mono)' }}>{absTime(n.started_at).slice(11, 16)}</span>
                </div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, fontWeight: 500, color: 'var(--fg)', wordBreak: 'break-all' }}>
                  {n.batch_id}
                </div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>
                  src: {n.source} · {n.row_count != null ? fmtCompact(n.row_count) : '—'} 行
                </div>
                {isCurrent && (
                  <div style={{ fontSize: 10, fontFamily: 'var(--font-mono)', color: 'var(--fg)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                    ▶ 当前
                  </div>
                )}
              </div>
              {i < nodes.length - 1 && (
                <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', padding: '0 8px', flexShrink: 0 }}>
                  <span style={{ fontSize: 9, color: 'var(--fg-4)', fontFamily: 'var(--font-mono)', marginBottom: 2 }}>replaced</span>
                  <span style={{ fontSize: 20, color: 'var(--fg-3)' }}>→</span>
                </div>
              )}
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
}

// ── Tab: Parameters ────────────────────────────────────────────────────────────
function ParamsTab({ b }) {
  function copyJSON() {
    navigator.clipboard?.writeText(JSON.stringify(b.parameters, null, 2));
  }
  return (
    <div style={{ padding: '16px 20px', maxWidth: 720 }}>
      <div style={{ display: 'flex', gap: 8, marginBottom: 12 }}>
        <button className="btn sm" onClick={copyJSON}>复制 JSON</button>
      </div>
      <div style={{
        background: 'var(--surface-2)', padding: 14, borderRadius: 4,
        border: '1px solid var(--border)',
        fontFamily: 'var(--font-mono)', fontSize: 11.5,
        whiteSpace: 'pre', color: 'var(--fg-2)',
        overflowX: 'auto',
      }}>
        {b.parameters ? JSON.stringify(b.parameters, null, 2) : 'null'}
      </div>
      {b.parameters_hash && (
        <div style={{ marginTop: 10, fontSize: 11, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)' }}>
          parameters_hash: <span style={{ color: 'var(--fg)' }}>{b.parameters_hash}</span>
        </div>
      )}
    </div>
  );
}
