// Chart wrappers: ECharts (general) + KLineChart (financial). Both theme-aware.
import React, { useEffect, useMemo, useRef } from 'react';
import ReactECharts from 'echarts-for-react';
import { dispose, init } from 'klinecharts';
import { useTheme } from '../../lib/theme';

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

export function EChart({ option, style, height = '100%' }) {
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
  return <ReactECharts option={merged} notMerge style={{ height, width: '100%', ...style }} />;
}

export function Kline({ candles, mainIndicators = ['MA'], subIndicators = ['VOL', 'MACD'], onCrosshair }) {
  const elRef = useRef(null);
  const chartRef = useRef(null);
  const { theme } = useTheme();

  useEffect(() => {
    const chart = init(elRef.current);
    chartRef.current = chart;
    try {
      mainIndicators.forEach((n) => chart.createIndicator(n, true, { id: 'candle_pane' }));
      subIndicators.forEach((n) => chart.createIndicator(n));
    } catch (e) { /* indicator name unsupported on this klinecharts build */ }
    if (onCrosshair) {
      try { chart.subscribeAction('onCrosshairChange', (d) => onCrosshair(d?.kLineData || null)); } catch (e) {}
    }
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

  useEffect(() => {
    const el = elRef.current;
    const chart = chartRef.current;
    // mark "rendering" while we (re)apply data — E2E waits for this to flip to true
    if (el) el.setAttribute('data-kline-ready', 'false');
    if (chart && candles) {
      try {
        chart.applyNewData(candles);
        // === E2E render-complete signal (charts.jsx Kline, doc §4-bis.4) ===
        if (el) {
          el.setAttribute('data-kline-ready', 'true');
          el.setAttribute('data-kline-candle-count', String(candles.length));
        }
      } catch (e) { /* keep ready=false on failure */ }
    }
  }, [candles]);

  return (
    <div
      ref={elRef}
      data-testid="kline-chart"
      data-kline-ready="false"
      style={{ width: '100%', height: '100%' }}
    />
  );
}
