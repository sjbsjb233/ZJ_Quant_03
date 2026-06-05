// ─────────────────────────────────────────────────────────────────────────────
// BasketDetail.jsx — §5 篮子详情页. Shared header (§5.0) + 5 inner tabs:
//   总览 · 因子结果 · 相关性 · 运行历史 · 配置与变更.
// Ported from the Claude Design handoff (fl-basket-detail.jsx + -detail2.jsx) into
// the real terminal shell + react-query + API. EVERY status renders via kit <Badge>.
// ─────────────────────────────────────────────────────────────────────────────
import React from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { endpoints } from '../../../lib/api';
import { TerminalShell } from '../../../components/shell';
import { Loading, ErrorState, Empty, Modal } from '../../../components/ui';
import {
  Badge, StaleBanner, Aging, ParamChips, SectionHead, Tabs, KPIStrip, EmptyState,
  fmtNum, fmtPct, degradeValue,
} from './kit';

// Map an exclude reason → the badge value vocabulary.
function excludeValue(reason) {
  return reason === 'failed' ? '失败'
    : reason === 'severe_degraded' ? '严重降级'
      : reason === 'insufficient_settled' ? '样本不足'
        : '指标缺失';
}

export default function BasketDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const qc = useQueryClient();

  const [tab, setTab] = React.useState('overview');
  const [sid, setSid] = React.useState(null);

  const basketQ = useQuery({ queryKey: ['basket', id], queryFn: () => endpoints.basket(id) });
  const snapsQ = useQuery({ queryKey: ['basket.snapshots', id], queryFn: () => endpoints.basketSnapshots(id) });
  const runsQ = useQuery({ queryKey: ['basket.runs', id], queryFn: () => endpoints.basketRuns(id) });
  const revsQ = useQuery({ queryKey: ['basket.revisions', id], queryFn: () => endpoints.basketRevisions(id) });

  const snapshots = snapsQ.data?.items || [];
  // default sid = latest snapshot id (first item).
  const latestSid = snapshots.length ? (snapshots[0].snapshot_id || snapshots[0].id) : null;
  const activeSid = sid || latestSid;

  const snapQ = useQuery({
    queryKey: ['basket.snapshot', id, activeSid],
    queryFn: () => endpoints.basketSnapshot(id, activeSid),
    enabled: !!activeSid,
  });

  const rerunMut = useMutation({
    mutationFn: (body) => endpoints.submitBasketRun(id, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['basket', id] });
      qc.invalidateQueries({ queryKey: ['basket.runs', id] });
    },
  });

  const b = basketQ.data;
  const snap = snapQ.data;

  const runCount = runsQ.data?.items?.length;
  const entryCount = b?.entry_count ?? b?.items?.length ?? 0;

  const tabs = [
    { id: 'overview', label: '总览' },
    { id: 'results', label: '因子结果', count: entryCount },
    { id: 'corr', label: '相关性' },
    { id: 'runs', label: '运行历史', count: runCount },
    { id: 'config', label: '配置与变更' },
  ];

  return (
    <TerminalShell
      module="factor"
      current="baskets"
      path={['factor_mgmt/', { label: 'baskets/', to: '/factors/baskets' }, b?.name || id]}
      title={b?.name || '因子篮子'}
      subtitle={b?.description || b?.desc || 'basket · 一组因子的长期打包 · 共享公共参数 · 反复运行产出快照'}
      actions={(
        <span style={{ display: 'inline-flex', gap: 6 }}>
          <button className="btn primary sm" disabled={rerunMut.isPending} onClick={() => rerunMut.mutate({ mode: 'full' })}>运行</button>
          <button className="btn sm" disabled={rerunMut.isPending} onClick={() => rerunMut.mutate({ mode: 'incremental' })}>增量</button>
          <button className="btn ghost sm" onClick={() => setTab('config')}>编辑配置</button>
        </span>
      )}
    >
      {basketQ.isLoading ? (
        <Loading />
      ) : basketQ.isError ? (
        <ErrorState error={basketQ.error} onRetry={basketQ.refetch} />
      ) : !b ? (
        <Empty label="篮子不存在" />
      ) : (
        <>
          {/* ── §5.0 header meta row ── */}
          <BasketHeader b={b} snap={snap} />

          {/* ── stale banner (persistent) ── */}
          <StaleBanner
            stale={b.stale}
            busy={rerunMut.isPending}
            onRerun={() => rerunMut.mutate({ mode: 'full' })}
          />

          <Tabs tabs={tabs} active={tab} onChange={setTab} />

          <div className="scroll" style={{ flex: 1, minHeight: 0, background: 'var(--bg)' }}>
            {tab === 'overview' && (
              <OverviewTab id={id} b={b} snap={snap} snapQ={snapQ} navigate={navigate} />
            )}
            {tab === 'results' && (
              <ResultsTab id={id} snap={snap} snapQ={snapQ} navigate={navigate} />
            )}
            {tab === 'corr' && (
              <CorrTab
                id={id} snapshots={snapshots} activeSid={activeSid}
                setSid={setSid} snap={snap}
              />
            )}
            {tab === 'runs' && (
              <RunsTab id={id} runsQ={runsQ} repair={(rid) => endpoints.repairBasketRun(id, rid)} qc={qc} />
            )}
            {tab === 'config' && (
              <ConfigTab id={id} b={b} revsQ={revsQ} qc={qc} />
            )}
          </div>
        </>
      )}
    </TerminalShell>
  );
}

