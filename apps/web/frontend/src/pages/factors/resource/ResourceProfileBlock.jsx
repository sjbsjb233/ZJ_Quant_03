// ResourceProfileBlock.jsx — 单因子运行透视页「资源画像」区块的数据驱动编排器
// (资源画像方案 §7.1). Wires real factor-api payloads into the ported design
// components, drives §3.9 state matrix + 三角联动 (选区 → 选区统计 / 阶段高亮 /
// 火焰图重渲染) + lazy 全分辨率/剖析加载.
import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { endpoints } from '../../../lib/api';
import { useAuth } from '../../../lib/auth';
import { RPPanel, RPEmpty } from './rpShared';
import { RPTitleRow, RPCards, RPStageBreakdown, RPBaselineCompare, RPHistoryTrend, RPConcurrency } from './rpBlocks';
import { RPTimeline, RPBrushNav, RPSelStats, RPTimelineLegend } from './rpTimeline';
import { RPFlamePanel } from './rpFlame';
import { traceToRun, applyFullres, profileToFlame } from './rpTransform';

// measure the container so the SVG charts fill the available width responsively.
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

export default function ResourceProfileBlock({ trace, run: factorRun, onRerun, onOpenRun }) {
  const [ref, width] = useWidth();
  const narrow = width < 1180;
  const chartW = Math.max(520, width - 32);

  const status = factorRun?.status || trace?.status;
  const runId = trace?.run_id;
  const factorId = trace?.factor_id || factorRun?.factor_id;
  const res = trace?.resources;

  const [sel, setSelRaw] = React.useState(null);
  const [wantFull, setWantFull] = React.useState(false);
  const [fineLoading, setFineLoading] = React.useState(false);
  const everZoomed = React.useRef(false);

  const sig = res?.summary?.signature;
  const classSig = res?.summary?.class_signature;
  const { hasRole } = useAuth();
  const isAdmin = hasRole && hasRole('admin');

  const baselineQ = useQuery({
    queryKey: ['rp.baseline', runId, sig],
    enabled: !!sig && !!isAdmin,
    queryFn: () => endpoints.factorResourceBaseline(factorId, sig, classSig),
    retry: false, staleTime: 30000,
  });
  const hasProfile = !!res?.has_profile;
  const profileQ = useQuery({
    queryKey: ['rp.profile', runId],
    enabled: hasProfile,
    queryFn: () => endpoints.runProfile(runId),
    retry: false, staleTime: 60000,
  });
  const fullresQ = useQuery({
    queryKey: ['rp.fullres', runId],
    enabled: wantFull && !!res?.has_fullres,
    queryFn: () => endpoints.runResourceSamples(runId),
    retry: false, staleTime: 60000,
  });

  function setSel(s) {
    if (s && !everZoomed.current && res?.has_fullres) {
      everZoomed.current = true;
      setWantFull(true);
      setFineLoading(true);
    }
    setSelRaw(s);
  }
  React.useEffect(() => {
    if (fullresQ.data || fullresQ.isError) {
      const t = setTimeout(() => setFineLoading(false), 200);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [fullresQ.data, fullresQ.isError]);

  // ── states that short-circuit the full block ──────────────────────────────
  const wrap = (children) => (
    <div ref={ref} data-testid="rp-block" style={{ display: 'flex', flexDirection: 'column', gap: 12, padding: '14px 16px', background: 'var(--bg)' }}>
      {children}
    </div>
  );

  if (!res) {
    // trace exists但无 resources节 — pre-feature run or sampling off.
    const running = status === 'queued' || status === 'running';
    return wrap(
      <>
        <RPTitleRow level="exact" run={{ id: runId || '—', factor: factorId || '—', pool: '—', win: '—' }} />
        <RPPanel>
          {running ? (
            <RPEmpty running title="运行结束后生成资源画像" desc="本期不提供实时画像；运行进入终态后自动生成本区块全部内容。" />
          ) : (
            <RPEmpty icon="∅" title="该运行早于资源画像功能上线，无采集数据"
              desc="历史运行不会回填资源数据。重跑一次即可获得完整的资源画像。"
              action={onRerun ? <button className="btn sm" onClick={() => onRerun(false)}>↻ 重跑该运行</button> : null} />
          )}
        </RPPanel>
      </>,
    );
  }

  let runData = traceToRun(trace, baselineQ.data, profileQ.data);
  if (fullresQ.data && fullresQ.data.available) runData = applyFullres(runData, fullresQ.data);
  const flame = profileToFlame(profileQ.data);

  const level = runData.level;
  const n = runData.n;
  const hasTimeline = (runData.series || []).length > 0;
  const degraded = !hasTimeline && (runData.stages || []).length > 0;
  const perfState = runData.perfState;

  const flameMode = hasProfile
    ? (profileQ.isLoading ? 'loading' : (flame ? 'data' : 'off'))
    : 'off';

  return wrap(
    <>
      <RPTitleRow level={level} n={n} run={runData} degradedTag={degraded} />
      <RPCards run={runData} perfState={perfState} perfN={runData.perfN} narrow={narrow} />

      {hasTimeline && !degraded && (
        <RPPanel title="资源时间线" meta="RSS 随运行内相对时间 · 本区块视觉重心">
          <div style={{ padding: '6px 12px 10px', position: 'relative' }}>
            <RPTimeline run={runData} sel={sel} onSel={setSel} width={chartW} height={narrow ? 240 : 280} level={level} fineLoading={fineLoading} />
            <RPBrushNav run={runData} sel={sel} onSel={setSel} width={chartW} />
            <RPTimelineLegend level={level} />
          </div>
        </RPPanel>
      )}
      {hasTimeline && !degraded && sel && <RPSelStats run={runData} sel={sel} onClear={() => setSel(null)} />}

      <RPStageBreakdown run={runData} sel={degraded ? null : sel} />

      <div style={{ display: 'grid', gridTemplateColumns: narrow ? '1fr' : '1.1fr 1.2fr', gap: 12 }}>
        <RPBaselineCompare run={runData} building={perfState === 'building'} buildingN={runData.perfN} />
        <RPHistoryTrend run={runData} w={narrow ? width - 60 : Math.round(width * 0.5)} onPick={onOpenRun} />
      </div>

      {level === 'shared' && <RPConcurrency run={runData} n={n} />}

      <RPFlamePanel run={runData} flame={flame} sel={degraded ? null : sel} mode={flameMode} width={chartW} onRerun={onRerun ? () => onRerun(true) : undefined} />
    </>,
  );
}
