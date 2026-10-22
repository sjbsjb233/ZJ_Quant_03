// Chart wrappers: ECharts (general) + KLineChart (financial). Both theme-aware.
// The Kline wrapper exposes the underlying KLineChart instance through a ref so
// the workstation can drive indicators / overlays / navigation / export without
// re-implementing what the library already provides (整改方案 v1.0 §3.1).
import React, { forwardRef, useEffect, useImperativeHandle, useMemo, useRef } from 'react';
import ReactECharts from 'echarts-for-react';
import { dispose, init, registerIndicator, registerOverlay } from 'klinecharts';
import { useTheme } from '../../lib/theme';
import { t } from '../../lib/i18n';

// Indicator name used to overlay extra close-price lines (multi-symbol compare /
// multi-source diff). The lines are fed via the indicator's `extendData`, which
// holds up to 5 numeric arrays aligned to the main data list by index.
export const COMPARE_INDICATOR = 'COMPARE';
// Market-cap sub-pane (VIZ Layer v2 §6.1): 流通市值 + 总市值 over time, sharing the
// price chart's x-axis. Fed via the indicator's extendData [circArr, totalArr].
export const MKTCAP_INDICATOR = 'MKTCAP';

const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

// red-up / green-down (China convention)
const UP = '#cc4334';
const DOWN = '#3aa776';

function decorateAxes(axis, fg3, border) {
  const apply = (a) => ({
    axisLine: { lineStyle: { color: border }, ...(a.axisLine || {}) },
    axisLabel: { color: fg3, fontFamily: cssVar('--font-mono'), fontSize: 10, ...(a.axisLabel || {}) },
    splitLine: { lineStyle: { color: border }, ...(a.splitLine || {}) },
    ...a,
  });
  if (Array.isArray(axis)) return axis.map(apply);
  return axis ? apply(axis) : axis;
}

