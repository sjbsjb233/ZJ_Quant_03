// /factors/selections — 入选集合 列表. 草稿 → 已定稿 → 已归档 (单向不可逆).
// 优选的最终产物 · 不可变存档：选中了哪些因子、为什么。(因子层重构 §11/§12)
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { endpoints } from '../../../lib/api';
import { TerminalShell, FilterBar } from '../../../components/shell';
import { ErrorState, Loading } from '../../../components/ui';
import { Badge, Segmented, EmptyState, KPIStrip } from '../baskets/kit';

export default function SelectionList() {
  const navigate = useNavigate();
  const [status, setStatus] = React.useState('all');
  const [basket, setBasket] = React.useState('');

  const q = useQuery({ queryKey: ['selections'], queryFn: () => endpoints.selections() });

  const all = q.data?.items || [];
  const basketNames = Array.from(new Set(all.map((s) => s.basketName).filter(Boolean)));
  const sels = all.filter((s) => {
    if (status !== 'all' && s.status_raw !== status) return false;
    if (basket && s.basketName !== basket) return false;
    return true;
  });

  const draftCnt = all.filter((s) => s.status_raw === 'draft').length;
  const finalCnt = all.filter((s) => s.status_raw === 'final').length;
  const staleCnt = all.filter((s) => s.stale).length;

  const appliedFilters = (status !== 'all' || basket) ? [{}] : [];

  return (
    <TerminalShell
      module="factor"
      current="selections"
      path={['factor_mgmt/', 'selections']}
      title="入选集合"
      subtitle="优选的最终产物 · 不可变存档：选中了哪些因子、为什么"
      actions={(
        <button
          className="btn sm"
          style={{ background: 'var(--fg)', color: 'var(--bg)', borderColor: 'var(--fg)' }}
          onClick={() => navigate('/factors/screening')}
        >
          进入优选工作台 →
        </button>
      )}
      filterBar={(
        <FilterBar applied={appliedFilters} onClear={() => { setStatus('all'); setBasket(''); }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span style={{ color: 'var(--fg-3)', fontSize: 10 }}>状态:</span>
            <Segmented
              options={[
                { v: 'all', l: '全部' },
                { v: 'draft', l: '草稿' },
                { v: 'final', l: '已定稿' },
                { v: 'archived', l: '已归档' },
              ]}
              value={status}
              onChange={setStatus}
            />
          </span>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span style={{ color: 'var(--fg-3)', fontSize: 10 }}>来源篮子:</span>
            <select
              value={basket}
              onChange={(e) => setBasket(e.target.value)}
              style={{ height: 24, padding: '0 8px', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)' }}
            >
              <option value="">全部</option>
              {basketNames.map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </span>
        </FilterBar>
      )}
    >
      {q.isLoading ? (
        <Loading />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={q.refetch} />
      ) : (
        <>
          <KPIStrip
            cols={4}
            items={[
              { l: '集合总数', v: String(all.length) },
              { l: '草稿', v: String(draftCnt), sub: draftCnt ? '可编辑' : '无' },
              { l: '已定稿', v: String(finalCnt), t: finalCnt ? 'ok' : undefined },
              { l: '来源已更新', v: String(staleCnt), sub: staleCnt ? '仅知悉' : '无', t: staleCnt ? 'warn' : undefined },
            ]}
          />

          {sels.length === 0 ? (
            <EmptyState
              icon="◇"
              title={all.length === 0 ? '还没有入选集合' : '没有匹配的集合'}
              desc={all.length === 0
                ? '入选集合是优选会话的产物。先去因子优选完成一次会话并保存。'
                : '调整状态或来源篮子筛选试试。'}
              action={all.length === 0
                ? <button className="btn primary" onClick={() => navigate('/factors/screening')}>去因子优选</button>
                : undefined}
            />
          ) : (
            <div className="scroll" style={{ flex: 1, minHeight: 0 }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11.5 }}>
                <thead>
                  <tr style={{ position: 'sticky', top: 0, zIndex: 2, background: 'var(--surface-2)', borderBottom: '1px solid var(--border-strong)' }}>
                    {['名称', '状态', '来源篮子', '快照', '成员数', '创建人', ''].map((h, i) => (
                      <th key={h || i} style={{ textAlign: i === 4 ? 'right' : 'left', padding: '8px 16px', fontSize: 9.5, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap' }}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {sels.map((s, i) => (
                    <tr
                      key={s.id || s.selection_id}
                      onClick={() => navigate(`/factors/selections/${s.id || s.selection_id}`)}
                      style={{ borderBottom: '1px solid var(--border-faint)', height: 38, cursor: 'pointer', background: i % 2 ? 'rgba(0,0,0,0.012)' : 'transparent' }}
                      onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--surface-2)'; }}
                      onMouseLeave={(e) => { e.currentTarget.style.background = i % 2 ? 'rgba(0,0,0,0.012)' : 'transparent'; }}
                    >
                      <td style={{ padding: '0 16px' }}>
                        <span style={{ fontWeight: 500, color: 'var(--fg)' }}>{s.name}</span>
                        {s.stale && <span className="chip warn" style={{ height: 14, fontSize: 8.5, marginLeft: 8 }}>stale</span>}
                      </td>
                      <td style={{ padding: '0 16px' }}><Badge group="selection" value={s.status} sm /></td>
                      <td style={{ padding: '0 16px', color: 'var(--fg-2)' }}>{s.basketName || '—'}</td>
                      <td style={{ padding: '0 16px', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)', whiteSpace: 'nowrap' }}>as_of {s.asOf || '—'} · rev {s.rev ?? '—'}</td>
                      <td style={{ padding: '0 16px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--fg)' }}>{s.member_count ?? (s.members ? s.members.length : '—')}</td>
                      <td style={{ padding: '0 16px', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-2)' }}>{s.creator || '—'}</td>
                      <td style={{ padding: '0 16px', textAlign: 'right', color: 'var(--fg-4)' }}>→</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </TerminalShell>
  );
}