// ─── §5.0 header (name/status/rev/tracking/aging) ────────────────────────────
function BasketHeader({ b, snap }) {
  const tr = b.tracking || {};
  const trackingValue = tr.state === 'on' ? '开启' : tr.state === 'paused' ? '已暂停' : '关闭';
  const trackingLabel = tr.state === 'on'
    ? (tr.next ? `追踪 · 下次 ${tr.next}` : '追踪开启')
    : tr.state === 'paused'
      ? (tr.paused_reason ? `已暂停 · ${tr.paused_reason}` : '已暂停')
      : '追踪关闭';

  return (
    <div style={{ padding: '12px 18px 10px', background: 'var(--surface)', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        {b.status === 'active'
          ? <span className="chip ok" style={{ height: 18 }}>活跃</span>
          : <span className="chip neutral" style={{ height: 18 }}>已归档</span>}
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-2)' }}>rev {b.rev ?? b.active_revision ?? '—'}</span>
        <span style={{ color: 'var(--fg-mute)' }}>·</span>
        <Badge group="tracking" value={trackingValue} label={trackingLabel} />
        <span style={{ color: 'var(--fg-mute)' }}>·</span>
        <Aging asOf={snap?.asOf || b.asOf} pending={b.pendingSettle} />
      </div>
    </div>
  );
}

// ─── §5.1 总览 ───────────────────────────────────────────────────────────────
function OverviewTab({ id, b, snap, snapQ, navigate }) {
  if (snapQ.isLoading) return <Loading />;
  if (snapQ.isError) return <ErrorState error={snapQ.error} onRetry={snapQ.refetch} />;
  if (!snap) {
    return (
      <EmptyState
        icon="▶"
        title="从未运行过"
        desc="这个篮子还没有任何快照。发起首次全量运行后，这里会显示总体统计与条目排行。"
      />
    );
  }

  const st = snap.basket_stats || {};
  const summaries = snap.item_summaries || {};
  const metas = snap.item_meta || {};

  const kpis = [
    { l: '条目成功率', v: fmtPct(st.success_rate, 0), sub: `${st.ok_count ?? 0}/${st.total ?? 0}`, t: 'ok' },
    { l: '平均 |IC|', v: fmtNum(st.avg_abs_ic, 3) },
    { l: '平均 ICIR', v: fmtNum(st.avg_icir, 2), t: (st.avg_icir ?? 0) >= 0.6 ? 'ok' : undefined },
    { l: '冗余率', v: fmtPct(st.redundancy, 0), sub: '高相关对', t: (st.redundancy ?? 0) > 0.4 ? 'warn' : undefined },
    { l: '被排除', v: String(st.excluded_count ?? 0), sub: '未参与矩阵' },
    { l: '降级摘要', v: String((st.degraded_count ?? 0) + (st.failed_count ?? 0)), sub: `部分${st.degraded_count ?? 0} 严重${st.failed_count ?? 0}`, t: 'warn' },
  ];

  // 条目排行榜: rows from item_summaries + item_meta, sort by icir_annual desc, top 6.
  const ranked = Object.keys(summaries)
    .map((itemId) => ({ item_id: itemId, s: summaries[itemId], m: metas[itemId] || {} }))
    .filter((r) => r.s && r.s.icir_annual != null)
    .sort((a, c) => (c.s.icir_annual ?? -Infinity) - (a.s.icir_annual ?? -Infinity))
    .slice(0, 6);

  return (
    <div style={{ padding: 18 }}>
      <div style={{ marginBottom: 16, border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
        <KPIStrip cols={6} items={kpis} />
      </div>

      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
        <SectionHead title="条目排行榜" meta="按年化ICIR · Top 6 · 点击进入条目详情" />
        {ranked.length === 0 ? (
          <Empty label="暂无可排名的条目" hint="该快照尚无成功条目" />
        ) : (
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11.5 }}>
            <thead>
              <tr style={{ background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}>
                {['#', '标签', '年化ICIR', 'IC均值', '多空夏普', '降级'].map((h, i) => (
                  <th key={h} style={{ textAlign: i >= 2 && i <= 4 ? 'right' : 'left', padding: '6px 14px', fontSize: 9.5, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', fontFamily: 'var(--font-mono)' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ranked.map((r, i) => {
                const s = r.s;
                return (
                  <tr
                    key={r.item_id}
                    onClick={() => navigate(`/factors/baskets/${id}/items/${r.item_id}`)}
                    className="iv-row"
                    style={{ borderBottom: '1px solid var(--border-faint)', height: 34, cursor: 'pointer' }}
                  >
                    <td style={{ padding: '0 14px', fontFamily: 'var(--font-mono)', color: 'var(--fg-4)' }}>{i + 1}</td>
                    <td style={{ padding: '0 14px' }}>
                      <span style={{ fontWeight: 500, color: 'var(--fg)' }}>{r.m.label || r.item_id}</span>
                    </td>
                    <td style={{ padding: '0 14px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: (s.icir_annual ?? 0) >= 2 ? 'var(--ok)' : 'var(--fg)', fontVariantNumeric: 'tabular-nums' }}>{fmtNum(s.icir_annual, 2)}</td>
                    <td style={{ padding: '0 14px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--fg-2)', fontVariantNumeric: 'tabular-nums' }}>{fmtNum(s.ic_mean, 3)}</td>
                    <td style={{ padding: '0 14px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--fg-2)', fontVariantNumeric: 'tabular-nums' }}>{fmtNum(s.ls_sharpe, 2)}</td>
                    <td style={{ padding: '0 14px' }}>
                      {s.degradation_level && s.degradation_level !== 'none'
                        ? <Badge group="degrade" value={degradeValue(s.degradation_level)} sm />
                        : <span style={{ color: 'var(--fg-mute)' }}>—</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ─── §5.2 因子结果 ───────────────────────────────────────────────────────────
const METRIC_COLS = [
  { k: 'ic_mean', l: 'IC均值', f: (v) => fmtNum(v, 3) },
  { k: 'rank_ic_mean', l: 'RankIC', f: (v) => fmtNum(v, 3) },
  { k: 'icir_annual', l: '年化ICIR', f: (v) => fmtNum(v, 2), hi: 2.0 },
  { k: 'ic_win_rate', l: '胜率', f: (v) => fmtPct(v, 0) },
  { k: 'ls_sharpe', l: '多空夏普', f: (v) => fmtNum(v, 2), hi: 1.0 },
  { k: 'ls_max_drawdown', l: '最大回撤', f: (v) => fmtPct(v, 1), neg: true },
  { k: 'turnover', l: '换手', f: (v) => fmtNum(v, 2) },
  { k: 'coverage', l: '覆盖率', f: (v) => fmtPct(v, 0) },
  { k: 'settled_n', l: '已结算', f: (v) => fmtNum(v, 0) },
];

function ResultsTab({ id, snap, snapQ, navigate }) {
  if (snapQ.isLoading) return <Loading />;
  if (snapQ.isError) return <ErrorState error={snapQ.error} onRetry={snapQ.refetch} />;
  if (!snap) return <EmptyState icon="▶" title="从未运行过" desc="发起首次全量运行后，这里会显示全部条目的指标。" />;

  const summaries = snap.item_summaries || {};
  const metas = snap.item_meta || {};
  const excluded = snap.excluded_items || [];

  const rows = Object.keys(summaries).map((itemId) => ({
    item_id: itemId, s: summaries[itemId], m: metas[itemId] || {},
  }));

  const [sortKey, setSortKey] = React.useState('icir_annual');
  const [sortDir, setSortDir] = React.useState('desc');
  const sorted = [...rows].sort((a, c) => {
    const av = a.s?.[sortKey]; const cv = c.s?.[sortKey];
    if (av == null && cv == null) return 0;
    if (av == null) return 1;
    if (cv == null) return -1;
    return sortDir === 'desc' ? cv - av : av - cv;
  });
  const onSort = (k) => {
    if (k === sortKey) setSortDir((d) => (d === 'desc' ? 'asc' : 'desc'));
    else { setSortKey(k); setSortDir('desc'); }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <div style={{ padding: '8px 16px', background: 'var(--surface)', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0, fontFamily: 'var(--font-mono)', fontSize: 11 }}>
        <span style={{ color: 'var(--fg-3)', fontSize: 10 }}>共 {rows.length} 条目 · 点击行进入条目详情 · 列可排序</span>
      </div>
      <div className="scroll" style={{ flex: 1, minHeight: 0 }}>
        <table style={{ borderCollapse: 'collapse', fontSize: 11, minWidth: 1280 }}>
          <thead>
            <tr style={{ position: 'sticky', top: 0, zIndex: 2, background: 'var(--surface-2)', borderBottom: '1px solid var(--border-strong)' }}>
              <th style={{ position: 'sticky', left: 0, zIndex: 3, background: 'var(--surface-2)', textAlign: 'left', padding: '7px 14px', fontSize: 9.5, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', fontFamily: 'var(--font-mono)', minWidth: 260 }}>标签 / 参数</th>
              {METRIC_COLS.map((c) => (
                <th
                  key={c.k}
                  onClick={() => onSort(c.k)}
                  style={{ textAlign: 'right', padding: '7px 10px', fontSize: 9, color: sortKey === c.k ? 'var(--fg)' : 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap', cursor: 'pointer' }}
                >
                  {c.l}{sortKey === c.k ? (sortDir === 'desc' ? ' ↓' : ' ↑') : ''}
                </th>
              ))}
              <th style={{ textAlign: 'left', padding: '7px 10px', fontSize: 9, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', fontFamily: 'var(--font-mono)' }}>降级</th>
              <th style={{ textAlign: 'left', padding: '7px 10px', fontSize: 9, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', fontFamily: 'var(--font-mono)' }}>状态</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((r, i) => {
              const s = r.s || {};
              return (
                <tr
                  key={r.item_id}
                  onClick={() => navigate(`/factors/baskets/${id}/items/${r.item_id}`)}
                  className="iv-row"
                  style={{ borderBottom: '1px solid var(--border-faint)', height: 34, cursor: 'pointer', background: i % 2 ? 'rgba(0,0,0,0.012)' : 'transparent' }}
                >
                  <td style={{ position: 'sticky', left: 0, zIndex: 1, background: i % 2 ? 'var(--surface-2)' : 'var(--surface)', padding: '0 14px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                      <span style={{ fontWeight: 500, color: 'var(--fg)' }}>{r.m.label || r.item_id}</span>
                      <ParamChips params={r.m.params} />
                    </div>
                  </td>
                  {METRIC_COLS.map((c) => {
                    const v = s[c.k];
                    if (v == null) return <td key={c.k} style={{ textAlign: 'right', padding: '0 10px', fontFamily: 'var(--font-mono)', color: 'var(--fg-mute)' }}>—</td>;
                    const col = c.hi != null && Number(v) >= c.hi ? 'var(--ok)' : c.neg ? 'var(--err)' : 'var(--fg-2)';
                    return <td key={c.k} style={{ textAlign: 'right', padding: '0 10px', fontFamily: 'var(--font-mono)', fontVariantNumeric: 'tabular-nums', color: col }}>{c.f(v)}</td>;
                  })}
                  <td style={{ padding: '0 10px' }}>
                    {s.degradation_level && s.degradation_level !== 'none'
                      ? <Badge group="degrade" value={degradeValue(s.degradation_level)} sm />
                      : <span style={{ color: 'var(--fg-mute)' }}>—</span>}
                  </td>
                  <td style={{ padding: '0 10px' }}>
                    <Badge
                      group="run"
                      value={s.status === 'failed' ? '失败' : s.status === 'degraded' ? '降级' : '成功'}
                      sm
                    />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {excluded.length > 0 && (
          <div style={{ margin: 16, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
            <SectionHead title="被排除条目" meta={`${excluded.length} 个未参与`} />
            {excluded.map((e) => (
              <div key={e.item_id} style={{ padding: '8px 14px', borderBottom: '1px solid var(--border-faint)', display: 'flex', alignItems: 'center', gap: 10 }}>
                <span style={{ flex: 1, fontSize: 11.5, color: 'var(--fg-2)' }}>{e.label || e.item_id}</span>
                {e.settled_n != null && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>结算 {e.settled_n}</span>}
                <Badge group="exclude" value={excludeValue(e.reason)} sm />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── §5.3 相关性 ─────────────────────────────────────────────────────────────
function CorrTab({ id, snapshots, activeSid, setSid, snap }) {
  const corrQ = useQuery({
    queryKey: ['basket.correlation', id, activeSid],
    queryFn: () => endpoints.basketCorrelation(id, activeSid),
    enabled: !!activeSid && !!snap?.has_correlation,
  });

  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)' }}>快照</span>
        <select
          value={activeSid || ''}
          onChange={(e) => setSid(e.target.value)}
          style={{ height: 26, padding: '0 8px', background: 'var(--surface-2)', border: '1px solid var(--border-strong)', borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--fg)' }}
        >
          {snapshots.map((s, i) => {
            const v = s.snapshot_id || s.id;
            return <option key={v} value={v}>as_of {s.asOf} · rev {s.rev}{i === 0 ? ' · 最新' : ''}</option>;
          })}
        </select>
        <div style={{ flex: 1 }} />
        {corrQ.data && (
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)' }}>方法 {corrQ.data.method} · 阈值 {fmtNum(corrQ.data.threshold, 2)} · {(corrQ.data.order || []).length} 条目（只读，改在「配置与变更」）</span>
        )}
      </div>

      {!snap?.has_correlation ? (
        <EmptyState
          icon="◇"
          title="本快照未计算相关性"
          desc="这一代运行没有计算篮内相关性。你可以发起一次「仅相关性」运行（mode=corr_only）来补算。"
        />
      ) : corrQ.isLoading ? (
        <Loading />
      ) : corrQ.isError ? (
        <ErrorState error={corrQ.error} onRetry={corrQ.refetch} />
      ) : !corrQ.data ? (
        <Empty label="无相关性数据" />
      ) : (
        <CorrContent corr={corrQ.data} />
      )}
    </div>
  );
}

function CorrContent({ corr }) {
  const order = corr.order || (corr.labels || []).map((l) => l.item_key);
  const labelOf = {};
  (corr.labels || []).forEach((l) => { labelOf[l.item_key] = l.label; });
  const labels = order.map((k) => labelOf[k] || k);
  const matrix = corr.matrix || {};
  const highPairs = corr.high_pairs || [];
  const clusters = corr.clusters || [];

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1.3fr 1fr', gap: 16 }}>
      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
        <SectionHead title="相关性热力图" meta={`${order.length} 条目`} />
        <div style={{ padding: 14, overflow: 'auto' }}>
          <Heatmap order={order} labels={labels} matrix={matrix} />
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
          <SectionHead title="高相关对" meta={`ρ ≥ ${fmtNum(corr.threshold, 2)} · 降序`} />
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
            <thead>
              <tr style={{ background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}>
                {['条目 A', '条目 B', 'ρ', '样本'].map((h, i) => (
                  <th key={h} style={{ textAlign: i >= 2 ? 'right' : 'left', padding: '5px 12px', fontSize: 9, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', fontFamily: 'var(--font-mono)' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {highPairs.length ? highPairs.map((p, i) => (
                <tr key={i} style={{ borderBottom: '1px solid var(--border-faint)', height: 28 }}>
                  <td style={{ padding: '0 12px', color: 'var(--fg-2)' }}>{p.a_label || labelOf[p.a] || p.a}</td>
                  <td style={{ padding: '0 12px', color: 'var(--fg-2)' }}>{p.b_label || labelOf[p.b] || p.b}</td>
                  <td style={{ padding: '0 12px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--err)', fontWeight: 500 }}>{fmtNum(p.rho, 2)}</td>
                  <td style={{ padding: '0 12px', textAlign: 'right', fontFamily: 'var(--font-mono)', color: 'var(--fg-3)' }}>{p.n ?? '—'}</td>
                </tr>
              )) : (
                <tr><td colSpan={4} style={{ padding: 16, textAlign: 'center', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-4)' }}>无超阈值的高相关对</td></tr>
              )}
            </tbody>
          </table>
        </div>

        <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
          <SectionHead title="聚类簇" meta={`${clusters.length} 个簇`} />
          {clusters.length ? clusters.map((cl, i) => (
            <div key={i} style={{ padding: '8px 12px', borderBottom: '1px solid var(--border-faint)', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-4)' }}>簇 {i + 1}</span>
              {cl.map((k) => (
                <span key={k} style={{ fontSize: 11, color: 'var(--fg-2)', padding: '2px 6px', background: 'var(--surface-2)', borderRadius: 3 }}>{labelOf[k] || k}</span>
              ))}
            </div>
          )) : (
            <div style={{ padding: 14, fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-4)' }}>无聚类簇</div>
          )}
        </div>
      </div>
    </div>
  );
}

// Pure inline-SVG heatmap. N×N grid, cell color interpolated by |rho|, diagonal=1.
function Heatmap({ order, labels, matrix }) {
  const n = order.length;
  if (!n) return <Empty label="无条目" />;
  const cell = n > 12 ? 22 : n > 8 ? 30 : 38;
  const pad = 96; // label gutter
  const w = pad + n * cell + 8;
  const h = pad + n * cell + 8;

  // interpolate |rho| → warm color (0 → surface, 1 → strong red).
  const color = (rho) => {
    if (rho == null) return 'var(--surface-2)';
    const a = Math.min(1, Math.abs(Number(rho)));
    // blend between a light neutral and an error-red using rgba alpha.
    return `rgba(180,60,48,${(0.10 + a * 0.85).toFixed(3)})`;
  };

  return (
    <svg width={w} height={h} style={{ fontFamily: 'var(--font-mono)' }}>
      {/* column labels (rotated) */}
      {order.map((k, j) => (
        <text
          key={`c${k}`}
          x={pad + j * cell + cell / 2}
          y={pad - 6}
          transform={`rotate(-55 ${pad + j * cell + cell / 2} ${pad - 6})`}
          fontSize={9}
          fill="var(--fg-3)"
          textAnchor="start"
        >
          {trunc(labels[j], 12)}
        </text>
      ))}
      {/* rows */}
      {order.map((rk, i) => (
        <g key={`r${rk}`}>
          <text x={pad - 6} y={pad + i * cell + cell / 2 + 3} fontSize={9} fill="var(--fg-3)" textAnchor="end">
            {trunc(labels[i], 12)}
          </text>
          {order.map((ck, j) => {
            const rho = i === j ? 1 : (matrix[rk] ? matrix[rk][ck] : null);
            return (
              <g key={`${rk}-${ck}`}>
                <rect
                  x={pad + j * cell}
                  y={pad + i * cell}
                  width={cell - 1.5}
                  height={cell - 1.5}
                  fill={i === j ? 'var(--surface-3)' : color(rho)}
                  stroke="var(--border-faint)"
                />
                {cell >= 30 && rho != null && (
                  <text
                    x={pad + j * cell + (cell - 1.5) / 2}
                    y={pad + i * cell + (cell - 1.5) / 2 + 3}
                    fontSize={8.5}
                    fill={Math.abs(Number(rho)) > 0.5 ? '#fff' : 'var(--fg-2)'}
                    textAnchor="middle"
                  >
                    {Number(rho).toFixed(2)}
                  </text>
                )}
              </g>
            );
          })}
        </g>
      ))}
    </svg>
  );
}
function trunc(s, n) {
  const str = String(s ?? '');
  return str.length > n ? `${str.slice(0, n - 1)}…` : str;
}

// ─── §5.4 运行历史 ───────────────────────────────────────────────────────────
function RunsTab({ id, runsQ, repair, qc }) {
  if (runsQ.isLoading) return <Loading />;
  if (runsQ.isError) return <ErrorState error={runsQ.error} onRetry={runsQ.refetch} />;
  const runs = runsQ.data?.items || [];
  if (!runs.length) return <EmptyState icon="▶" title="暂无运行记录" desc="这个篮子还没有发起过任何运行。" />;

  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-2)' }}>代次时间线 · 新在上</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {runs.map((r) => {
          const rid = r.brun_id || r.id;
          const sub = r.sub || {};
          return (
            <div key={rid} style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 180 }}>
                <div style={{ display: 'flex', gap: 5 }}>
                  <Badge group="mode" value={r.mode} sm />
                  <Badge group="trigger" value={r.trigger} sm />
                </div>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>窗口 {r.win} · as_of {r.asOf}</span>
              </div>
              <Badge group="run" value={r.status} />
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)', display: 'flex', gap: 12 }}>
                <span>耗时 {r.dur || '—'}</span>
                <span>
                  子任务 <span style={{ color: 'var(--ok)' }}>成{sub.ok ?? 0}</span> · <span style={{ color: 'var(--warn)' }}>降{sub.deg ?? 0}</span> · <span style={{ color: 'var(--err)' }}>失{sub.fail ?? 0}</span>
                </span>
              </div>
              <div style={{ flex: 1 }} />
              <Link to={`/factors/baskets/${id}/runs/${rid}`} className="btn sm ghost" style={{ textDecoration: 'none' }}>查看详情 →</Link>
              {(sub.fail ?? 0) > 0 && (
                <button
                  className="btn sm"
                  onClick={async () => { await repair(rid); qc.invalidateQueries({ queryKey: ['basket.runs', id] }); }}
                >
                  重跑失败条目
                </button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── §5.5 配置与变更 ─────────────────────────────────────────────────────────
function ConfigTab({ id, b, revsQ, qc }) {
  const [edit, setEdit] = React.useState(false);
  const c = b.common || {};
  const win = c.window
    ? (c.window.mode === 'rolling' ? `滚动 ${c.window.len || c.window.span || ''}` : `固定 ${c.window.start} ~ ${c.window.end}`)
    : '—';

  const chips = [
    ['股票池', c.pool], ['时间窗口', win], ['调仓频率', c.freq], ['主周期', c.mainCycle],
    ['收益口径', c.returnDef], ['IC 类型', c.icType],
    ['市值中性化', c.mktNeutral ? (c.mktNeutral.on ? (c.mktNeutral.scope || '开') : '关闭') : '—'],
    ['行业中性化', c.indNeutral ? (c.indNeutral.on ? `${c.indNeutral.system || ''} · ${c.indNeutral.level ?? ''}` : '关闭') : '—'],
    ['分层', c.layers != null ? `${c.layers} 层 · ${c.layerWeight || ''}` : '—'],
    ['交易成本', c.cost], ['前向周期', Array.isArray(c.fwdPeriods) ? c.fwdPeriods.join(' · ') : c.fwdPeriods],
  ].filter(([, v]) => v != null && v !== '');

  const items = b.items || [];

  return (
    <div style={{ padding: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-2)' }}>当前修订 rev {b.rev ?? b.active_revision ?? '—'} · 只读</span>
        <div style={{ flex: 1 }} />
        <button className="btn sm" onClick={() => setEdit(true)}>编辑</button>
      </div>

      <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
        <SectionHead title="共享参数（口径）" meta="篮内强制统一" />
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, padding: 12 }}>
          {chips.length ? chips.map(([k, v]) => (
            <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, padding: '4px 8px', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 4, fontSize: 11 }}>
              <span style={{ color: 'var(--fg-3)' }}>{k}</span>
              <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--fg)', fontWeight: 500 }}>{v}</span>
            </span>
          )) : <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-4)' }}>无共享参数</span>}
        </div>
      </div>

      <div style={{ marginTop: 14, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
        <SectionHead title="条目清单" meta={`${items.length} 条目 · 只读`} />
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11.5 }}>
          <thead>
            <tr style={{ background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}>
              {['标签', '因子', '版本', '参数'].map((h) => (
                <th key={h} style={{ textAlign: 'left', padding: '6px 14px', fontSize: 9.5, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', fontFamily: 'var(--font-mono)' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map((e) => (
              <tr key={e.item_id || e.item_key} style={{ borderBottom: '1px solid var(--border-faint)', height: 32 }}>
                <td style={{ padding: '0 14px', color: 'var(--fg)', fontWeight: 500 }}>{e.label || e.item_id}</td>
                <td style={{ padding: '0 14px', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-2)' }}>{e.factor_id}</td>
                <td style={{ padding: '0 14px', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)' }}>{e.version ?? '—'}</td>
                <td style={{ padding: '0 14px' }}><ParamChips params={e.params} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <RevisionHistory revsQ={revsQ} />

      {edit && (
        <ConfigEditModal id={id} b={b} qc={qc} onClose={() => setEdit(false)} />
      )}
    </div>
  );
}

function RevisionHistory({ revsQ }) {
  return (
    <div style={{ marginTop: 14, background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
      <SectionHead title="修订历史" meta="无回滚按钮 · 通过新建修订回到旧配置" />
      {revsQ.isLoading ? (
        <Loading />
      ) : revsQ.isError ? (
        <ErrorState error={revsQ.error} onRetry={revsQ.refetch} />
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
          <thead>
            <tr style={{ background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}>
              {['rev', '时间', '作者', '原因', '影响范围', '变更'].map((h) => (
                <th key={h} style={{ textAlign: 'left', padding: '6px 12px', fontSize: 9, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', fontFamily: 'var(--font-mono)' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(revsQ.data?.items || []).map((r) => (
              <tr key={r.revision ?? r.rev} style={{ borderBottom: '1px solid var(--border-faint)', verticalAlign: 'top' }}>
                <td style={{ padding: '8px 12px', fontFamily: 'var(--font-mono)', color: 'var(--fg)', fontWeight: 500 }}>rev {r.rev ?? r.revision}</td>
                <td style={{ padding: '8px 12px', fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>{r.time}</td>
                <td style={{ padding: '8px 12px', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-2)' }}>{r.author}</td>
                <td style={{ padding: '8px 12px', fontSize: 11, color: 'var(--fg-2)' }}>{r.reason || '—'}</td>
                <td style={{ padding: '8px 12px', fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>{r.scope || '—'}</td>
                <td style={{ padding: '8px 12px' }}>
                  <span style={{ display: 'inline-flex', gap: 5, flexWrap: 'wrap' }}>
                    {(r.changes || []).map((ch, i) => {
                      const col = ch.level === 'red' ? 'var(--err)' : ch.level === 'yellow' ? 'var(--warn)' : 'var(--fg-2)';
                      return (
                        <span key={i} style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 10, color: col }}>
                          <span style={{ width: 6, height: 6, borderRadius: '50%', background: ch.level === 'red' ? 'var(--err)' : ch.level === 'yellow' ? 'var(--warn)' : 'var(--fg-4)' }} />
                          {ch.field}
                        </span>
                      );
                    })}
                    {!(r.changes || []).length && <span style={{ color: 'var(--fg-mute)' }}>—</span>}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function ConfigEditModal({ id, b, qc, onClose }) {
  const [text, setText] = React.useState(() => JSON.stringify(b.shared_config ?? {}, null, 2));
  const [reason, setReason] = React.useState('');
  const [parseErr, setParseErr] = React.useState(null);
  const [result, setResult] = React.useState(null);

  const saveMut = useMutation({
    mutationFn: (body) => endpoints.saveBasketRevision(id, body),
    onSuccess: (resp) => {
      setResult(resp);
      qc.invalidateQueries({ queryKey: ['basket', id] });
      qc.invalidateQueries({ queryKey: ['basket.revisions', id] });
    },
  });

  const onSave = () => {
    let parsed;
    try {
      parsed = JSON.parse(text);
      setParseErr(null);
    } catch (err) {
      setParseErr(err.message);
      return;
    }
    saveMut.mutate({ shared_config: parsed, items: b.items, reason });
  };

  const saveErr = saveMut.error?.apiError?.message || (saveMut.isError ? String(saveMut.error) : null);

  return (
    <Modal
      width={620}
      title="编辑配置 · 新建修订"
      onClose={onClose}
      footer={(
        <>
          <span style={{ flex: 1, fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)' }}>保存后修订号 +1，可能触发重算</span>
          <button className="btn sm" onClick={onClose}>{result ? '关闭' : '取消'}</button>
          {!result && (
            <button className="btn primary sm" disabled={saveMut.isPending} onClick={onSave}>
              {saveMut.isPending ? '保存中…' : '保存'}
            </button>
          )}
        </>
      )}
    >
      {result ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ fontSize: 12, color: 'var(--fg-2)' }}>已保存为 <b style={{ color: 'var(--fg)' }}>rev {result.revision}</b>。</div>
          <div style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 5, padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 11.5 }}>
              <span style={{ color: 'var(--fg-3)' }}>失效（invalidation）</span>
              <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--fg)' }}>{String(result.invalidation ?? '—')}</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 11.5 }}>
              <span style={{ color: 'var(--fg-3)' }}>重算范围（stale_scope）</span>
              <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--fg)' }}>{String(result.stale_scope ?? '—')}</span>
            </div>
            {Array.isArray(result.stale_items) && result.stale_items.length > 0 && (
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, fontSize: 11.5 }}>
                <span style={{ color: 'var(--fg-3)' }}>受影响条目</span>
                <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--fg)' }}>{result.stale_items.length} 个</span>
              </div>
            )}
          </div>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {(parseErr || saveErr) && (
            <div style={{ padding: '6px 12px', background: 'var(--err-bg)', border: '1px solid var(--err)', borderRadius: 4, fontSize: 11, color: 'var(--err)' }}>
              {parseErr ? `JSON 解析失败：${parseErr}` : `保存失败：${saveErr}`}
            </div>
          )}
          <div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>shared_config (JSON)</div>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              spellCheck={false}
              style={{ width: '100%', height: 280, padding: 10, background: 'var(--surface-2)', border: '1px solid var(--border-strong)', borderRadius: 4, fontFamily: 'var(--font-mono)', fontSize: 11.5, color: 'var(--fg)', resize: 'vertical', lineHeight: 1.6 }}
            />
          </div>
          <div>
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>原因（可空）</div>
            <input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="例如：缩小股票池以聚焦大盘"
              style={{ width: '100%', height: 32, padding: '0 10px', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 4, fontSize: 11.5, color: 'var(--fg)' }}
            />
          </div>
        </div>
      )}
    </Modal>
  );
}
