// BasketResourceBlock.jsx — 篮子运行详情页「篮子资源画像」区块 (资源画像方案 §7.2).
// 总曲线与甘特严格共用横轴;框选两图同步缩放;甘特/成本表点击跳子 run 详情(落点 =
// 页面一资源画像区块).
import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { endpoints } from '../../../lib/api';
import { RPPanel, RPEmpty } from './rpShared';
import { RPBrushNav } from './rpTimeline';
import { rpMMSS } from './rpFormat';
import { BKCards, BKSharedAxis, BKLegend, BKCostTable } from './rpBasket';
import { basketToBk } from './rpTransform';

function useWidth(initial = 1100) {
  const ref = React.useRef(null);
  const [w, setW] = React.useState(initial);
  React.useEffect(() => {
    if (!ref.current || typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver((entries) => {
      const cw = entries[0]?.contentRect?.width;
      if (cw) setW(Math.max(560, Math.round(cw)));
    });
    ro.observe(ref.current);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

export default function BasketResourceBlock({ basketId, brunId, brun, basketName }) {
  const [ref, width] = useWidth();
  const navigate = useNavigate();
  const narrow = width < 1180;
  const chartW = Math.max(520, width - 32);
  const [sel, setSel] = React.useState(null);

  const status = brun?.status_raw || brun?.status;
  const live = status === 'queued' || status === 'running';

  const q = useQuery({
    queryKey: ['basket.resources', basketId, brunId],
    queryFn: () => endpoints.basketRunResources(basketId, brunId),
    enabled: !!basketId && !!brunId && !live,
    retry: false, staleTime: 15000,
  });

  // Click a gantt bar / cost row → the factor's run-detail, 运行透视 tab, that run
  // selected — 落点即页面一资源画像区块 (design). Needs the run's factor_id, which
  // the basket resources payload carries per task.
  const openRun = (runId) => {
    if (!runId) return;
    const fid = (q.data?.tasks || []).find((t) => t.run_id === runId)?.factor_id;
    if (fid) navigate(`/factors/library/${fid}?tab=runscope&run=${runId}`);
  };

  const Title = ({ bk }) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, minHeight: 26 }}>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 14, fontWeight: 500, color: 'var(--fg)' }}>篮子资源画像</span>
      <div style={{ flex: 1 }}></div>
      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)', display: 'inline-flex', gap: 8 }}>
        <span style={{ color: 'var(--fg-2)' }}>{brunId}</span>
        {basketName && <><span style={{ color: 'var(--fg-mute)' }}>·</span><span>{basketName}</span></>}
        {bk && <><span style={{ color: 'var(--fg-mute)' }}>·</span><span>{bk.factors.length} 因子{brun?.win ? ` · ${brun.win}` : ''}</span></>}
      </span>
    </div>
  );

  const wrap = (children) => (
    <div ref={ref} data-testid="bk-block" style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: 16, background: 'var(--bg)', borderRadius: 6 }}>
      {children}
    </div>
  );

  if (live) {
    return wrap(<><Title /><RPPanel><RPEmpty running title="篮子运行结束后生成资源画像" desc="本期不提供实时画像；篮子进入终态后自动生成总曲线、甘特与成本排行。" /></RPPanel></>);
  }
  if (q.isLoading) {
    return wrap(<><Title /><RPPanel><RPEmpty running title="加载篮子资源画像…" /></RPPanel></>);
  }
  if (q.isError || !q.data || q.data.available === false) {
    return wrap(<><Title /><RPPanel><RPEmpty icon="∅" title="该篮子运行早于资源画像功能上线，无采集数据"
      desc="历史运行不会回填资源数据。重跑一次即可获得完整的篮子资源画像。" /></RPPanel></>);
  }

  const bk = basketToBk(q.data, { brunId, basketName, win: brun?.win });
  const hasMixed = bk.factors.some((f) => f.legacy);

  return wrap(
    <>
      <Title bk={bk} />
      {hasMixed && (
        <div style={{ padding: '6px 12px', background: 'var(--info-bg)', border: '1px solid var(--info)', borderRadius: 4, fontSize: 11, color: 'var(--fg-2)', display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ color: 'var(--info)' }}>ⓘ</span>
          部分子任务早于资源画像上线：甘特仍按真实起止绘制，成本表中 CPU 与内存列显示「—」，不构成错误。
        </div>
      )}
      <BKCards bk={bk} narrow={narrow} onOpenRun={openRun} />
      <RPPanel title="总曲线 + 子任务甘特" meta="共享同一时间轴 · 看并发叠加如何推高总内存">
        <div style={{ padding: '8px 12px 10px', position: 'relative' }}>
          <BKSharedAxis bk={bk} factors={bk.factors} sel={sel} onSel={setSel} width={chartW} narrow={narrow} onOpenRun={openRun} />
          <RPBrushNav run={{ T: bk.T, series: bk.series, stages: [] }} series={bk.series} stages={[]}
            sel={sel} onSel={setSel} width={chartW} padL={narrow ? 110 : 138} padR={50} />
          <BKLegend />
        </div>
      </RPPanel>
      {sel && (
        <div style={{
          display: 'flex', alignItems: 'center', gap: 14, padding: '5px 12px',
          background: 'var(--info-bg)', border: '1px solid var(--info)', borderRadius: 4,
          fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-2)',
        }}>
          <span style={{ color: 'var(--info)', fontWeight: 500 }}>◧ 选区 {rpMMSS(sel.t0)} – {rpMMSS(sel.t1)}</span>
          <span>总曲线与甘特已同步缩放 · 甘特条保持最小可点击宽度</span>
          <div style={{ flex: 1 }}></div>
          <button className="btn ghost sm" onClick={() => setSel(null)} style={{ color: 'var(--info)' }}>✕ 清除选区</button>
        </div>
      )}
      <BKCostTable factors={bk.factors} onOpenRun={openRun} />
    </>,
  );
}
