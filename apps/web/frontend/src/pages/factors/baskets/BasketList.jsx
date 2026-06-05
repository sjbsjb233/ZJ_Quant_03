// /factors/baskets — 因子篮子 list. Card grid, one card per basket: health
// + latest-conclusion at a glance. (因子层重构 §3, migration banner §13.)
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { endpoints } from '../../../lib/api';
import { TerminalShell, FilterBar, FInput } from '../../../components/shell';
import { ErrorState, Loading } from '../../../components/ui';
import {
  Badge, CalibrChips, LightHint, Segmented, EmptyState, KPIStrip, fmtNum,
} from './kit';

const LEGACY_HINT_KEY = 'fl.basket.legacyHint.dismissed';

export default function BasketList() {
  const navigate = useNavigate();
  const [filter, setFilter] = React.useState('active');
  const [search, setSearch] = React.useState('');
  const [hintDismissed, setHintDismissed] = React.useState(
    () => { try { return localStorage.getItem(LEGACY_HINT_KEY) === '1'; } catch { return false; } },
  );

  const q = useQuery({ queryKey: ['baskets'], queryFn: () => endpoints.baskets() });
  const legacyQ = useQuery({ queryKey: ['baskets.legacyTracked'], queryFn: () => endpoints.basketLegacyTracked() });

  const all = q.data?.items || [];
  const legacy = legacyQ.data?.items || [];
  const term = search.trim().toLowerCase();
  const baskets = all.filter((b) => {
    if (filter === 'archived' ? b.status !== 'archived' : filter === 'active' ? b.status !== 'active' : false) return false;
    if (term && !`${b.name || ''} ${b.description || b.desc || ''}`.toLowerCase().includes(term)) return false;
    return true;
  });

  const activeCnt = all.filter((b) => b.status === 'active').length;
  const trackingCnt = all.filter((b) => b.tracking?.state === 'on').length;
  const staleCnt = all.filter((b) => b.stale?.is).length;
  const entryCnt = all.reduce((s, b) => s + (b.entry_count || 0), 0);

  const dismissHint = () => {
    setHintDismissed(true);
    try { localStorage.setItem(LEGACY_HINT_KEY, '1'); } catch { /* ignore */ }
  };

  const showHint = !hintDismissed && legacy.length > 0;
  const appliedFilters = (filter !== 'active' || term) ? [{}] : [];

  return (
    <TerminalShell
      module="factor"
      current="baskets"
      path={['factor_mgmt/', 'baskets']}
      fkeys={[{ k: 'F1', l: '新建篮子' }, { k: 'F5', l: '刷新全部' }]}
      title="因子篮子"
      subtitle="baskets · 一组因子的长期打包 · 共享公共参数 · 反复运行产出快照"
      actions={(
        <button
          className="btn sm"
          style={{ background: 'var(--fg)', color: 'var(--bg)', borderColor: 'var(--fg)' }}
          onClick={() => navigate('/factors/baskets/new')}
        >
          + 新建篮子
        </button>
      )}
      filterBar={(
        <FilterBar applied={appliedFilters} onClear={() => { setFilter('active'); setSearch(''); }}>
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
            <span style={{ color: 'var(--fg-3)', fontSize: 10 }}>状态:</span>
            <Segmented
              options={[{ v: 'active', l: '活跃' }, { v: 'archived', l: '已归档' }, { v: 'all', l: '全部' }]}
              value={filter}
              onChange={setFilter}
            />
          </span>
          <FInput label="search" placeholder="篮子名…" width={150} value={search} onChange={setSearch} />
        </FilterBar>
      )}
    >
      {q.isLoading ? (
        <Loading />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={q.refetch} />
      ) : (
        <>
          {showHint && (
            <div style={{ padding: '8px 18px 0', flexShrink: 0 }}>
              <LightHint
                tone="info"
                action={(
                  <span style={{ display: 'inline-flex', gap: 8 }}>
                    <button className="btn primary sm" onClick={() => navigate('/factors/baskets/new')}>新建追踪篮子</button>
                    <button className="btn ghost sm" onClick={dismissHint}>知道了</button>
                  </span>
                )}
              >
                单因子追踪已并入因子篮子。这些因子（
                {legacy.map((f) => f.name).filter(Boolean).join('、')}
                ）可一键创建为追踪篮子
              </LightHint>
            </div>
          )}

          <KPIStrip
            cols={5}
            items={[
              { l: '活跃篮子', v: String(activeCnt), t: 'ok' },
              { l: '追踪开启', v: String(trackingCnt), sub: '日常增量' },
              { l: '待重算', v: String(staleCnt), sub: staleCnt ? '需重跑' : '无', t: staleCnt ? 'warn' : undefined },
              { l: '篮子总数', v: String(all.length) },
              { l: '累计条目', v: String(entryCnt), sub: `跨 ${all.length} 篮` },
            ]}
          />

          {baskets.length === 0 ? (
            <EmptyState
              icon="◇"
              title={all.length === 0 ? '还没有因子篮子' : '没有匹配的篮子'}
              desc={all.length === 0
                ? '篮子是一组因子的“打包”——它们共享一套公共参数（股票池、时间窗口、调仓频率），长期存在、反复运行，每次产出一份不可变的快照供回看与优选。'
                : '调整状态筛选或搜索词试试。'}
              action={all.length === 0
                ? <button className="btn primary" onClick={() => navigate('/factors/baskets/new')}>+ 新建第一个篮子</button>
                : undefined}
            />
          ) : (
            <div
              className="scroll"
              style={{ flex: 1, minHeight: 0, padding: 18, display: 'flex', flexWrap: 'wrap', gap: 16, alignContent: 'flex-start', background: 'var(--bg)' }}
            >
              {baskets.map((b) => <BasketCard key={b.id || b.basket_id} b={b} navigate={navigate} />)}
            </div>
          )}
        </>
      )}
    </TerminalShell>
  );
}

