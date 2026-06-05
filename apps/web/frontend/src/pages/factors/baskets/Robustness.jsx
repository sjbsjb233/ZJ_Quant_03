// /factors/:factorId/robustness — §11.8 跨篮稳健性视图 (因子维度, READ-ONLY).
// The ONLY place multiple baskets sit on one screen. No sort / no checkbox /
// no scoring / no 建集合. Answers "is this factor stable across calibrations".
// Ported from the Claude Design mockup fl-robustness.jsx into the real shell.
import React from 'react';
import {
  useParams, useNavigate, useSearchParams, Link,
} from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { endpoints } from '../../../lib/api';
import { TerminalShell } from '../../../components/shell';
import { Loading, ErrorState, Empty } from '../../../components/ui';
import { ParamChips, SectionHead, fmtNum, fmtPct } from './kit';

// Metric columns shown per basket row (§11.8).
const METRIC_COLS = [
  { k: 'icir_annual', l: 'ICIR(年化)', f: (v) => fmtNum(v, 2) },
  { k: 'rank_ic_mean', l: 'RankIC均值', f: (v) => fmtNum(v, 3) },
  { k: 'ic_win_rate', l: 'IC胜率', f: (v) => fmtPct(v, 1) },
  { k: 'ls_sharpe', l: '多空夏普', f: (v) => fmtNum(v, 2) },
  { k: 'ls_max_drawdown', l: '最大回撤', f: (v) => fmtPct(v, 1) },
  { k: 'turnover', l: '换手', f: (v) => fmtPct(v, 1) },
  { k: 'coverage', l: '覆盖', f: (v) => fmtPct(v, 1) },
];

export default function Robustness() {
  const { factorId } = useParams();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const basketId = searchParams.get('basket_id') || undefined;

  const q = useQuery({
    queryKey: ['factorRobustness', factorId, basketId || null],
    queryFn: () => endpoints.factorRobustness(factorId, basketId),
  });

  const data = q.data;
  const rows = data?.rows || [];

  return (
    <TerminalShell
      module="factor"
      current="library"
      path={['factor_mgmt/', '稳健性', factorId]}
      title={`稳健性视图 · ${factorId}`}
      subtitle="跨篮只读 · 唯一合法跨口径并排"
      actions={(
        <Link to={`/factors/library/${factorId}`} className="btn ghost sm" style={{ textDecoration: 'none' }}>← 因子库</Link>
      )}
    >
      {/* PROMINENT, persistent header note */}
      <div style={{ padding: '8px 18px', background: 'var(--info-bg)', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
        <span style={{ color: 'var(--info)' }}>ⓘ</span>
        <span style={{ fontSize: 11.5, color: 'var(--fg-2)' }}>
          <b style={{ color: 'var(--fg)' }}>跨口径数据，仅观察稳定性，不构成排名</b>
          <span style={{ color: 'var(--fg-3)', marginLeft: 8 }}>· 刻意不提供排序 / 勾选 / 评分 / 由此创建集合的入口</span>
        </span>
      </div>

      {q.isLoading ? (
        <Loading />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={q.refetch} />
      ) : rows.length === 0 ? (
        <Empty label="该因子未出现在任何篮子中" hint="把这个因子加入篮子后，可在此跨口径观察其稳定性" />
      ) : (
        <div className="scroll" style={{ flex: 1, minHeight: 0, background: 'var(--bg)', padding: 16 }}>
          <div style={{ fontSize: 11, color: 'var(--fg-3)', marginBottom: 12 }}>
            该因子出现在 <b style={{ color: 'var(--fg)' }}>{rows.length}</b> 个篮子中（各取最新快照）
            {data?.reference_basket && <span> · 基准篮 <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--fg-2)' }}>{data.reference_basket}</span></span>}
          </div>

          <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'auto' }}>
            <SectionHead title="跨口径表现" meta="行=篮子（各取最新快照）· 无排序按钮（产品规则）" />
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11.5 }}>
              <thead>
                <tr style={{ background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}>
                  {['篮子', '标签', 'as_of', '参数', '口径差异（相对基准）'].map((h) => (
                    <th key={h} style={{ textAlign: 'left', padding: '7px 14px', fontSize: 9.5, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap' }}>{h}</th>
                  ))}
                  {METRIC_COLS.map((c) => (
                    <th key={c.k} style={{ textAlign: 'right', padding: '7px 14px', fontSize: 9.5, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap' }}>{c.l}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((r, idx) => {
                  const rid = r.basket_id || idx;
                  const isRef = data?.reference_basket && r.basket_id === data.reference_basket;
                  const m = r.metrics || {};
                  return (
                    <tr key={rid} style={{ borderBottom: '1px solid var(--border-faint)', background: isRef ? 'var(--info-bg)' : 'transparent', verticalAlign: 'top' }}>
                      <td style={{ padding: '9px 14px' }}>
                        <span
                          onClick={() => r.basket_id && navigate(`/factors/baskets/${r.basket_id}`)}
                          style={{ fontWeight: 500, color: 'var(--fg)', cursor: r.basket_id ? 'pointer' : 'default' }}
                        >
                          {r.basket_name || r.basket_id || '—'}
                        </span>
                        {isRef && <span className="chip info" style={{ height: 14, fontSize: 8.5, marginLeft: 6 }}>基准</span>}
                      </td>
                      <td style={{ padding: '9px 14px', color: 'var(--fg-2)' }}>{r.label || '—'}</td>
                      <td style={{ padding: '9px 14px', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)', whiteSpace: 'nowrap' }}>{r.asOf || '—'}</td>
                      <td style={{ padding: '9px 14px' }}><ParamChips params={r.params} /></td>
                      <td style={{ padding: '9px 14px' }}><DiffChips diff={r.diff} isRef={isRef} /></td>
                      {METRIC_COLS.map((c) => (
                        <td key={c.k} style={{ padding: '9px 14px', textAlign: 'right', fontFamily: 'var(--font-mono)', fontVariantNumeric: 'tabular-nums', color: 'var(--fg-2)', whiteSpace: 'nowrap' }}>
                          {m[c.k] == null ? '—' : c.f(m[c.k])}
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {/* legend */}
          <div style={{ marginTop: 14, display: 'flex', gap: 16, flexWrap: 'wrap', fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>
            <span><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: 'var(--err)', marginRight: 5 }} />样本/收益定义级差异（考卷 · hard）</span>
            <span><span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: 'var(--warn)', marginRight: 5 }} />加工方式级差异（soft）</span>
          </div>
        </div>
      )}
    </TerminalShell>
  );
}

// ─── 口径差异 chips: one per diff field, colored by level. ────────────────────
function DiffChips({ diff, isRef }) {
  if (isRef) return <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-mute)' }}>—（基准）</span>;
  if (!diff || diff.length === 0) return <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--ok)' }}>口径一致</span>;
  return (
    <span style={{ display: 'inline-flex', gap: 5, flexWrap: 'wrap' }}>
      {diff.map((d, i) => {
        const col = d.level === 'hard' ? 'var(--err)' : 'var(--warn)';
        const bg = d.level === 'hard' ? 'var(--err-bg)' : 'var(--warn-bg)';
        return (
          <span
            key={`${d.field}-${i}`}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 4, height: 17, padding: '0 6px', borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 10, color: col, background: bg, whiteSpace: 'nowrap' }}
          >
            <span style={{ width: 6, height: 6, borderRadius: '50%', background: col }} />
            {d.field}: {String(d.ref)}→{String(d.cur)}
          </span>
        );
      })}
    </span>
  );
}
