// /factors/selections/:sid — 入选集合 详情. 草稿可改名/定稿/删除; 已定稿可归档;
// 总是可导出 (JSON). 剔除项保留可见证据 (被谁挡掉 + ρ). 条件回放冻结展示.
import React from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { endpoints } from '../../../lib/api';
import { downloadBlob } from '../../../lib/download';
import { TerminalShell } from '../../../components/shell';
import { ErrorState, Loading, Modal } from '../../../components/ui';
import { Badge, SectionHead, LightHint, fmtNum } from '../baskets/kit';

const PRESET_LABEL = {
  spre_builtin_ic_quality: 'IC质量优选（内置）',
  spre_builtin_ls_return: '多空收益优选（内置）',
  spre_builtin_low_turnover: '低换手优选（内置）',
};
const ORDER_MODE_LABEL = (m) => {
  if (!m) return '—';
  if (m === 'score') return '推荐得分';
  if (m === 'manual') return '手动拖拽';
  if (typeof m === 'string' && m.startsWith('metric:')) return `单指标 · ${m.slice(7).replace(/_desc$/, '')}`;
  return m;
};

function describeWeights(w) {
  if (w == null) return '—';
  if (typeof w === 'string') return PRESET_LABEL[w.replace(/^preset:/, '')] || w;
  const keys = Object.keys(w);
  if (!keys.length) return '默认';
  return keys.map((k) => `${k} ${(Number(w[k]) * 100).toFixed(0)}%`).join(' / ');
}

function describeFilters(f) {
  if (!f) return '无';
  let obj = f;
  if (typeof f === 'string') { try { obj = JSON.parse(f); } catch { return f; } }
  const parts = [];
  (obj.metric || []).forEach((c) => parts.push(`${c.k} ${c.op} ${c.v}`));
  (obj.params || []).forEach((c) => parts.push(`参数 ${c.k} ${c.op} ${c.v}`));
  if (obj.flags?.monotonic) parts.push('要求单调性');
  if (obj.factor?.category) parts.push(`类别=${obj.factor.category}`);
  if (obj.factor?.tags_any?.length) parts.push(`标签∈[${obj.factor.tags_any.join(',')}]`);
  if (obj.factor?.q) parts.push(`搜索"${obj.factor.q}"`);
  return parts.length ? parts.join(' · ') : '无';
}

