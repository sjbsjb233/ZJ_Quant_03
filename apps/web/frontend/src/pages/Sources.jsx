// 数据源 list + create/edit modal
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { TerminalShell, FilterBar, FInput, FSelect, Stat } from '../components/shell';
import { Modal, Loading, ErrorState, Empty, StatusChip } from '../components/ui';
import { Sparkline } from '../components/ui';
import { endpoints } from '../lib/api';
import { relTime, absTime } from '../lib/format';
import { useAuth } from '../lib/auth';

const TABLES_8 = [
  'raw.bars_daily', 'raw.bars_minute', 'raw.fundamentals', 'raw.universe',
  'raw.calendar', 'raw.corporate_actions', 'reference.symbols', 'reference.sectors',
];

const CATEGORY_OPTIONS = [
  { value: 'external_api', label: '外部 API' },
  { value: 'manual', label: '人工录入' },
  { value: 'internal', label: '内部镜像' },
];

const AUTH_OPTIONS = [
  { value: 'token', label: 'Token (header)' },
  { value: 'basic', label: 'Basic Auth' },
  { value: 'none', label: '无认证' },
  { value: 'oauth2', label: 'OAuth2' },
];

function statusTone(s) {
  if (!s) return 'neutral';
  if (s.is_active === false) return 'neutral';
  return 'ok';
}

function sourceStatus(s) {
  if (!s.is_active) return 'inactive';
  return 'ACTIVE';
}

