// /factors/universe — preset + custom stock pools, with editor drawer +
// previewer.
import React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { endpoints } from '../../lib/api';
import { TerminalShell, TPanel, FilterBar, FInput, FSelect, KpiStrip } from '../../components/shell';
import { Empty, Loading } from '../../components/ui';
import { DrawerHeader } from './RunConfigDrawer';
import { MiniEmpty, MiniError, MiniLoading, PRESET_UNI } from './shared';

export default function UniverseManager() {
  const qc = useQueryClient();
  const [search, setSearch] = React.useState('');
  const [typeFilter, setTypeFilter] = React.useState('');
  const [previewDate, setPreviewDate] = React.useState(() => new Date().toISOString().slice(0, 10));
  const [previewUid, setPreviewUid] = React.useState(null);
  const [editingUid, setEditingUid] = React.useState(null);

  const uniQ = useQuery({
    queryKey: ['universes'],
    queryFn: () => endpoints.universes(),
  });
  const items = (uniQ.data?.items || []).filter((u) => {
    if (typeFilter === 'preset' && u.kind !== 'preset_index') return false;
    if (typeFilter === 'custom' && u.kind !== 'custom') return false;
    if (search && !(u.name + u.universe_id).toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  const presets = items.filter((u) => u.kind === 'preset_index');
  const customs = items.filter((u) => u.kind === 'custom');

  const usingTotal = items.reduce((a, u) => a + (u.used_by || 0), 0);
  const maxCount = Math.max(0, ...items.map((u) => u.member_count || 0));

  return (
    <TerminalShell
      module="factor" current="pools"
      path={['factor_mgmt/', 'pools']}
      title="股票池"
      subtitle={`universes · ${presets.length} 预设 · ${customs.length} 自定义 · ${items.length} 总计`}
      actions={
        <>
          <button className="btn ghost sm" onClick={() => setEditingUid('__new__')}>+ 新建自定义池</button>
          <button className="btn sm" style={{ background: 'var(--fg)', color: 'var(--bg)', borderColor: 'var(--fg)' }} onClick={() => uniQ.refetch()}>刷新</button>
        </>
      }
      filterBar={
        <FilterBar applied={[search, typeFilter].filter(Boolean)} onClear={() => { setSearch(''); setTypeFilter(''); }}>
          <FInput label="search" placeholder="池名 / id…" value={search} onChange={setSearch} width={180} />
          <FSelect label="type" value={typeFilter} onChange={setTypeFilter} width={120} options={[
            { value: '', label: '全部' }, { value: 'preset', label: 'preset' }, { value: 'custom', label: 'custom' },
          ]} />
        </FilterBar>
      }
    >
      <KpiStrip cols={6} cells={[
        { label: '总池数', value: items.length, sub: `${presets.length} 预设 + ${customs.length} 自定义` },
        { label: '在用因子', value: usingTotal, sub: 'factor 引用', tone: usingTotal ? 'ok' : 'neutral' },
        { label: '日常跟踪', value: items.filter((u) => u.used_by > 0).length, sub: '池有跟踪' },
        { label: '最大成分股', value: maxCount.toLocaleString(), sub: '全市场' },
        { label: '快照保留', value: '730 天', sub: '历史可复现' },
        { label: '本页过滤', value: items.length, sub: items.length === (uniQ.data?.items || []).length ? '无过滤' : '已过滤' },
      ]} />

      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        <div style={{ flex: 1.4, minWidth: 0, borderRight: editingUid || previewUid ? '1px solid var(--border)' : 'none', display: 'flex', flexDirection: 'column' }}>
          <Section title="PRESET · 系统预设" meta="不可编辑 · 不可删除 · 成分股动态">
            {uniQ.isLoading ? <MiniLoading />
              : uniQ.isError ? <MiniError error={uniQ.error} onRetry={() => uniQ.refetch()} label="加载股票池失败" />
              : presets.length ? (
                <PoolTable pools={presets} editingUid={editingUid} onPreview={(uid) => { setPreviewUid(uid); setEditingUid(null); }} />
              ) : <MiniEmpty label="预设池未就绪 (factor.db 尚未 seed)" />}
          </Section>
          <Section title="CUSTOM · 自定义池" meta="可编辑 · 可删除">
            {customs.length ? (
              <PoolTable pools={customs} editingUid={editingUid}
                onEdit={(uid) => { setEditingUid(uid); setPreviewUid(null); }}
                onPreview={(uid) => { setPreviewUid(uid); setEditingUid(null); }}
                onDelete={(uid) => {
                  if (window.confirm(`确定删除 ${uid}?`)) {
                    endpoints.deleteUniverse(uid).then(() => qc.invalidateQueries({ queryKey: ['universes'] }));
                  }
                }} />
            ) : !uniQ.isError && !uniQ.isLoading && <MiniEmpty label="还没有自定义池 — 「+ 新建自定义池」开始" />}
          </Section>

          {previewUid && (
            <div style={{ borderTop: '1px solid var(--border)', flexShrink: 0 }}>
              <PoolPreview uid={previewUid} date={previewDate} onDate={setPreviewDate} onClose={() => setPreviewUid(null)} />
            </div>
          )}
        </div>

        {editingUid && (
          <PoolEditor uid={editingUid === '__new__' ? null : editingUid} onClose={() => setEditingUid(null)}
            onSaved={() => { setEditingUid(null); qc.invalidateQueries({ queryKey: ['universes'] }); }} />
        )}
      </div>
    </TerminalShell>
  );
}

function Section({ title, meta, children }) {
  return (
    <section>
      <header style={{
        padding: '6px 14px', background: 'var(--surface-2)', borderBottom: '1px solid var(--border)',
        borderTop: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10,
      }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{title}</span>
        {meta && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)' }}>· {meta}</span>}
      </header>
      <div>{children}</div>
    </section>
  );
}

function PoolTable({ pools, editingUid, onEdit, onPreview, onDelete }) {
  return (
    <table data-testid="pool-table" style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
      <thead><tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
        {['', 'name', 'type', '成分股数', '过滤摘要', '行业体系', 'creator', '在用因子数', 'updated', ''].map((c, i) => (
          <th key={i} style={{ textAlign: i === 0 || i === 9 ? 'center' : i === 3 || i === 7 ? 'right' : 'left',
            padding: '6px 12px', fontSize: 9.5, color: 'var(--fg-3)', fontWeight: 500,
            textTransform: 'uppercase', letterSpacing: '0.06em', fontFamily: 'var(--font-mono)' }}>{c}</th>
        ))}
      </tr></thead>
      <tbody>
        {pools.map((p, i) => {
          const isEditing = p.universe_id === editingUid;
          return (
            <tr key={p.universe_id} data-testid={`pool-row-${p.universe_id}`} style={{
              borderBottom: '1px solid var(--border-faint)', height: 28,
              background: isEditing ? 'var(--info-bg)' : (i % 2 ? 'rgba(0,0,0,0.012)' : 'transparent'),
            }}>
              <td style={{ padding: '0 8px', textAlign: 'center' }}>{p.is_preset ? '🔒' : '✎'}</td>
              <td style={{ padding: '0 12px', fontWeight: 500, color: 'var(--fg)' }}>{p.name}{isEditing && <span style={{ color: 'var(--info)', marginLeft: 8, fontFamily: 'var(--font-mono)', fontSize: 9 }}>← 正在编辑</span>}</td>
              <td style={{ padding: '0 12px' }}>
                {p.is_preset
                  ? <span className="chip neutral" style={{ height: 16, fontSize: 9.5 }}>preset</span>
                  : <span className="chip info" style={{ height: 16, fontSize: 9.5 }}>custom</span>}
              </td>
              <td style={{ padding: '0 12px', textAlign: 'right', fontFamily: 'var(--font-mono)', fontVariantNumeric: 'tabular-nums', color: 'var(--fg)' }}>
                {p.member_count != null ? p.member_count.toLocaleString() : '动态'}
              </td>
              <td style={{ padding: '0 12px', fontFamily: 'var(--font-mono)', color: 'var(--fg-2)', fontSize: 10.5 }}>
                {p.universe_id}{p.exclude_st ? ' · 排ST' : ''}{p.min_list_days > 0 ? ` · ≥${p.min_list_days}d` : ''}
              </td>
              <td style={{ padding: '0 12px', fontFamily: 'var(--font-mono)', color: 'var(--fg-3)', fontSize: 10.5 }}>
                {p.classification_system}{p.default_industry_level ? ` ${['','一','二','三'][p.default_industry_level] || ''}级` : ''}
              </td>
              <td style={{ padding: '0 12px', fontFamily: 'var(--font-mono)', color: 'var(--fg-3)' }}>{p.owner || 'system'}</td>
              <td style={{ padding: '0 12px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: p.used_by > 5 ? 'var(--ok)' : 'var(--fg-2)' }}>{p.used_by ?? 0}</td>
              <td style={{ padding: '0 12px', fontFamily: 'var(--font-mono)', color: 'var(--fg-3)', fontSize: 10.5 }}>{p.updated_at?.slice(0, 10)}</td>
              <td style={{ padding: '0 12px', textAlign: 'right', fontFamily: 'var(--font-mono)', fontSize: 10 }}>
                <span onClick={() => onPreview?.(p.universe_id)} style={{ color: 'var(--fg-3)', cursor: 'pointer', marginRight: 8 }}>预览</span>
                {!p.is_preset && (
                  <>
                    <span onClick={() => onEdit?.(p.universe_id)} style={{ color: 'var(--info)', cursor: 'pointer', marginRight: 8 }}>编辑</span>
                    <span onClick={() => onDelete?.(p.universe_id)} style={{ color: 'var(--err)', cursor: 'pointer' }}>删除</span>
                  </>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

// ─── Preview area ───────────────────────────────────────────────────────
function PoolPreview({ uid, date, onDate, onClose }) {
  const q = useQuery({
    queryKey: ['universe.preview', uid, date],
    queryFn: () => endpoints.previewUniverse(uid, { date }),
    enabled: !!uid && !!date,
  });
  return (
    <TPanel title="POOL PREVIEW · 当日实际成分" meta={`${uid} · 已叠加过滤`} last
      action={
        <>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', marginRight: 8 }}>日期</span>
          <input type="date" value={date} onChange={(e) => onDate(e.target.value)}
            style={{ height: 22, padding: '0 7px', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 11 }} />
          <button className="btn ghost sm" onClick={onClose} style={{ marginLeft: 8, fontSize: 10 }}>关闭</button>
        </>
      }>
      {q.isLoading && <MiniLoading />}
      {q.isError && <MiniError error={q.error} onRetry={() => q.refetch()} label="加载成分股失败" />}
      {q.data && (
        <>
          <div data-testid="pool-preview-funnel" style={{
            padding: '10px 16px', display: 'flex', alignItems: 'center', gap: 14,
            fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-2)',
            background: 'var(--surface-2)', borderBottom: '1px solid var(--border-faint)',
          }}>
            <span>选定 <span style={{ color: 'var(--fg)' }}>{q.data.selected}</span> · 排除 ST <span style={{ color: 'var(--err)' }}>−{q.data.excluded_st}</span> · 排除停牌 <span style={{ color: 'var(--err)' }}>−{q.data.excluded_suspended}</span> · 上市天数过滤 <span style={{ color: 'var(--err)' }}>−{q.data.excluded_newly_listed}</span></span>
            <span style={{ color: 'var(--fg-mute)' }}>=</span>
            <span style={{ color: 'var(--ok)' }}>{q.data.tradable} 只可评估</span>
            <span style={{ color: 'var(--fg-mute)' }}>│</span>
            <span>覆盖率 <span style={{ color: 'var(--fg)' }}>{(q.data.coverage * 100).toFixed(1)}%</span></span>
          </div>
          {q.data.sample?.length ? (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', padding: '8px 12px', gap: 1, fontFamily: 'var(--font-mono)', fontSize: 10.5 }}>
              {q.data.sample.map((s) => (
                <div key={s.symbol} style={{ padding: '4px 6px' }}>
                  <div style={{ color: 'var(--fg)', fontWeight: 500 }}>{s.symbol}</div>
                  <div style={{ color: 'var(--fg-3)', fontSize: 9.5 }}>{s.name || '—'}</div>
                </div>
              ))}
              {q.data.tradable > q.data.sample.length && (
                <div style={{ padding: '4px 6px', color: 'var(--fg-mute)' }}>… +{q.data.tradable - q.data.sample.length}</div>
              )}
            </div>
          ) : <MiniEmpty label="无可评估成分股" />}
        </>
      )}
    </TPanel>
  );
}

// ─── Editor drawer ───────────────────────────────────────────────────────
function PoolEditor({ uid, onClose, onSaved }) {
  const isNew = !uid;
  const q = useQuery({
    queryKey: ['universe', uid],
    queryFn: () => endpoints.universe(uid),
    enabled: !isNew,
  });
  const data = q.data;
  const [name, setName] = React.useState('');
  const [symbols, setSymbols] = React.useState([]);
  const [search, setSearch] = React.useState('');
  const [excludeST, setExcludeST] = React.useState(true);
  const [excludeSusp, setExcludeSusp] = React.useState(true);
  const [minListDays, setMinListDays] = React.useState(60);
  const [system, setSystem] = React.useState('SW');
  const [level, setLevel] = React.useState(1);
  const [description, setDescription] = React.useState('');

  React.useEffect(() => {
    if (data) {
      setName(data.name);
      setSymbols(data.members || []);
      setExcludeST(data.exclude_st);
      setExcludeSusp(data.exclude_suspended);
      setMinListDays(data.min_list_days);
      setSystem(data.classification_system || 'SW');
      setLevel(data.default_industry_level || 1);
      setDescription(data.description || '');
    }
  }, [data]);

  const searchQ = useQuery({
    queryKey: ['universe.symbols.search', search],
    queryFn: () => endpoints.symbolSearchUni(search, 8),
    enabled: search.length >= 2,
  });

  const save = useMutation({
    mutationFn: () => {
      const body = {
        name, symbols, exclude_st: excludeST, exclude_suspended: excludeSusp,
        min_list_days: Number(minListDays) || 0, classification_system: system,
        default_industry_level: Number(level), description,
      };
      return isNew ? endpoints.createUniverse(body) : endpoints.updateUniverse(uid, body);
    },
    onSuccess: () => onSaved?.(),
  });

  const addSymbol = (s) => {
    if (!symbols.includes(s)) setSymbols([...symbols, s]);
    setSearch('');
  };
  const removeSymbol = (s) => setSymbols(symbols.filter((x) => x !== s));
  const pasteSymbols = async () => {
    const t = await navigator.clipboard.readText();
    const arr = t.split(/[\s,;]+/).map((x) => x.trim()).filter(Boolean);
    setSymbols(Array.from(new Set([...symbols, ...arr])));
  };

  return (
    <aside data-testid="pool-editor" style={{
      width: 580, flexShrink: 0, background: 'var(--surface)', display: 'flex', flexDirection: 'column',
      minHeight: 0, boxShadow: '-8px 0 24px rgba(20,18,14,0.06)',
    }}>
      <DrawerHeader title={isNew ? '新建自定义票池' : `编辑 ${uid}`} subtitle={isNew ? '' : data?.name} onClose={onClose} />
      <div className="scroll" style={{ flex: 1, padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
        <Field label="名称" required>
          <input value={name} onChange={(e) => setName(e.target.value)} style={inputStyle} />
        </Field>
        <Field label="股票">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, width: '100%' }}>
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <input data-testid="pool-search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="搜索代码或名称…"
                style={{ ...inputStyle, flex: 1 }} />
              <button className="btn ghost sm" onClick={pasteSymbols} style={{ fontSize: 10 }}>📋 粘贴</button>
            </div>
            {searchQ.data?.items?.length > 0 && (
              <div style={{ border: '1px solid var(--border)', borderRadius: 3, maxHeight: 140, overflow: 'auto' }}>
                {searchQ.data.items.map((s) => (
                  <div key={s.symbol} data-testid={`pool-search-result-${s.symbol}`} onClick={() => addSymbol(s.symbol)} style={{
                    padding: '5px 10px', cursor: 'pointer', display: 'grid',
                    gridTemplateColumns: '90px 1fr 60px', gap: 8, fontFamily: 'var(--font-mono)', fontSize: 11,
                    borderBottom: '1px solid var(--border-faint)',
                  }}>
                    <span style={{ fontWeight: 500, color: 'var(--fg)' }}>{s.symbol}</span>
                    <span style={{ color: 'var(--fg-2)' }}>{s.name}</span>
                    <span style={{ color: 'var(--fg-3)' }}>{s.exchange}</span>
                  </div>
                ))}
              </div>
            )}
            <div style={{ border: '1px solid var(--border)', borderRadius: 3, maxHeight: 240, overflow: 'auto', background: 'var(--surface-2)' }}>
              {symbols.length === 0 ? (
                <MiniEmpty label="未添加股票" />
              ) : symbols.map((s) => (
                <div key={s} data-testid={`pool-member-${s}`} style={{
                  padding: '4px 12px', display: 'flex', justifyContent: 'space-between',
                  borderBottom: '1px solid var(--border-faint)', fontFamily: 'var(--font-mono)', fontSize: 11,
                }}>
                  <span style={{ color: 'var(--fg)' }}>{s}</span>
                  <span onClick={() => removeSymbol(s)} style={{ color: 'var(--err)', cursor: 'pointer' }}>✕</span>
                </div>
              ))}
            </div>
            <span style={{ fontSize: 10, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)' }}>共 <span style={{ color: 'var(--fg)' }}>{symbols.length}</span> 只</span>
          </div>
        </Field>
        <Field label="排除 ST"><Toggle on={excludeST} onClick={() => setExcludeST(!excludeST)} /></Field>
        <Field label="排除停牌"><Toggle on={excludeSusp} onClick={() => setExcludeSusp(!excludeSusp)} /></Field>
        <Field label="最少上市天数">
          <input type="number" value={minListDays} onChange={(e) => setMinListDays(e.target.value)} style={{ ...inputStyle, width: 100 }} />
          <span style={{ fontSize: 10, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)' }}>天</span>
        </Field>
        <Field label="默认行业体系">
          <select value={system} onChange={(e) => setSystem(e.target.value)} style={{ ...inputStyle, width: 100 }}>
            <option value="SW">申万</option>
            <option value="CITIC">中信</option>
          </select>
          <select value={level} onChange={(e) => setLevel(Number(e.target.value))} style={{ ...inputStyle, width: 80 }}>
            <option value={1}>一级</option><option value={2}>二级</option><option value={3}>三级</option>
          </select>
        </Field>
        <Field label="描述">
          <textarea value={description} onChange={(e) => setDescription(e.target.value)} rows={3}
            style={{ ...inputStyle, height: 56, resize: 'vertical', flex: 1 }} />
        </Field>
        {save.isError && <div className="chip err" style={{ padding: '6px 10px', height: 'auto' }}>{save.error?.apiError?.message || '保存失败'}</div>}
      </div>
      <footer style={{
        padding: '10px 16px', borderTop: '1px solid var(--border)', background: 'var(--surface-2)',
        display: 'flex', alignItems: 'center', gap: 8,
      }}>
        {!isNew && data?.used_by > 0 && (
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--warn)' }}>
            ⚠ 该池正被 {data.used_by} 个因子使用,保存会影响它们的下次评估
          </span>
        )}
        <div style={{ flex: 1 }} />
        <button className="btn ghost sm" onClick={onClose}>取消</button>
        <button className="btn sm" data-testid="pool-save" disabled={save.isPending || !name || symbols.length === 0}
          onClick={() => save.mutate()}
          style={{ background: 'var(--fg)', color: 'var(--bg)', borderColor: 'var(--fg)' }}>
          {save.isPending ? '保存中…' : '保存'}
        </button>
      </footer>
    </aside>
  );
}

const inputStyle = {
  height: 22, padding: '0 7px', background: 'var(--surface-2)', border: '1px solid var(--border)',
  borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)',
};

function Field({ label, required, children }) {
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '110px 1fr', alignItems: 'flex-start', gap: 12, minHeight: 24 }}>
      <span style={{ fontSize: 11, color: 'var(--fg-2)', paddingTop: 4 }}>{label}{required && <span style={{ color: 'var(--err)' }}> *</span>}</span>
      <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>{children}</div>
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