// forwardRef so callers can grab the ECharts instance (getEchartsInstance) for
// PNG export etc. (整改方案 v1.0 §5.1).
export const EChart = forwardRef(function EChart({ option, style, height = '100%' }, ref) {
  const innerRef = useRef(null);
  const wrapRef = useRef(null);
  const { theme } = useTheme();
  const merged = useMemo(() => {
    const fg2 = cssVar('--fg-2');
    const fg3 = cssVar('--fg-3');
    const border = cssVar('--border');
    return {
      backgroundColor: 'transparent',
      textStyle: { color: fg2, fontFamily: cssVar('--font-mono') },
      grid: { left: 48, right: 18, top: 28, bottom: 28, ...(option.grid || {}) },
      tooltip: { backgroundColor: cssVar('--surface'), borderColor: border, textStyle: { color: fg2, fontSize: 11 }, ...(option.tooltip || {}) },
      legend: { textStyle: { color: fg3, fontSize: 10 }, ...(option.legend || {}) },
      ...option,
      xAxis: decorateAxes(option.xAxis, fg3, border),
      yAxis: decorateAxes(option.yAxis, fg3, border),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [option, theme]);
  useImperativeHandle(ref, () => ({
    getEchartsInstance: () => innerRef.current?.getEchartsInstance?.() || null,
  }));
  // Keep the chart sized to its (often flex/grid) container. echarts-for-react
  // only listens to window resize; panels that gain their width/height *after*
  // mount (e.g. a query resolving inside a 2-col grid) would otherwise stay
  // 0×0 and render no canvas. A ResizeObserver fixes that for every chart page.
  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const ro = new ResizeObserver(() => {
      const inst = innerRef.current?.getEchartsInstance?.();
      if (inst && el.clientWidth > 0 && el.clientHeight > 0) {
        try { inst.resize(); } catch (e) { /* disposed */ }
      }
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return (
    <div ref={wrapRef} style={{ height, width: '100%', ...style }}>
      <ReactECharts ref={innerRef} option={merged} notMerge style={{ height: '100%', width: '100%' }} />
    </div>
  );
});

// ── batch-revision band (整改方案 v1.0 §3.2) ────────────────────────────────
// A custom overlay drawing a faint red vertical band over the [start, end] span
// that was rewritten by an invalidated batch. Registered once at module load.
let _bandRegistered = false;
function registerBatchRevisionBand() {
  if (_bandRegistered) return;
  _bandRegistered = true;
  try {
    registerOverlay({
      name: 'batchRevisionBand',
      totalStep: 3,
      needDefaultPointFigure: false,
      needDefaultXAxisFigure: false,
      needDefaultYAxisFigure: false,
      createPointFigures: ({ coordinates, bounding }) => {
        if (!coordinates || coordinates.length < 2) return [];
        const [a, b] = coordinates;
        return [{
          type: 'rect',
          attrs: {
            x: Math.min(a.x, b.x),
            y: 0,
            width: Math.max(2, Math.abs(b.x - a.x)),
            height: bounding.height,
          },
          styles: { style: 'fill', color: 'rgba(204, 67, 52, 0.10)' },
          ignoreEvent: ['mouseClickEvent'],
        }];
      },
    });
  } catch (e) { /* older klinecharts build without registerOverlay */ }
}

let _compareRegistered = false;
function registerCompareIndicator() {
  if (_compareRegistered) return;
  _compareRegistered = true;
  try {
    registerIndicator({
      name: COMPARE_INDICATOR,
      shortName: t('对比'),
      calcParams: [],
      figures: [
        { key: 's0', title: 'S1: ', type: 'line' },
        { key: 's1', title: 'S2: ', type: 'line' },
        { key: 's2', title: 'S3: ', type: 'line' },
        { key: 's3', title: 'S4: ', type: 'line' },
        { key: 's4', title: 'S5: ', type: 'line' },
      ],
      calc: (dataList, indicator) => {
        const ext = indicator?.extendData || [];
        return dataList.map((_, i) => ({
          s0: ext[0]?.[i] ?? null,
          s1: ext[1]?.[i] ?? null,
          s2: ext[2]?.[i] ?? null,
          s3: ext[3]?.[i] ?? null,
          s4: ext[4]?.[i] ?? null,
        }));
      },
    });
  } catch (e) { /* older build */ }
}

let _mktcapRegistered = false;
function registerMktCapIndicator() {
  if (_mktcapRegistered) return;
  _mktcapRegistered = true;
  try {
    registerIndicator({
      name: MKTCAP_INDICATOR,
      shortName: t('市值(亿)'),
      precision: 0,
      figures: [
        { key: 'circ', title: t('流通: '), type: 'line' },
        { key: 'total', title: t('总: '), type: 'line' },
      ],
      // The candle payload carries total_mv / circ_mv per bar (§6.1), so the
      // indicator reads them straight off the data list (in 亿) -- no extendData
      // plumbing, recalculates automatically whenever applyNewData runs.
      calc: (dataList) => dataList.map((d) => ({
        circ: d && d.circ_mv != null ? d.circ_mv / 1e8 : null,
        total: d && d.total_mv != null ? d.total_mv / 1e8 : null,
      })),
    });
  } catch (e) { /* older klinecharts build */ }
}

function dateToMs(v) {
  if (v == null) return null;
  if (typeof v === 'number') return v;
  const d = new Date(String(v).length <= 10 ? `${v}T00:00:00` : v);
  return Number.isNaN(d.getTime()) ? null : d.getTime();
}

function caColor(actionType) {
  switch (actionType) {
    case 'dividend': return '#3aa776';
    case 'split':
    case 'bonus': return '#3a80c8';
    default: return '#d89900';
  }
}

function caText(a) {
  if (a.action_type === 'dividend' && a.amount != null) return `DIV ¥${Number(a.amount).toFixed(2)}`;
  if ((a.action_type === 'split' || a.action_type === 'bonus') && a.ratio) return `1:${a.ratio}`;
  return (a.action_type || 'CA').toUpperCase();
}

export const Kline = forwardRef(function Kline({
  candles,
  marks,                 // { corporate_actions: [...], batch_revisions: [...] } | null
  onCrosshair,
  onLoadMore,            // ({ earliestTs, callback }) => void
  onMarkClick,           // (mark) => void  — clicking a batch-revision band
  paneHeights,           // reserved
  mainIndicators = ['MA'],
  subIndicators = ['VOL', 'MACD'],
  showMktCap = false,    // 市值副图 (VIZ Layer v2 §6.1) — 流通/总市值时序 sub-pane
}, ref) {
  const elRef = useRef(null);
  const chartRef = useRef(null);
  const onLoadMoreRef = useRef(onLoadMore);
  const onMarkClickRef = useRef(onMarkClick);
  const { theme } = useTheme();

  useEffect(() => { onLoadMoreRef.current = onLoadMore; }, [onLoadMore]);
  useEffect(() => { onMarkClickRef.current = onMarkClick; }, [onMarkClick]);

  // Expose the instance methods the workstation needs.
  useImperativeHandle(ref, () => ({
    createIndicator: (name, isStack, paneOptions) => chartRef.current?.createIndicator(name, isStack, paneOptions),
    removeIndicator: (paneId, name) => chartRef.current?.removeIndicator(paneId, name),
    overrideIndicator: (override, paneId) => chartRef.current?.overrideIndicator(override, paneId),
    listIndicators: () => chartRef.current?.getIndicatorByPaneId?.() || {},
    createOverlay: (value, paneId) => chartRef.current?.createOverlay(value, paneId),
    removeOverlay: (remove) => chartRef.current?.removeOverlay(remove),
    scrollToRealTime: (dur) => chartRef.current?.scrollToRealTime?.(dur),
    scrollToTimestamp: (ts, dur) => chartRef.current?.scrollToTimestamp?.(ts, dur),
    zoomAtTimestamp: (scale, ts) => chartRef.current?.zoomAtTimestamp?.(scale, ts),
    getDataList: () => chartRef.current?.getDataList?.() || [],
    getCanvas: () => {
      const dom = chartRef.current?.getDom?.('candle_pane', 'main');
      return dom?.querySelector('canvas') || null;
    },
    getRoot: () => elRef.current,
    raw: () => chartRef.current,
  }));

  // init / dispose
  useEffect(() => {
    registerBatchRevisionBand();
    registerCompareIndicator();
    registerMktCapIndicator();
    const chart = init(elRef.current);
    chartRef.current = chart;
    try {
      mainIndicators.forEach((n) => chart.createIndicator(n, true, { id: 'candle_pane' }));
      subIndicators.forEach((n) => chart.createIndicator(n));
    } catch (e) { /* indicator name unsupported on this klinecharts build */ }
    if (onCrosshair) {
      try { chart.subscribeAction('onCrosshairChange', (d) => onCrosshair(d?.kLineData || null)); } catch (e) {}
    }
    // loadMore: KLineChart calls back when the user scrolls to the left edge.
    try {
      if (typeof chart.setLoadDataCallback === 'function') {
        chart.setLoadDataCallback(({ type, data, callback }) => {
          if (type !== 'forward' || !data) { callback && callback([], false); return; }
          const cb = onLoadMoreRef.current;
          if (!cb) { callback && callback([], false); return; }
          cb({ earliestTs: data.timestamp, callback });
        });
      } else if (typeof chart.loadMore === 'function') {
        chart.loadMore((ts) => {
          const cb = onLoadMoreRef.current;
          if (cb) cb({ earliestTs: ts, callback: (more) => chart.applyMoreData?.(more) });
        });
      }
    } catch (e) {}
    return () => { dispose(elRef.current); chartRef.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // theme styling (re-applied on theme change)
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const grid = cssVar('--border-faint');
    const text = cssVar('--fg-3');
    const styles = {
      grid: { horizontal: { color: grid }, vertical: { color: grid } },
      candle: {
        bar: { upColor: UP, downColor: DOWN, noChangeColor: text, upBorderColor: UP, downBorderColor: DOWN, upWickColor: UP, downWickColor: DOWN },
        tooltip: { text: { color: text } },
        priceMark: { last: { text: { color: '#fff' } } },
      },
      xAxis: { axisLine: { color: cssVar('--border') }, tickText: { color: text } },
      yAxis: { axisLine: { color: cssVar('--border') }, tickText: { color: text } },
      crosshair: { horizontal: { line: { color: text } }, vertical: { line: { color: text } } },
    };
    try { chart.setStyles ? chart.setStyles(styles) : chart.setStyleOptions(styles); } catch (e) {}
  }, [theme]);

  // data
  useEffect(() => {
    const el = elRef.current;
    const chart = chartRef.current;
    // mark "rendering" while we (re)apply data — E2E waits for this to flip to true
    if (el) el.setAttribute('data-kline-ready', 'false');
    if (chart && candles) {
      try {
        chart.applyNewData(candles);
        if (el) {
          el.setAttribute('data-kline-ready', 'true');
          el.setAttribute('data-kline-candle-count', String(candles.length));
        }
      } catch (e) { /* keep ready=false on failure */ }
    }
  }, [candles]);

  // marks: corporate actions (simpleTag) + batch-revision bands. Re-derived from
  // the `marks` prop on every change; default-on governance transparency (§11.13).
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    try {
      chart.removeOverlay({ groupId: 'marks_ca' });
      chart.removeOverlay({ groupId: 'marks_rev' });
    } catch (e) {}
    if (!marks) return;
    (marks.corporate_actions || []).forEach((a, i) => {
      const ts = dateToMs(a.date);
      if (ts == null) return;
      try {
        chart.createOverlay({
          name: 'simpleTag',
          groupId: 'marks_ca',
          id: `ca_${i}`,
          points: [{ timestamp: ts }],
          extendData: { ...a, text: caText(a) },
          styles: {
            line: { color: caColor(a.action_type) },
            text: { color: '#fff', backgroundColor: caColor(a.action_type), size: 10, paddingLeft: 4, paddingRight: 4 },
          },
        });
      } catch (e) {}
    });
    (marks.batch_revisions || []).forEach((r, i) => {
      const s = dateToMs(r.start);
      const e2 = dateToMs(r.end);
      if (s == null || e2 == null) return;
      try {
        chart.createOverlay({
          name: 'batchRevisionBand',
          groupId: 'marks_rev',
          id: `rev_${i}`,
          points: [{ timestamp: s }, { timestamp: e2 }],
          extendData: r,
          lock: true,
          onClick: () => { onMarkClickRef.current?.(r); return true; },
        });
      } catch (e) {}
    });
  }, [marks]);

  // 市值副图 (VIZ Layer v2 §6.1): create/remove the MKTCAP sub-pane here, inside
  // the component that owns chartRef — the authoritative place (avoids the
  // parent-effect ref/StrictMode race where a create lands on a disposed chart).
  // The indicator reads total_mv / circ_mv straight off the candle data, so it
  // recalculates whenever applyNewData runs. Re-runs on candles so it survives
  // data swaps.
  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return undefined;
    try { chart.removeIndicator('mktcap_pane', MKTCAP_INDICATOR); } catch (e) {}
    const hasMv = (candles || []).some((c) => c && (c.total_mv != null || c.circ_mv != null));
    if (showMktCap && hasMv) {
      try { chart.createIndicator(MKTCAP_INDICATOR, false, { id: 'mktcap_pane', height: 92 }); } catch (e) {}
    }
    return () => { try { chartRef.current?.removeIndicator('mktcap_pane', MKTCAP_INDICATOR); } catch (e) {} };
  }, [showMktCap, candles]);

  return (
    <div
      ref={elRef}
      data-testid="kline-chart"
      data-kline-ready="false"
      style={{ width: '100%', height: '100%' }}
    />
  );
});