function BasketCard({ b, navigate }) {
  const id = b.id || b.basket_id;
  const tr = b.tracking || {};
  const stats = b.stats || {};
  const mini = b.mini || {};
  const pending = b.pendingSettle || [];
  const trackingValue = tr.state === 'on' ? '开启' : tr.state === 'paused' ? '已暂停' : '关闭';
  const trackingLabel = tr.state === 'on'
    ? (tr.next ? `追踪 · ${tr.next}` : '追踪开启')
    : tr.state === 'paused'
      ? (tr.paused_reason ? `已暂停 · ${tr.paused_reason}` : '已暂停')
      : (tr.paused_reason ? `追踪关闭 · ${tr.paused_reason}` : '追踪关闭');

  return (
    <div
      onClick={() => navigate(`/factors/baskets/${id}`)}
      style={{
        flex: '1 1 440px', maxWidth: 620, alignSelf: 'flex-start',
        background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden',
        cursor: 'pointer', position: 'relative', transition: 'box-shadow .12s, border-color .12s',
        boxShadow: '0 1px 0 rgba(20,18,14,0.02)',
      }}
      onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--border-strong)'; e.currentTarget.style.boxShadow = '0 6px 20px rgba(20,18,14,0.07)'; }}
      onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.boxShadow = '0 1px 0 rgba(20,18,14,0.02)'; }}
    >
      {b.stale?.is && (
        <div style={{ position: 'absolute', top: 0, right: 0, background: 'var(--warn-bg)', borderLeft: '1px solid var(--warn)', borderBottom: '1px solid var(--warn)', borderBottomLeftRadius: 6, padding: '3px 8px' }}>
          <Badge group="stale" value="待重算" sm />
        </div>
      )}

      <div style={{ padding: '12px 14px 10px' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 3, paddingRight: b.stale?.is ? 70 : 0 }}>
          <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--fg)' }}>{b.name}</span>
          {b.status === 'archived' && <span className="chip neutral" style={{ height: 15, fontSize: 9 }}>已归档</span>}
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-4)' }}>rev {b.rev ?? b.active_revision ?? '—'}</span>
        </div>
        <div style={{ fontSize: 11, color: 'var(--fg-3)', lineHeight: 1.5, marginBottom: 10 }}>{b.description || b.desc}</div>

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10 }}>
          {b.status === 'active' && <span className="chip ok" style={{ height: 17, fontSize: 10 }}>活跃</span>}
          <Badge group="tracking" value={trackingValue} label={trackingLabel} />
        </div>

        <div style={{ marginBottom: 10 }}><CalibrChips calibration={b.calibration} compact /></div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', borderTop: '1px solid var(--border-faint)', borderBottom: '1px solid var(--border-faint)' }}>
        {[
          { l: '条目', v: b.entry_count ?? 0 },
          { l: '最新 as_of', v: b.asOf ? b.asOf.slice(5) : '—', sub: pending.length ? `待结算 ${pending.length}` : '已结算' },
          { l: '平均|IC|', v: fmtNum(mini.avgIC, 3) },
          { l: '冗余率', v: mini.redundancy ?? '—' },
        ].map((s, i) => (
          <div key={s.l} style={{ padding: '8px 10px', borderRight: i < 3 ? '1px solid var(--border-faint)' : 'none' }}>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 8.5, color: 'var(--fg-4)', textTransform: 'uppercase', letterSpacing: '0.04em' }}>{s.l}</div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 500, color: 'var(--fg)', fontVariantNumeric: 'tabular-nums', lineHeight: 1.2 }}>{s.v}</div>
            {s.sub && <div style={{ fontFamily: 'var(--font-mono)', fontSize: 8.5, color: s.sub.includes('待结算') ? 'var(--warn)' : 'var(--fg-4)' }}>{s.sub}</div>}
          </div>
        ))}
      </div>

      <div style={{ padding: '8px 14px', display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)' }}>
          最新快照
          <Badge group="run" value="成功" label={`${stats.okCnt ?? 0} 成功`} sm />
          {(stats.degCnt ?? 0) > 0 && <Badge group="run" value="降级" label={`${stats.degCnt} 降级`} sm />}
          {(stats.failCnt ?? 0) > 0 && <Badge group="run" value="失败" label={`${stats.failCnt} 失败`} sm />}
        </span>
        <div style={{ flex: 1 }} />
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-4)' }}>rev {b.rev ?? '—'} · {b.snapshot_count ?? 0} 快照</span>
        <span
          onClick={(e) => { e.stopPropagation(); navigate(`/factors/baskets/${id}`); }}
          style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--info)', cursor: 'pointer' }}
        >
          详情 →
        </span>
      </div>
    </div>
  );
}