// Field Mapping Editor: per-table {srcCol -> stdCol} rows
function FieldMappingEditor({ value, onChange }) {
  // value: { [table]: { [srcCol]: stdCol } }
  const [activeTable, setActiveTable] = useState(TABLES_8[0]);
  const schemaQ = useQuery({
    queryKey: ['tableSchema', activeTable],
    queryFn: () => endpoints.tableSchema(activeTable),
    staleTime: 300000,
  });

  const tableMapping = (value || {})[activeTable] || {};
  const stdCols = (schemaQ.data?.columns || []).filter(c => !c.is_pk && !c.is_meta).map(c => c.name);

  const setMapping = (srcCol, stdCol) => {
    const updated = { ...(value || {}), [activeTable]: { ...tableMapping, [srcCol]: stdCol } };
    onChange(updated);
  };

  const addRow = () => {
    const updated = { ...(value || {}), [activeTable]: { ...tableMapping, '': '' } };
    onChange(updated);
  };

  const removeRow = (srcCol) => {
    const copy = { ...tableMapping };
    delete copy[srcCol];
    onChange({ ...(value || {}), [activeTable]: copy });
  };

  const updateSrcCol = (oldKey, newKey) => {
    const copy = { ...tableMapping };
    const v = copy[oldKey];
    delete copy[oldKey];
    copy[newKey] = v;
    onChange({ ...(value || {}), [activeTable]: copy });
  };

  return (
    <div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
        {TABLES_8.map(t => (
          <button key={t} onClick={() => setActiveTable(t)} className={`btn ghost sm${activeTable === t ? '' : ''}`}
            style={{ fontSize: 10, height: 22, background: activeTable === t ? 'var(--surface-2)' : 'transparent', borderColor: activeTable === t ? 'var(--fg)' : 'var(--border)' }}>
            {t.split('.')[1]}
          </button>
        ))}
      </div>
      <div style={{ fontSize: 10, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)', marginBottom: 6 }}>
        目标表: <b style={{ color: 'var(--fg)' }}>{activeTable}</b>
        {schemaQ.isLoading && ' (加载列名…)'}
      </div>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
        <thead>
          <tr style={{ borderBottom: '1px solid var(--border)' }}>
            <th style={{ textAlign: 'left', padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', fontWeight: 500 }}>源字段 (srcCol)</th>
            <th style={{ textAlign: 'left', padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', fontWeight: 500 }}>标准字段 (stdCol)</th>
            <th style={{ width: 30 }} />
          </tr>
        </thead>
        <tbody>
          {Object.entries(tableMapping).map(([src, std], i) => (
            <tr key={i} style={{ borderBottom: '1px solid var(--border-faint)' }}>
              <td style={{ padding: '3px 8px' }}>
                <input value={src} onChange={e => updateSrcCol(src, e.target.value)}
                  style={{ width: '100%', height: 22, padding: '0 6px', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 2, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)' }} />
              </td>
              <td style={{ padding: '3px 8px' }}>
                <select value={std} onChange={e => setMapping(src, e.target.value)}
                  style={{ width: '100%', height: 22, padding: '0 6px', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 2, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)' }}>
                  <option value="">— 选择标准字段 —</option>
                  {stdCols.map(c => <option key={c} value={c}>{c}</option>)}
                </select>
              </td>
              <td style={{ padding: '3px 4px', textAlign: 'center' }}>
                <button onClick={() => removeRow(src)} style={{ color: 'var(--err)', background: 'none', border: 'none', cursor: 'pointer', fontSize: 13, padding: '0 4px' }}>✕</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <button onClick={addRow} className="btn ghost sm" style={{ marginTop: 8, height: 22, fontSize: 10 }}>+ 添加映射行</button>
    </div>
  );
}

function SourceModal({ source, onClose }) {
  const qc = useQueryClient();
  const { hasRole } = useAuth();
  const isCreate = !source;

  const [form, setForm] = useState({
    source_id: source?.source_id || '',
    display_name: source?.display_name || '',
    category: source?.category || 'external_api',
    priority: source?.priority ?? 5,
    is_active: source?.is_active ?? true,
    rate_limit_per_min: source?.rate_limit_per_min ?? 60,
    auth_method: source?.auth_method || 'token',
    endpoint: source?.endpoint || '',
    description: source?.description || '',
    field_mapping: source?.field_mapping || {},
  });
  const [tab, setTab] = useState('basic');
  const [err, setErr] = useState('');

  const set = (k, v) => setForm(f => ({ ...f, [k]: v }));

  const createMut = useMutation({
    mutationFn: (body) => endpoints.createSource(body),
    onSuccess: () => { qc.invalidateQueries(['sources']); onClose(); },
    onError: (e) => setErr(e.apiError?.message || '创建失败'),
  });
  const updateMut = useMutation({
    mutationFn: (body) => endpoints.updateSource(source.source_id, body),
    onSuccess: () => { qc.invalidateQueries(['sources']); onClose(); },
    onError: (e) => setErr(e.apiError?.message || '更新失败'),
  });

  const busy = createMut.isPending || updateMut.isPending;

  const handleSave = () => {
    setErr('');
    if (!form.source_id.match(/^[a-z0-9_]+$/)) { setErr('source_id 只允许小写字母/数字/下划线'); return; }
    if (isCreate) createMut.mutate(form);
    else updateMut.mutate(form);
  };

  return (
    <Modal width={640} onClose={onClose} title={isCreate ? '+ 新建数据源' : `编辑数据源 · ${source.source_id}`}
      footer={
        <>
          <button className="btn sm" onClick={onClose}>取消</button>
          <button className="btn sm" onClick={handleSave} disabled={busy || !hasRole('editor')}
            style={{ background: 'var(--fg)', color: 'var(--bg)', borderColor: 'var(--fg)' }}>
            {busy ? '保存中…' : '保存'}
          </button>
        </>
      }>
      {/* Tab bar */}
      <div style={{ display: 'flex', gap: 0, marginBottom: 16, borderBottom: '1px solid var(--border)' }}>
        {[['basic', '基本信息'], ['mapping', 'Field Mapping']].map(([k, l]) => (
          <button key={k} data-testid={`source-modal-tab-${k}`} onClick={() => setTab(k)} style={{
            padding: '6px 16px', background: tab === k ? 'var(--surface-2)' : 'transparent',
            border: 'none', borderBottom: tab === k ? '2px solid var(--fg)' : '2px solid transparent',
            fontFamily: 'var(--font-mono)', fontSize: 11, color: tab === k ? 'var(--fg)' : 'var(--fg-3)',
            cursor: 'pointer', marginBottom: -1,
          }}>{l}</button>
        ))}
      </div>

      {tab === 'basic' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {isCreate && (
            <FormRow label="source_id *">
              <input value={form.source_id} onChange={e => set('source_id', e.target.value)}
                placeholder="lowercase_a_z_0_9" style={inputStyle} />
              <div style={{ fontSize: 10, color: 'var(--fg-3)', marginTop: 3 }}>小写字母/数字/下划线，创建后不可修改</div>
            </FormRow>
          )}
          <FormRow label="display_name *">
            <input value={form.display_name} onChange={e => set('display_name', e.target.value)} style={inputStyle} />
          </FormRow>
          <FormRow label="category">
            <div style={{ display: 'flex', gap: 12 }}>
              {CATEGORY_OPTIONS.map(o => (
                <label key={o.value} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, cursor: 'pointer', fontFamily: 'var(--font-mono)' }}>
                  <input type="radio" name="category" value={o.value} checked={form.category === o.value} onChange={() => set('category', o.value)} style={{ accentColor: 'var(--fg)' }} />
                  {o.label}
                </label>
              ))}
            </div>
          </FormRow>
          <FormRow label="priority">
            <input type="number" min={1} max={99} value={form.priority} onChange={e => set('priority', parseInt(e.target.value) || 1)} style={{ ...inputStyle, width: 80 }} />
          </FormRow>
          <FormRow label="is_active">
            <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
              <input type="checkbox" checked={form.is_active} onChange={e => set('is_active', e.target.checked)} style={{ accentColor: 'var(--fg)' }} />
              启用此数据源
            </label>
          </FormRow>
          <FormRow label="rate_limit/min">
            <input type="number" min={0} value={form.rate_limit_per_min} onChange={e => set('rate_limit_per_min', parseInt(e.target.value) || 0)} style={{ ...inputStyle, width: 100 }} />
          </FormRow>
          <FormRow label="auth_method">
            <select value={form.auth_method} onChange={e => set('auth_method', e.target.value)} style={{ ...inputStyle, width: 160 }}>
              {AUTH_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </FormRow>
          <FormRow label="endpoint">
            <input value={form.endpoint} onChange={e => set('endpoint', e.target.value)} placeholder="https://..." style={inputStyle} />
          </FormRow>
          <FormRow label="description">
            <textarea value={form.description} onChange={e => set('description', e.target.value)}
              rows={3} style={{ ...inputStyle, height: 'auto', padding: '6px 8px', resize: 'vertical', width: '100%' }} />
          </FormRow>
        </div>
      )}

      {tab === 'mapping' && (
        <FieldMappingEditor value={form.field_mapping} onChange={v => set('field_mapping', v)} />
      )}

      {err && <div style={{ marginTop: 12, color: 'var(--err)', fontSize: 11, fontFamily: 'var(--font-mono)' }}>✕ {err}</div>}
      {!hasRole('editor') && <div style={{ marginTop: 12, color: 'var(--warn)', fontSize: 11 }}>只读模式：需要 editor 权限才能保存</div>}
    </Modal>
  );
}

function FormRow({ label, children }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '140px 1fr', gap: 10, alignItems: 'flex-start' }}>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)', paddingTop: 4 }}>{label}</span>
      <div>{children}</div>
    </div>
  );
}

const inputStyle = {
  width: '100%', height: 28, padding: '0 8px',
  background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3,
  fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)',
};

function SourceCard({ s, onDetail, onEdit }) {
  const { hasRole } = useAuth();
  const qc = useQueryClient();
  const isActive = s.is_active;
  const tone = isActive ? 'ok' : 'neutral';

  const toggleMut = useMutation({
    mutationFn: () => endpoints.updateSource(s.source_id, { is_active: !isActive }),
    onSuccess: () => qc.invalidateQueries(['sources']),
  });

  const [editingPriority, setEditingPriority] = useState(false);
  const [priorityVal, setPriorityVal] = useState(String(s.priority));

  const savePriority = useMutation({
    mutationFn: (p) => endpoints.updateSource(s.source_id, { priority: p }),
    onSuccess: () => qc.invalidateQueries(['sources']),
  });

  const handlePriorityEnter = () => {
    const p = parseInt(priorityVal);
    if (!isNaN(p) && p !== s.priority) savePriority.mutate(p);
    setEditingPriority(false);
  };

  const dimmed = !isActive;

  return (
    <div data-testid={`source-card-${s.source_id}`} style={{
      padding: 16, background: 'var(--surface)',
      borderBottom: '1px solid var(--border)',
      opacity: dimmed ? 0.65 : 1,
      minHeight: 220,
      display: 'flex', flexDirection: 'column', gap: 12,
    }}>
      {/* Title row */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 10 }}>
        <div style={{
          width: 36, height: 36, borderRadius: 4,
          background: 'var(--surface-2)', border: '1px solid var(--border)',
          display: 'grid', placeItems: 'center',
          fontFamily: 'var(--font-mono)', fontSize: 16, fontWeight: 600,
          color: dimmed ? 'var(--fg-3)' : 'var(--fg)',
          flexShrink: 0,
        }}>{s.source_id[0].toUpperCase()}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 500, color: 'var(--fg)', display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            {s.source_id}
            <span className={`chip ${tone}`} style={{ fontSize: 9, height: 16 }}>{isActive ? 'ACTIVE' : 'INACTIVE'}</span>
          </div>
          <div style={{ fontSize: 11, color: 'var(--fg-3)', marginTop: 2 }}>
            {s.display_name} · {s.category}
          </div>
        </div>
        <div style={{ textAlign: 'right', flexShrink: 0 }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>PRIORITY</div>
          {editingPriority && hasRole('editor') ? (
            <input autoFocus data-testid={`source-priority-input-${s.source_id}`} value={priorityVal}
              onChange={e => setPriorityVal(e.target.value)}
              onBlur={handlePriorityEnter}
              onKeyDown={e => { if (e.key === 'Enter') handlePriorityEnter(); if (e.key === 'Escape') setEditingPriority(false); }}
              style={{ width: 44, fontFamily: 'var(--font-mono)', fontSize: 18, fontWeight: 600, textAlign: 'right', background: 'var(--surface-2)', border: '1px solid var(--fg)', borderRadius: 2, color: 'var(--fg)', padding: '1px 4px' }} />
          ) : (
            <div
              data-testid={`source-priority-${s.source_id}`}
              onClick={() => hasRole('editor') && setEditingPriority(true)}
              style={{ fontFamily: 'var(--font-mono)', fontSize: 19, fontWeight: 600, color: 'var(--fg)', lineHeight: 1, cursor: hasRole('editor') ? 'pointer' : 'default' }}
              title={hasRole('editor') ? '点击编辑优先级' : ''}
            >{s.priority}</div>
          )}
        </div>
      </div>

      {/* Stats row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8 }}>
        <Stat label="priority" value={s.priority} />
        <Stat label="rate/min" value={s.rate_limit_per_min ?? '—'} />
        <Stat label="category" value={s.category?.replace('_', ' ')} />
      </div>

      {/* Field mapping tables */}
      {s.field_mapping && Object.keys(s.field_mapping).length > 0 && (
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>
          tables: {Object.keys(s.field_mapping).map(t => <span key={t} style={{ marginRight: 6, color: 'var(--fg-2)' }}>{t.split('.')[1]}</span>)}
        </div>
      )}

      <div style={{ flex: 1 }} />

      {/* Updated at */}
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>
        updated: <span style={{ color: 'var(--fg-2)' }}>{s.updated_at ? relTime(s.updated_at) : '—'}</span>
      </div>

      {/* Actions */}
      <div style={{ display: 'flex', gap: 6 }}>
        <button className="btn ghost sm" onClick={onDetail} style={{ height: 22, fontSize: 10 }}>详情 →</button>
        {hasRole('editor') && (
          <button className="btn ghost sm" onClick={onEdit} style={{ height: 22, fontSize: 10 }}>编辑</button>
        )}
        <div style={{ flex: 1 }} />
        {hasRole('editor') && (
          <button className="btn ghost sm" onClick={() => toggleMut.mutate()}
            disabled={toggleMut.isPending}
            style={{ height: 22, fontSize: 10, color: isActive ? 'var(--warn)' : 'var(--info)' }}>
            {isActive ? '禁用' : '启用'}
          </button>
        )}
      </div>
    </div>
  );
}

export default function Sources() {
  const nav = useNavigate();
  const { hasRole } = useAuth();
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [editTarget, setEditTarget] = useState(null); // null=closed, false=create mode, source obj=edit mode
  const [showModal, setShowModal] = useState(false);

  const sourcesQ = useQuery({
    queryKey: ['sources'],
    queryFn: () => endpoints.sources(false),
    refetchInterval: 30000,
  });

  const items = sourcesQ.data?.items || [];

  const filtered = items.filter(s => {
    const matchSearch = !search || s.source_id.includes(search) || s.display_name?.includes(search);
    const matchStatus = statusFilter === 'all' || (statusFilter === 'active' ? s.is_active : !s.is_active);
    return matchSearch && matchStatus;
  }).sort((a, b) => a.priority - b.priority);

  const activeCount = items.filter(s => s.is_active).length;
  const total = items.length;

  const openCreate = () => { setEditTarget(false); setShowModal(true); };
  const openEdit = (s) => { setEditTarget(s); setShowModal(true); };
  const closeModal = () => { setShowModal(false); setEditTarget(null); };

  return (
    <TerminalShell
      module="data" current="sources"
      path={['data_mgmt/', 'sources']}
      fkeys={[
        { k: 'F1', l: '新建', onClick: openCreate },
        { k: 'F5', l: '刷新', onClick: () => sourcesQ.refetch() },
      ]}
      title="数据源"
      subtitle={`sources · ${total} registered · ${activeCount} active · ${total - activeCount} inactive`}
      actions={
        <>
          <button className="btn ghost sm" onClick={() => nav('/data/sources/diff')}>跨源对比</button>
          {hasRole('editor') && (
            <button className="btn sm" data-testid="source-create-btn" onClick={openCreate}
              style={{ background: 'var(--fg)', color: 'var(--bg)', borderColor: 'var(--fg)' }}>
              + 新建数据源
            </button>
          )}
        </>
      }
      filterBar={
        <FilterBar>
          <FInput label="search" placeholder="source_id / name" width={160} value={search} onChange={setSearch} />
          <FSelect label="status" value={statusFilter} width={120}
            options={[{ value: 'all', label: '全部' }, { value: 'active', label: 'active' }, { value: 'inactive', label: 'inactive' }]}
            onChange={setStatusFilter} />
        </FilterBar>
      }
    >
      {sourcesQ.isLoading ? <Loading /> : sourcesQ.error ? <ErrorState error={sourcesQ.error} onRetry={sourcesQ.refetch} /> : (
        <div style={{ flex: 1, display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gridAutoRows: 'min-content', overflow: 'auto', minHeight: 0 }}>
          {filtered.map((s, i) => (
            <div key={s.source_id} style={{ borderRight: (i + 1) % 3 === 0 ? 'none' : '1px solid var(--border)' }}>
              <SourceCard s={s}
                onDetail={() => nav(`/data/sources/${s.source_id}`)}
                onEdit={() => openEdit(s)} />
            </div>
          ))}
          {filtered.length === 0 && !sourcesQ.isLoading && (
            <div style={{ gridColumn: '1 / -1' }}><Empty label="暂无数据源" /></div>
          )}
          {/* Add new card */}
          {hasRole('editor') && (
            <div
              onClick={openCreate}
              style={{
                padding: 16, minHeight: 220,
                borderBottom: '1px solid var(--border)',
                display: 'grid', placeItems: 'center',
                color: 'var(--fg-3)', cursor: 'pointer',
                background: 'repeating-linear-gradient(135deg, transparent 0 8px, var(--hover) 8px 9px)',
              }}
              onMouseEnter={e => e.currentTarget.style.opacity = '0.8'}
              onMouseLeave={e => e.currentTarget.style.opacity = '1'}
            >
              <div style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 32, color: 'var(--fg-4)' }}>+</div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-3)', marginTop: 4 }}>+ NEW SOURCE</div>
                <div style={{ fontSize: 10, color: 'var(--fg-4)', marginTop: 4 }}>akshare · tushare · custom REST · CSV</div>
              </div>
            </div>
          )}
        </div>
      )}

      {showModal && <SourceModal source={editTarget || null} onClose={closeModal} />}
    </TerminalShell>
  );
}
