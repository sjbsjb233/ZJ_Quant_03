// rpTransform.js — map the factor-api resource payloads (trace.resources / baseline
// / fullres samples / profile / basket resources) into the design components' data
// shapes (the shapes rp-data.js produced). Keeps the ported components data-agnostic.
import { rpMMSS } from './rpFormat';

export function attrLevelN(attribution) {
  if (!attribution || attribution === 'exact') return { level: 'exact', n: 1 };
  const m = /shared-(\d+)/.exec(attribution);
  return { level: 'shared', n: m ? parseInt(m[1], 10) : 2 };
}

function winLabel(meta) {
  if (meta && meta.start && meta.end) {
    const y = (new Date(meta.end) - new Date(meta.start)) / (365 * 864e5);
    if (!Number.isFinite(y) || y <= 0) return '—';
    return y >= 1 ? `${Math.round(y)} 年` : `${Math.max(1, Math.round(y * 12))} 月`;
  }
  return '—';
}

// ── single-factor run (页面一) ───────────────────────────────────────────────
export function traceToRun(trace, baseline, profile) {
  const res = trace.resources || {};
  const sum = res.summary || {};
  const meta = trace.meta || {};
  const { level, n } = attrLevelN(res.attribution);

  const stages = (res.stages || []).map((s) => ({ id: s.stage, from: s.from, to: s.to }));
  const series = (res.samples || []).map(([t, rss, cpu, arrow, inflight]) => ({
    t, rss, cpu, arrow, conc: inflight, concShared: inflight,
  }));
  const events = (res.admission || []).map((e) => ({
    t: e.ts,
    kind: e.event === 'granted' ? 'release' : 'delay',
    label: e.event === 'granted'
      ? (e.wait_ms ? `准入放行 · 等待 ${(e.wait_ms / 1000).toFixed(1)}s` : '准入放行')
      : (e.event === 'draining' ? '准入排干（资源紧张）' : '准入延迟'),
    time: rpMMSS(e.ts),
  }));
  const cards = {
    peak: sum.peak_rss_mb || 0,
    cpuS: sum.cpu_sec || 0,
    cpuNorm: sum.cpu_sec_per_mrow != null ? `${sum.cpu_sec_per_mrow} s/百万行` : '—',
    avgCpu: Math.round(sum.avg_cpu_pct || 0),
    threads: sum.threads || 1,
    memNorm: sum.peak_per_mrow || 0,
    p50n: sum.baseline_p50_norm, p90n: sum.baseline_p90_norm,
  };
  const stageRows = (res.stages || []).map((s) => ({
    id: s.stage, cpu: s.cpu_sec || 0, mem: (s.rss_end_mb || 0) - (s.rss_start_mb || 0),
  }));
  const baselineCompare = [
    { l: '本次', v: sum.peak_per_mrow, self: true },
    { l: '同因子 p50', v: sum.baseline_p50_norm },
    { l: '同因子 p90', v: sum.baseline_p90_norm },
    { l: '同类 p50', v: sum.class_p50_norm },
    { l: '同类 p90', v: sum.class_p90_norm },
  ];
  const recent = (baseline && baseline.recent) || [];
  const history = recent.map((p) => ({
    id: 'r_' + (p.run_id || '').slice(-4),
    v: p.peak_rss_mb, current: p.run_id === trace.run_id, run_id: p.run_id,
  }));
  const concTasks = (res.concurrent_tasks || []).map((t) => ({
    name: t.name, type: t.type, overlap: `${rpMMSS(t.from)} – ${rpMMSS(t.to)}`,
  }));
  const profiling = profile && profile.profiling
    ? { hz: profile.profiling.hz_actual, samples: profile.profiling.samples }
    : { hz: '—', samples: 0 };

  return {
    id: trace.run_id,
    factor: (trace.factor_id || meta.factor || '') + (trace.version ? '@' + trace.version : ''),
    pool: meta.universe_id || '—',
    win: winLabel(meta),
    T: res.duration_s || (series.length ? series[series.length - 1].t : 1),
    stages, series,
    p50: sum.baseline_p50_abs_mb != null ? sum.baseline_p50_abs_mb : null,
    p90: sum.baseline_p90_abs_mb != null ? sum.baseline_p90_abs_mb : null,
    events, cards, stageRows, baselineCompare, history, concTasks, profiling,
    attribution: res.attribution, level, n,
    perfState: sum.perf_state || 'normal',
    perfN: sum.baseline_sample_n || 0,
    signature: sum.signature, classSignature: sum.class_signature,
    hasFullres: !!res.has_fullres, hasProfile: !!res.has_profile,
  };
}

// Swap in the full-resolution 1Hz series (loaded lazily on first zoom, §3.4).
export function applyFullres(run, fullres) {
  if (!fullres || !fullres.samples || !fullres.samples.length) return run;
  const series = fullres.samples.map(([t, rss, cpu, arrow, inflight]) => ({
    t, rss, cpu, arrow, conc: inflight, concShared: inflight,
  }));
  return { ...run, series };
}

export function profileToFlame(profile) {
  return (profile && profile.tree) || null;
}

// ── basket run (页面二) ──────────────────────────────────────────────────────
export function basketToBk(api, meta) {
  const totals = api.totals || {};
  const top = totals.costliest_factor || {};
  const factors = (api.tasks || []).map((t) => ({
    name: t.name, ver: t.ver, from: t.from, to: t.to, cpu: t.cpu,
    state: t.state, mem: t.mem, legacy: t.legacy, run_id: t.run_id,
  }));
  const series = (api.timeline || []).map(([t, rss]) => ({ t, rss }));
  return {
    id: meta.brunId,
    basket: meta.basketName || '—',
    pool: meta.pool || '—',
    win: meta.win || '—',
    T: (api.window && api.window.T) || (series.length ? series[series.length - 1].t : 1),
    series,
    factors,
    cards: {
      peak: totals.peak_rss_mb || 0,
      limit: meta.limit || 0,
      limitPct: meta.limit ? Math.round((totals.peak_rss_mb || 0) / meta.limit * 100) : 0,
      cpuTotal: totals.cpu_sec_sum || 0,
      top: { name: top.name, cpu: top.cpu, run_id: top.run_id },
      concPeak: totals.concurrent_max || 1,
      attr: totals.concurrent_max || 1,
    },
    admission: api.admission || [],
  };
}