export default function SelectionDetail() {
  const { sid } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const [renaming, setRenaming] = React.useState(false);
  const [newName, setNewName] = React.useState('');
  const [confirmFinalize, setConfirmFinalize] = React.useState(false);
  const [confirmDelete, setConfirmDelete] = React.useState(false);
  const [confirmArchive, setConfirmArchive] = React.useState(false);

  const q = useQuery({ queryKey: ['selection', sid], queryFn: () => endpoints.selection(sid) });
  const s = q.data;

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['selection', sid] });
    qc.invalidateQueries({ queryKey: ['selections'] });
  };

  const renameM = useMutation({
    mutationFn: (name) => endpoints.patchSelection(sid, { name }),
    onSuccess: () => { setRenaming(false); invalidate(); },
  });
  const finalizeM = useMutation({
    mutationFn: () => endpoints.finalizeSelection(sid),
    onSuccess: () => { setConfirmFinalize(false); invalidate(); },
  });
  const archiveM = useMutation({
    mutationFn: () => endpoints.archiveSelection(sid),
    onSuccess: () => { setConfirmArchive(false); invalidate(); },
  });
  const deleteM = useMutation({
    mutationFn: () => endpoints.deleteSelection(sid),
    onSuccess: () => { setConfirmDelete(false); qc.invalidateQueries({ queryKey: ['selections'] }); navigate('/factors/selections'); },
  });
  const exportM = useMutation({
    mutationFn: () => endpoints.exportSelection(sid, 'json'),
    onSuccess: (data) => {
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
      downloadBlob(blob, `selection_${sid}.json`);
    },
  });

  return (
    <TerminalShell
      module="factor"
      current="selections"
      path={['factor_mgmt/', 'selections', s?.name || sid]}
      titleBar={false}
    >
      {q.isLoading ? (
        <Loading />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={q.refetch} />
      ) : !s ? (
        <ErrorState error={{ message: '集合不存在' }} />
      ) : (
        <>
          {/* header */}
          <div style={{ padding: '12px 18px', background: 'var(--surface)', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
                  <span onClick={() => navigate('/factors/selections')} style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-3)', cursor: 'pointer' }}>← 入选集合</span>
                  <h1 style={{ margin: 0, fontFamily: 'var(--font-mono)', fontSize: 17, fontWeight: 500, color: 'var(--fg)' }}>{s.name}</h1>
                  <Badge group="selection" value={s.status} />
                  {s.stale && <span className="chip warn" style={{ height: 18, fontSize: 9 }}>来源已更新</span>}
                </div>
                <div style={{ display: 'flex', gap: 12, marginTop: 7, fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)', flexWrap: 'wrap' }}>
                  <span>来源 <span style={{ color: 'var(--fg-2)' }}>{s.basketName || '—'}</span></span>
                  <span>快照 as_of {s.asOf || '—'}</span>
                  <span>rev {s.rev ?? '—'}</span>
                  <span>创建 {s.creator || '—'}{s.created ? ` · ${s.created}` : ''}</span>
                </div>
              </div>
              <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
                {s.status_raw === 'draft' && (
                  <>
                    <button className="btn sm primary" onClick={() => setConfirmFinalize(true)}>定稿</button>
                    <button className="btn sm" onClick={() => { setNewName(s.name); setRenaming(true); }}>改名</button>
                    <button className="btn sm" disabled={exportM.isPending} onClick={() => exportM.mutate()}>导出</button>
                    <button className="btn sm ghost" style={{ color: 'var(--err)' }} onClick={() => setConfirmDelete(true)}>删除</button>
                  </>
                )}
                {s.status_raw === 'final' && (
                  <>
                    <button className="btn sm" disabled={exportM.isPending} onClick={() => exportM.mutate()}>导出</button>
                    <button className="btn sm ghost" onClick={() => setConfirmArchive(true)}>归档</button>
                  </>
                )}
                {s.status_raw === 'archived' && (
                  <button className="btn sm" disabled={exportM.isPending} onClick={() => exportM.mutate()}>导出</button>
                )}
              </div>
            </div>
          </div>

          {s.stale && (
            <div style={{ padding: '8px 18px', background: 'var(--bg)' }}>
              <LightHint tone="warn">来源快照已不是最新，本集合内容不变，仅供知悉。</LightHint>
            </div>
          )}

          <div className="scroll" style={{ flex: 1, minHeight: 0, background: 'var(--bg)', padding: 16 }}>
            {/* members */}
            <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
              <SectionHead title="成员" meta={`${(s.members || []).length} 项`} />
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11.5 }}>
                <thead>
                  <tr style={{ background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}>
                    {['名次', '标签', '得分', '决策标记', '年化ICIR', '|RankIC|', 'IC胜率', '多空夏普', '最大回撤', '换手'].map((h, i) => (
                      <th key={h} style={{ textAlign: i >= 2 && i !== 3 ? 'right' : 'left', padding: '6px 14px', fontSize: 9.5, color: 'var(--fg-3)', fontWeight: 500, fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(s.members || []).map((m) => {
                    const mt = m.metrics || {};
                    return (
                      <tr key={m.entryId || m.item_key || m.rank} style={{ borderBottom: '1px solid var(--border-faint)', height: 32 }}>
                        <td style={{ padding: '0 14px', fontFamily: 'var(--font-mono)', color: 'var(--fg-4)' }}>{m.rank}</td>
                        <td style={{ padding: '0 14px', fontWeight: 500, color: 'var(--fg)' }}>{m.label}</td>
                        <td style={{ padding: '0 14px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--fg)' }}>{fmtNum(m.score, 3)}</td>
                        <td style={{ padding: '0 14px' }}><Badge group="decision" value={m.decision} sm /></td>
                        <td style={{ padding: '0 14px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--fg-2)' }}>{fmtNum(mt.icir_annual, 2)}</td>
                        <td style={{ padding: '0 14px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--fg-2)' }}>{fmtNum(Math.abs(mt.rank_ic_mean ?? NaN), 3)}</td>
                        <td style={{ padding: '0 14px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--fg-2)' }}>{mt.ic_win_rate != null ? `${(mt.ic_win_rate * 100).toFixed(0)}%` : '—'}</td>
                        <td style={{ padding: '0 14px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--fg-2)' }}>{fmtNum(mt.ls_sharpe, 2)}</td>
                        <td style={{ padding: '0 14px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--err)' }}>{mt.ls_max_drawdown != null ? `${(mt.ls_max_drawdown * 100).toFixed(1)}%` : '—'}</td>
                        <td style={{ padding: '0 14px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--fg-2)' }}>{fmtNum(mt.turnover, 2)}</td>
                      </tr>
                    );
                  })}
                  {(s.members || []).length === 0 && (
                    <tr><td colSpan={10} style={{ padding: 16, textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-4)' }}>无成员</td></tr>
                  )}
                </tbody>
              </table>
            </div>

            {/* exclusion evidence */}
            <div style={{ marginTop: 16, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
              <SectionHead title="剔除证据表" meta="让任何人事后能看懂“为什么是这几个、不是那几个”" />
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11.5 }}>
                <thead>
                  <tr style={{ background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}>
                    {['被剔除项', '原因', '挡掉它的条目', 'ρ'].map((h, i) => (
                      <th key={h} style={{ textAlign: i === 3 ? 'right' : 'left', padding: '6px 14px', fontSize: 9.5, color: 'var(--fg-3)', fontWeight: 500, fontFamily: 'var(--font-mono)' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(s.excluded || []).map((x, i) => (
                    <tr key={i} style={{ borderBottom: '1px solid var(--border-faint)', height: 32 }}>
                      <td style={{ padding: '0 14px', color: 'var(--fg-2)' }}>{x.label}</td>
                      <td style={{ padding: '0 14px' }}><Badge group="decision" value={x.by ? '因相关剔除' : '超上限剔除'} sm /></td>
                      <td style={{ padding: '0 14px', fontFamily: 'var(--font-mono)', fontSize: 11, color: x.by ? 'var(--fg)' : 'var(--fg-mute)' }}>{x.by || '—'}</td>
                      <td style={{ padding: '0 14px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: x.rho ? 'var(--err)' : 'var(--fg-mute)' }}>{x.rho != null ? Number(x.rho).toFixed(2) : '—'}</td>
                    </tr>
                  ))}
                  {(s.excluded || []).length === 0 && (
                    <tr><td colSpan={4} style={{ padding: 16, textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-4)' }}>无剔除项</td></tr>
                  )}
                </tbody>
              </table>
            </div>

            {/* frozen criteria replay */}
            <div style={{ marginTop: 16, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
              <SectionHead title="条件回放卡" meta="当时的全部设置 · 冻结展示" />
              <div style={{ padding: '12px 16px', display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '8px 24px', fontSize: 11.5 }}>
                {(() => {
                  const c = s.criteria || {};
                  const rows = [
                    ['筛选条件', describeFilters(c.filters)],
                    ['权重', describeWeights(c.weights)],
                    ['折叠', c.collapse === 'best' ? '同因子只看最优' : c.collapse === 'all' ? '全部变体' : (c.collapse || '—')],
                    ['优先序模式', ORDER_MODE_LABEL(c.order_mode)],
                    ['相关性阈值', c.threshold != null ? Number(c.threshold).toFixed(2) : (s.threshold != null ? Number(s.threshold).toFixed(2) : '—')],
                    ['保留上限', c.max_n == null ? '不限' : c.max_n],
                    ['手动保留', (c.forced && c.forced.length) ? `${c.forced.length} 项` : '无'],
                    ['纳入降级', c.include_degraded === 'all' ? '全部' : c.include_degraded === 'partial' ? '含部分' : '不含'],
                  ];
                  return rows.map(([k, v]) => (
                    <div key={k} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--fg-4)', textTransform: 'uppercase' }}>{k}</span>
                      <span style={{ color: 'var(--fg-2)', fontSize: 11 }}>{v}</span>
                    </div>
                  ));
                })()}
              </div>
            </div>
          </div>

          {/* rename modal */}
          {renaming && (
            <Modal
              title="重命名集合"
              onClose={() => setRenaming(false)}
              width={420}
              footer={(
                <>
                  <button className="btn sm" onClick={() => setRenaming(false)}>取消</button>
                  <button className="btn sm primary" disabled={!newName.trim() || renameM.isPending} onClick={() => renameM.mutate(newName.trim())}>{renameM.isPending ? '保存中…' : '保存'}</button>
                </>
              )}
            >
              <input
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                style={{ width: '100%', height: 32, padding: '0 12px', background: 'var(--surface-2)', border: '1px solid var(--border-strong)', borderRadius: 4, fontSize: 13, color: 'var(--fg)' }}
              />
              {renameM.isError && <div style={{ marginTop: 8, fontSize: 11, color: 'var(--err)' }}>{renameM.error?.apiError?.message || '保存失败'}</div>}
            </Modal>
          )}

          {/* finalize modal */}
          {confirmFinalize && (
            <Modal
              title="定稿入选集合？"
              onClose={() => setConfirmFinalize(false)}
              width={440}
              footer={(
                <>
                  <button className="btn sm" onClick={() => setConfirmFinalize(false)}>取消</button>
                  <button className="btn sm primary" disabled={finalizeM.isPending} onClick={() => finalizeM.mutate()}>{finalizeM.isPending ? '提交中…' : '确认定稿'}</button>
                </>
              )}
            >
              <div style={{ fontSize: 12, color: 'var(--fg-2)', lineHeight: 1.7 }}>
                定稿是<b style={{ color: 'var(--fg)' }}>单向不可逆</b>操作（草稿 → 已定稿 → 已归档）。定稿后集合内容与界面上的所有编辑入口都会消失，仅可导出 / 归档。
              </div>
              {finalizeM.isError && <div style={{ marginTop: 8, fontSize: 11, color: 'var(--err)' }}>{finalizeM.error?.apiError?.message || '定稿失败'}</div>}
            </Modal>
          )}

          {/* archive modal */}
          {confirmArchive && (
            <Modal
              title="归档入选集合？"
              onClose={() => setConfirmArchive(false)}
              width={440}
              footer={(
                <>
                  <button className="btn sm" onClick={() => setConfirmArchive(false)}>取消</button>
                  <button className="btn sm primary" disabled={archiveM.isPending} onClick={() => archiveM.mutate()}>{archiveM.isPending ? '提交中…' : '确认归档'}</button>
                </>
              )}
            >
              <div style={{ fontSize: 12, color: 'var(--fg-2)', lineHeight: 1.7 }}>归档后集合进入只读终态，仅可导出。</div>
              {archiveM.isError && <div style={{ marginTop: 8, fontSize: 11, color: 'var(--err)' }}>{archiveM.error?.apiError?.message || '归档失败'}</div>}
            </Modal>
          )}

          {/* delete modal */}
          {confirmDelete && (
            <Modal
              title="删除草稿集合？"
              onClose={() => setConfirmDelete(false)}
              width={440}
              footer={(
                <>
                  <button className="btn sm" onClick={() => setConfirmDelete(false)}>取消</button>
                  <button className="btn sm danger" disabled={deleteM.isPending} onClick={() => deleteM.mutate()}>{deleteM.isPending ? '删除中…' : '确认删除'}</button>
                </>
              )}
            >
              <div style={{ fontSize: 12, color: 'var(--fg-2)', lineHeight: 1.7 }}>仅草稿可删除，此操作不可恢复。</div>
              {deleteM.isError && <div style={{ marginTop: 8, fontSize: 11, color: 'var(--err)' }}>{deleteM.error?.apiError?.message || '删除失败'}</div>}
            </Modal>
          )}
        </>
      )}
    </TerminalShell>
  );
}
