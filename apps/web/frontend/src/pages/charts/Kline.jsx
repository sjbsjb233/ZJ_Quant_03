// K-line workstation: centerpiece with quote header, control bar, chart, bottom info panel.
// Drives the KLineChart instance through a ref to expose its built-in indicators,
// drawing overlays, navigation and export (整改方案 v1.0 §3).
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { TerminalShell, FSelect } from '../../components/shell';
import { Kline, SymbolSearch, Loading, Empty, ErrorState } from '../../components/ui';
import { COMPARE_INDICATOR } from '../../components/ui/charts';
import { fmtCap } from './vizShared';
import {
  KlineIndicatorPanel, KlineDrawPanel, KlineExportPanel, Popover,
} from '../../components/ui/KlineToolPanels';
import { endpoints } from '../../lib/api';
import { fmtCompact, absTime } from '../../lib/format';
import { t } from '../../lib/i18n';
import { downloadCSV, stitchCanvasesToPng } from '../../lib/download';

const RECENT_KEY = 'zjq_recent_charts';

function pushRecent(label) {
  try {
    const arr = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
    const next = [{ label, type: 'K线' }, ...arr.filter((r) => r.label !== label)].slice(0, 20);
    localStorage.setItem(RECENT_KEY, JSON.stringify(next));
  } catch {}
}

const FREQ_OPTIONS = [
  { display: '1m',  value: '1m' },
  { display: '5m',  value: '5m' },
  { display: '15m', value: '15m' },
  { display: '30m', value: '30m' },
  { display: '1h',  value: '1h' },
  { display: '1D',  value: '1d' },
  { display: '1W',  value: '1w' },
  { display: '1M',  value: '1M' },
];

const ADJUST_OPTIONS = [
  { value: 'pre',  label: t('前复权') },
  { value: 'none', label: t('不复权') },
  { value: 'post', label: t('后复权') },
];

const SOURCE_OPTIONS = [
  { value: 'current_view', label: 'current_view' },
  { value: 'akshare',      label: 'akshare' },
  { value: 'tushare_pro',  label: 'tushare_pro' },
];

const DIFF_SOURCE_OPTIONS = [
  { value: 'akshare',     label: 'akshare' },
  { value: 'tushare_pro', label: 'tushare_pro' },
];

const RANGE_OPTIONS = [
  { display: '1M',  months: 1 },
  { display: '3M',  months: 3 },
  { display: '6M',  months: 6 },
  { display: '1Y',  months: 12 },
  { display: '3Y',  months: 36 },
  { display: '5Y',  months: 60 },
  { display: 'ALL', months: null },
];

const MODE_OPTIONS = [
  { value: 'single', label: t('单 symbol') },
  { value: 'multi',  label: t('多 symbol 对比') },
  { value: 'diff',   label: t('源对比') },
];

function computeStart(months) {
  if (!months) return undefined;
  const d = new Date();
  d.setMonth(d.getMonth() - months);
  return d.toISOString().slice(0, 10);
}

function fmtPrice(v) {
  if (v === null || v === undefined) return '—';
  return Number(v).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtTs(ms) {
  if (!ms) return '—';
  return absTime(new Date(ms)).slice(0, 10);
}

// align symbol-B closes onto symbol-A's timestamps
function alignClose(baseCandles, otherCandles) {
  const m = new Map((otherCandles || []).map((c) => [c.timestamp, c.close]));
  return (baseCandles || []).map((c) => (m.has(c.timestamp) ? m.get(c.timestamp) : null));
}

export default function KlinePage() {
  const nav = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const [symbols, setSymbols] = useState([searchParams.get('symbol') || '600519.SH']);
  const [compareMode, setCompareMode] = useState('grid'); // 'grid' | 'overlay'
  const [showCompare, setShowCompare] = useState(false);
  const [freq, setFreq] = useState('1d');
  const [adjust, setAdjust] = useState('pre');
  const [source, setSource] = useState('current_view');
  const [viewMode, setViewMode] = useState('current'); // 'current' | 'all'
  const [rangeIdx, setRangeIdx] = useState(3); // default 1Y
  const [hover, setHover] = useState(null);

  // source-diff
  const [diffMode, setDiffMode] = useState(false);
  const [sourceA, setSourceA] = useState('akshare');
  const [sourceB, setSourceB] = useState('tushare_pro');

  // indicators (controlled so toggles + remounts stay in sync)
  const [mainIndicators, setMainIndicators] = useState(['MA']);
  const [subIndicators, setSubIndicators] = useState(['VOL', 'MACD']);

  // marks (governance transparency — default ON, doc §11.13)
  const [marksEnabled, setMarksEnabled] = useState(true);

  // 市值副图 (VIZ Layer v2 §6.1) — 流通/总市值时序副图, 默认开
  const [showMktCap, setShowMktCap] = useState(true);

  // tool-rail popovers
  const [panel, setPanel] = useState(null); // 'main' | 'sub' | 'draw' | 'export' | 'settings' | null

  const klineRef = useRef(null);
  const bodyRef = useRef(null);

  const symbol = symbols[0];
  const isMulti = symbols.length > 1;
  const isGrid = isMulti && compareMode === 'grid';
  const isOverlay = isMulti && compareMode === 'overlay';
  const diffActive = diffMode && !isMulti;

  // top-bar mode selector (design: 单 symbol / 多 symbol 对比 / 源对比) — a thin
  // driver over the existing showCompare + diffMode state.
  const mode = diffMode ? 'diff' : (showCompare || isMulti) ? 'multi' : 'single';
  const setMode = useCallback((m) => {
    if (m === 'single') { setShowCompare(false); setDiffMode(false); setSymbols((p) => [p[0]]); }
    else if (m === 'multi') { setShowCompare(true); setDiffMode(false); }
    else if (m === 'diff') { setShowCompare(true); setDiffMode(true); }
  }, []);

  useEffect(() => {
    setSearchParams({ symbol }, { replace: true });
    pushRecent(`${symbol} · ${freq === '1d' ? '1D' : freq}`);
  }, [symbol, freq]); // eslint-disable-line react-hooks/exhaustive-deps

  const rangeOpt = RANGE_OPTIONS[rangeIdx];
  const start = computeStart(rangeOpt.months);

  // single-symbol candles
  const klineQ = useQuery({
    queryKey: ['kline', symbol, freq, adjust, source, rangeIdx, viewMode],
    queryFn: () => endpoints.kline({
      symbol, frequency: freq, adjust, source,
      ...(start ? { start } : {}),
      count: 250, view: viewMode,
    }),
    enabled: !!symbol && !isMulti && !diffActive,
  });

  // multi-symbol compare (always grid payload; overlay derived client-side)
  const multiQ = useQuery({
    queryKey: ['klineMulti', symbols, freq, adjust, rangeIdx],
    queryFn: () => endpoints.klineMulti({
      symbols, frequency: freq, adjust, mode: 'grid',
      ...(start ? { start } : {}),
    }),
    enabled: isMulti,
  });

  // multi-source diff
  const diffQ = useQuery({
    queryKey: ['klineDiff', symbol, freq, sourceA, sourceB, rangeIdx],
    queryFn: () => endpoints.klineDiffSources({
      symbol, frequency: freq, source_a: sourceA, source_b: sourceB,
      ...(start ? { start } : {}),
    }),
    enabled: diffActive,
  });

  const marksQ = useQuery({
    queryKey: ['klineMarks', symbol, freq],
    queryFn: () => endpoints.klineMarks({ symbol, frequency: freq, include: 'corporate_actions,batch_revisions' }),
    enabled: !!symbol,
  });

  // active query for status/error/loading on the single-instance views
  const activeQ = diffActive ? diffQ : isMulti ? multiQ : klineQ;

  // candles for the main instance
  const mainCandles = useMemo(() => {
    if (diffActive) return diffQ.data?.candles_a || [];
    if (isMulti) return multiQ.data?.series?.[symbol] || [];
    return klineQ.data?.candles || [];
  }, [diffActive, isMulti, symbol, diffQ.data, multiQ.data, klineQ.data]);

  const batchRevisions = marksQ.data?.batch_revisions || [];

  const lastCandle = mainCandles.length ? mainCandles[mainCandles.length - 1] : null;
  const prevClose = mainCandles.length > 1 ? mainCandles[mainCandles.length - 2].close : lastCandle?.open;
  const change = lastCandle && prevClose != null ? lastCandle.close - prevClose : null;
  const changePct = change !== null && prevClose ? change / prevClose : null;
  const isUp = change !== null ? change >= 0 : null;
  const priceColor = isUp === null ? 'var(--fg)' : isUp ? 'var(--err)' : 'var(--ok)';

  const infoCandle = hover || lastCandle;

  const handleSymbolChange = useCallback((sym) => setSymbols([sym]), []);

  const addCompareSymbol = useCallback((s) => {
    setSymbols((prev) => (s && !prev.includes(s) && prev.length < 5 ? [...prev, s] : prev));
  }, []);
  const removeCompareSymbol = useCallback((s) => {
    setSymbols((prev) => (prev.length > 1 ? prev.filter((x) => x !== s) : prev));
  }, []);

  // loadMore (single normal view only) — KLineChart fires when scrolled to left edge
  const loadMore = useCallback(({ earliestTs, callback }) => {
    if (isMulti || diffActive || !earliestTs) { callback?.([], false); return; }
    // intraday frequencies need a precise timestamp cursor so we don't skip
    // bars within the cutoff day; daily uses a date cursor.
    const intraday = ['1m', '5m', '15m', '30m', '1h'].includes(freq);
    const before = intraday
      ? new Date(earliestTs).toISOString()
      : new Date(earliestTs).toISOString().slice(0, 10);
    endpoints.kline({
      symbol, frequency: freq, adjust, source, before, count: 200, view: viewMode,
    })
      .then((r) => callback?.(r.candles || [], !!r.has_more_before))
      .catch(() => callback?.([], false));
  }, [isMulti, diffActive, symbol, freq, adjust, source, viewMode]);

  // overlay-compare lines (own sub-pane, rebased to 100)
  useEffect(() => {
    const k = klineRef.current;
    if (!isOverlay || !k) return;
    const series = multiQ.data?.series || {};
    const base = series[symbol] || [];
    const ext = symbols.map((sym) => {
      const m = new Map((series[sym] || []).map((c) => [c.timestamp, c.close]));
      let b0 = null;
      return base.map((c) => {
        const v = m.get(c.timestamp);
        if (v == null) return null;
        if (b0 == null) b0 = v;
        return b0 ? (v / b0) * 100 : null;
      });
    });
    try {
      k.removeIndicator('compare_pane', COMPARE_INDICATOR);
      k.createIndicator({ name: COMPARE_INDICATOR, extendData: ext }, false, { id: 'compare_pane' });
    } catch (e) {}
    return () => { try { klineRef.current?.removeIndicator('compare_pane', COMPARE_INDICATOR); } catch (e) {} };
  }, [isOverlay, multiQ.data, symbols, symbol]);

  // source-diff: overlay source-B close on the candle pane + highlight large gaps
  useEffect(() => {
    const k = klineRef.current;
    if (!diffActive || !diffQ.data || !k) return;
    const candlesA = diffQ.data.candles_a || [];
    const candlesB = diffQ.data.candles_b || [];
    const bAligned = alignClose(candlesA, candlesB);
    const mb = new Map(candlesB.map((c) => [c.timestamp, c.close]));
    try {
      k.removeIndicator('candle_pane', COMPARE_INDICATOR);
      k.createIndicator({ name: COMPARE_INDICATOR, extendData: [bAligned] }, true, { id: 'candle_pane' });
      k.removeOverlay({ groupId: 'diff_alert' });
      candlesA.forEach((a, i) => {
        const b = mb.get(a.timestamp);
        if (b != null && a.close && Math.abs((a.close - b) / a.close) > 0.005) {
          k.createOverlay({
            name: 'simpleTag',
            groupId: 'diff_alert',
            id: `diff_${i}`,
            points: [{ timestamp: a.timestamp, value: a.close }],
            extendData: { text: `Δ ${(((a.close - b) / a.close) * 100).toFixed(2)}%` },
            styles: { text: { color: '#fff', backgroundColor: 'rgba(204,67,52,0.85)', size: 9 } },
          });
        }
      });
    } catch (e) {}
    return () => {
      const k2 = klineRef.current;
      try { k2?.removeIndicator('candle_pane', COMPARE_INDICATOR); k2?.removeOverlay({ groupId: 'diff_alert' }); } catch (e) {}
    };
  }, [diffActive, diffQ.data]);

  // ── exports ────────────────────────────────────────────────────────────────
  const exportPng = useCallback(() => {
    const root = klineRef.current?.getRoot();
    stitchCanvasesToPng(root, `${symbol}_${freq}_${new Date().toISOString().slice(0, 10)}.png`);
  }, [symbol, freq]);

  const exportCsv = useCallback(() => {
    const data = klineRef.current?.getDataList() || mainCandles;
    const rows = [['timestamp', 'open', 'high', 'low', 'close', 'volume']];
    data.forEach((d) => rows.push([
      new Date(d.timestamp).toISOString(), d.open, d.high, d.low, d.close, d.volume,
    ]));
    downloadCSV(rows, `${symbol}_${freq}.csv`);
  }, [symbol, freq, mainCandles]);

  // ── fullscreen ───────────────────────────────────────────────────────────────
  const toggleFullscreen = useCallback(() => {
    const el = bodyRef.current;
    if (!document.fullscreenElement) el?.requestFullscreen?.();
    else document.exitFullscreen?.();
  }, []);

  const togglePanel = (id) => setPanel((p) => (p === id ? null : id));

  const railButtons = [
    { id: 'main',     i: '∿', l: t('主图指标'), onClick: () => togglePanel('main') },
    { id: 'sub',      i: '⊟', l: t('副图指标'), onClick: () => togglePanel('sub') },
    { id: 'draw',     i: '✎', l: t('绘图工具'), onClick: () => togglePanel('draw') },
    { id: 'marks',    i: '⚑', l: t('标记控制'), onClick: () => setMarksEnabled((v) => !v), active: marksEnabled },
    { id: 'mktcap',   i: '¥', l: t('市值副图'), onClick: () => setShowMktCap((v) => !v), active: showMktCap },
    { id: 'settings', i: '☰', l: t('设置'),     onClick: () => togglePanel('settings') },
    null,
    { id: 'fs',       i: '⇲', l: t('全屏'),     onClick: toggleFullscreen },
    { id: 'export',   i: '↗', l: t('导出'),     onClick: () => togglePanel('export') },
  ];

  // tool rail / popovers only make sense on the single-instance views
  const toolsEnabled = !isGrid;

  return (
    <TerminalShell
      module="viz"
      current="kline"
      path={['viz/', 'kline/', symbol]}
      fkeys={[
        { k: 'F1', l: t('指标'), onClick: () => togglePanel('main'), active: panel === 'main' },
        { k: 'F2', l: t('标注'), onClick: () => togglePanel('draw'), active: panel === 'draw' },
        { k: 'F3', l: t('对比'), onClick: () => setShowCompare((v) => !v), active: showCompare },
        { k: 'F4', l: t('导出'), onClick: () => togglePanel('export'), active: panel === 'export' },
        { k: 'F5', l: t('刷新'), onClick: () => activeQ.refetch() },
      ]}
      titleBar={false}
    >
      {/* Top control bar */}
      <div style={{
        padding: '0 14px', minHeight: 40,
        background: 'var(--surface)', borderBottom: '1px solid var(--border)',
        display: 'flex', alignItems: 'center', gap: 0, flexWrap: 'wrap', rowGap: 6,
        flexShrink: 0, fontFamily: 'var(--font-mono)', fontSize: 11,
      }}>
        <SymbolSearch value={symbol} onChange={handleSymbolChange} width={240} />

        <span style={{ color: 'var(--fg-mute)', margin: '0 10px' }}>│</span>

        {/* Period */}
        <div style={{ display: 'inline-flex', border: '1px solid var(--border-strong)', borderRadius: 3, overflow: 'hidden' }}>
          {FREQ_OPTIONS.map((f, i) => (
            <button
              key={f.value}
              onClick={() => setFreq(f.value)}
              style={{
                height: 26, padding: '0 9px',
                background: freq === f.value ? 'var(--fg)' : 'transparent',
                color: freq === f.value ? 'var(--bg)' : 'var(--fg-2)',
                border: 'none',
                borderRight: i < FREQ_OPTIONS.length - 1 ? '1px solid var(--border-strong)' : 'none',
                fontFamily: 'var(--font-mono)', fontSize: 10.5, cursor: 'pointer',
                fontWeight: freq === f.value ? 600 : 400,
              }}
            >{f.display}</button>
          ))}
        </div>

        <span style={{ color: 'var(--fg-mute)', margin: '0 10px' }}>│</span>

        {/* Adjust */}
        <div style={{ display: 'inline-flex', border: '1px solid var(--border-strong)', borderRadius: 3, overflow: 'hidden' }}>
          {ADJUST_OPTIONS.map((a, i) => (
            <button
              key={a.value}
              onClick={() => setAdjust(a.value)}
              style={{
                height: 26, padding: '0 9px',
                background: adjust === a.value ? 'var(--fg)' : 'transparent',
                color: adjust === a.value ? 'var(--bg)' : 'var(--fg-2)',
                border: 'none',
                borderRight: i < ADJUST_OPTIONS.length - 1 ? '1px solid var(--border-strong)' : 'none',
                fontFamily: 'var(--font-mono)', fontSize: 10.5, cursor: 'pointer',
                fontWeight: adjust === a.value ? 600 : 400,
              }}
            >{a.label}</button>
          ))}
        </div>

        <span style={{ marginRight: 8 }} />
        {!diffActive && (
          <FSelect label="src" value={source} options={SOURCE_OPTIONS} onChange={setSource} width={130} />
        )}
        <span style={{ marginRight: 8 }} />
        <FSelect label="mode" value={mode} options={MODE_OPTIONS}
          onChange={(m) => { setMode(m); if (m !== 'single') setShowCompare(true); }} width={120} testId="kline-mode-select" />

        <span style={{ color: 'var(--fg-mute)', margin: '0 10px' }}>│</span>

        {/* View: current / all */}
        <div style={{ display: 'inline-flex', border: '1px solid var(--border-strong)', borderRadius: 3, overflow: 'hidden' }}>
          {[{ value: 'current', label: t('当前') }, { value: 'all', label: t('含作废') }].map((v, i) => (
            <button
              key={v.value}
              data-testid={`kline-view-${v.value}`}
              onClick={() => setViewMode(v.value)}
              style={{
                height: 26, padding: '0 9px',
                background: viewMode === v.value ? 'var(--fg)' : 'transparent',
                color: viewMode === v.value ? 'var(--bg)' : 'var(--fg-2)',
                border: 'none',
                borderRight: i === 0 ? '1px solid var(--border-strong)' : 'none',
                fontFamily: 'var(--font-mono)', fontSize: 10.5, cursor: 'pointer',
                fontWeight: viewMode === v.value ? 600 : 400,
              }}
            >{v.label}</button>
          ))}
        </div>

        <div style={{ flex: 1 }} />

        {/* Range */}
        <div style={{ display: 'inline-flex', border: '1px solid var(--border-strong)', borderRadius: 3, overflow: 'hidden' }}>
          {RANGE_OPTIONS.map((r, i) => (
            <button
              key={r.display}
              data-testid={`kline-range-${r.display}`}
              onClick={() => setRangeIdx(i)}
              style={{
                height: 26, padding: '0 9px',
                background: rangeIdx === i ? 'var(--fg)' : 'transparent',
                color: rangeIdx === i ? 'var(--bg)' : 'var(--fg-2)',
                border: 'none',
                borderRight: i < RANGE_OPTIONS.length - 1 ? '1px solid var(--border-strong)' : 'none',
                fontFamily: 'var(--font-mono)', fontSize: 10.5, cursor: 'pointer',
                fontWeight: rangeIdx === i ? 600 : 400,
              }}
            >{r.display}</button>
          ))}
        </div>
      </div>

      {/* Compare control row (toggled by F3 / 对比) */}
      {showCompare && (
        <div data-testid="kline-compare-bar" style={{
          padding: '6px 14px', minHeight: 34,
          background: 'var(--surface-2)', borderBottom: '1px solid var(--border)',
          display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', rowGap: 6,
          fontFamily: 'var(--font-mono)', fontSize: 11, flexShrink: 0,
        }}>
          <span style={{ color: 'var(--fg-3)', fontSize: 10 }}>{t('对比 symbol:')}</span>
          <span style={{ color: 'var(--fg)' }}>{symbol}</span>
          {symbols.slice(1).map((s) => (
            <span key={s} style={{
              padding: '1px 7px', background: 'var(--surface)', border: '1px solid var(--border)',
              borderRadius: 3, display: 'inline-flex', alignItems: 'center', gap: 6,
            }}>
              {s}<span style={{ cursor: 'pointer', color: 'var(--fg-3)' }} onClick={() => removeCompareSymbol(s)}>✕</span>
            </span>
          ))}
          {symbols.length < 5 && !diffMode && (
            <SymbolSearch value="" onChange={addCompareSymbol} width={180} />
          )}

          {isMulti && (
            <>
              <span style={{ color: 'var(--fg-mute)' }}>│</span>
              <div style={{ display: 'inline-flex', border: '1px solid var(--border-strong)', borderRadius: 3, overflow: 'hidden' }}>
                {[{ value: 'grid', label: t('网格') }, { value: 'overlay', label: t('叠加') }].map((m, i) => (
                  <button
                    key={m.value}
                    data-testid={`kline-compare-mode-${m.value}`}
                    onClick={() => setCompareMode(m.value)}
                    style={{
                      height: 22, padding: '0 8px',
                      background: compareMode === m.value ? 'var(--fg)' : 'transparent',
                      color: compareMode === m.value ? 'var(--bg)' : 'var(--fg-2)',
                      border: 'none', borderRight: i === 0 ? '1px solid var(--border-strong)' : 'none',
                      fontFamily: 'var(--font-mono)', fontSize: 10, cursor: 'pointer',
                    }}
                  >{m.label}</button>
                ))}
              </div>
            </>
          )}

          {!isMulti && (
            <>
              <span style={{ color: 'var(--fg-mute)' }}>│</span>
              <label style={{ display: 'inline-flex', alignItems: 'center', gap: 5, cursor: 'pointer', color: 'var(--fg-2)' }}>
                <input type="checkbox" data-testid="kline-diff-toggle" checked={diffMode} onChange={(e) => setDiffMode(e.target.checked)} />
                {t('源对比 (A vs B)')}
              </label>
              {diffMode && (
                <>
                  <FSelect label="A" value={sourceA} options={DIFF_SOURCE_OPTIONS} onChange={setSourceA} width={110} />
                  <span style={{ color: 'var(--fg-3)' }}>vs</span>
                  <FSelect label="B" value={sourceB} options={DIFF_SOURCE_OPTIONS} onChange={setSourceB} width={110} />
                </>
              )}
            </>
          )}
        </div>
      )}

      {/* Quote header */}
      <div style={{
        padding: '8px 14px', height: 56,
        background: 'var(--surface)', borderBottom: '1px solid var(--border-strong)',
        display: 'flex', alignItems: 'center', gap: 28,
        fontFamily: 'var(--font-mono)', flexShrink: 0,
      }}>
        <div>
          <div style={{ fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            LAST · {lastCandle ? fmtTs(lastCandle.timestamp) : '—'}
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
            <span style={{ fontSize: 22, fontWeight: 600, color: priceColor, fontVariantNumeric: 'tabular-nums' }}>
              {lastCandle ? fmtPrice(lastCandle.close) : '—'}
            </span>
            {change !== null && (
              <>
                <span style={{ fontSize: 13, color: priceColor }}>
                  {change >= 0 ? '+' : ''}{fmtPrice(change)}
                </span>
                <span style={{ fontSize: 13, color: priceColor }}>
                  {changePct !== null ? (changePct >= 0 ? '+' : '') + (changePct * 100).toFixed(2) + '%' : ''}
                </span>
              </>
            )}
          </div>
        </div>
        {[
          ['OPEN', fmtPrice(lastCandle?.open)],
          ['HIGH', fmtPrice(lastCandle?.high)],
          ['LOW',  fmtPrice(lastCandle?.low)],
          ['PREV', fmtPrice(prevClose)],
          ['VOL',  lastCandle?.volume != null ? fmtCompact(lastCandle.volume) : '—'],
          ['AMT',  lastCandle?.amount != null ? fmtCompact(lastCandle.amount) : '—'],
          ['TURN', lastCandle?.turnover != null ? `${Number(lastCandle.turnover).toFixed(2)}%` : '—'],
          ['MKT.CAP', lastCandle?.total_mv != null ? fmtCap(lastCandle.total_mv) : '—'],
        ].map(([k, v]) => (
          <div key={k}>
            <div style={{ fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{k}</div>
            <div style={{ fontSize: 13, color: 'var(--fg)', fontVariantNumeric: 'tabular-nums', fontWeight: 500 }}>{v}</div>
          </div>
        ))}
        <div style={{ flex: 1 }} />
        {diffActive && (
          <span style={{ fontSize: 10, color: 'var(--info)' }}>
            {t('源对比')} · {sourceA} ▬ vs {sourceB} ▬ · {t('Δ>0.5% 高亮')}
          </span>
        )}
        {marksEnabled && batchRevisions.length > 0 && (
          <span data-testid="kline-revision-warning" style={{ fontSize: 10, color: 'var(--warn)' }}>
            ⚠ {batchRevisions[0].start?.slice(0, 10)} → {batchRevisions[0].end?.slice(0, 10)} {t('区间被批次修订')}
          </span>
        )}
        {activeQ.isFetching && (
          <span style={{ fontSize: 10, color: 'var(--fg-3)', marginLeft: 8 }}>⟳</span>
        )}
      </div>

      {/* Body: left toolbar + chart area */}
      <div ref={bodyRef} style={{ flex: 1, display: 'flex', minHeight: 0, background: 'var(--bg)' }}>
        {/* Left tool rail */}
        <div style={{
          width: 40, background: 'var(--surface-2)', borderRight: '1px solid var(--border)',
          display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '8px 0', gap: 4,
          flexShrink: 0, position: 'relative',
        }}>
          {railButtons.map((b, i) => b ? (
            <button
              key={b.id}
              title={b.l}
              data-testid={`kline-tool-${b.id}`}
              className="btn ghost"
              disabled={!toolsEnabled && b.id !== 'fs'}
              onClick={b.onClick}
              style={{
                width: 28, height: 28, padding: 0,
                background: (b.active || panel === b.id) ? 'var(--surface)' : 'transparent',
                border: (b.active || panel === b.id) ? '1px solid var(--border)' : 'none',
                fontFamily: 'var(--font-mono)', fontSize: 14,
                color: (b.active || panel === b.id) ? 'var(--fg)' : 'var(--fg-2)',
                opacity: (!toolsEnabled && b.id !== 'fs') ? 0.4 : 1,
                cursor: (!toolsEnabled && b.id !== 'fs') ? 'not-allowed' : 'pointer',
              }}
            >{b.i}</button>
          ) : (
            <div key={`sep-${i}`} style={{ width: 24, height: 1, background: 'var(--border)', margin: '4px 0' }} />
          ))}

          {/* Popovers anchored to the rail */}
          {toolsEnabled && panel === 'main' && (
            <KlineIndicatorPanel pane="main" klineRef={klineRef} active={mainIndicators}
              onChange={setMainIndicators} onClose={() => setPanel(null)} top={8} />
          )}
          {toolsEnabled && panel === 'sub' && (
            <KlineIndicatorPanel pane="sub" klineRef={klineRef} active={subIndicators}
              onChange={setSubIndicators} onClose={() => setPanel(null)} top={36} />
          )}
          {toolsEnabled && panel === 'draw' && (
            <KlineDrawPanel klineRef={klineRef} onClose={() => setPanel(null)} top={64} />
          )}
          {toolsEnabled && panel === 'export' && (
            <KlineExportPanel onPng={exportPng} onCsv={exportCsv} onClose={() => setPanel(null)} top={220} />
          )}
          {toolsEnabled && panel === 'settings' && (
            <Popover onClose={() => setPanel(null)} top={120} width={180} title={t('设置')} testId="kline-settings-panel">
              <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                <button className="btn ghost sm" style={{ fontSize: 10.5, justifyContent: 'flex-start' }}
                  onClick={() => { klineRef.current?.scrollToRealTime(200); setPanel(null); }}>{t('⇥ 回到最新')}</button>
                <button className="btn ghost sm" style={{ fontSize: 10.5, justifyContent: 'flex-start' }}
                  onClick={() => { klineRef.current?.removeOverlay({}); setPanel(null); }}>{t('✕ 清空所有画线')}</button>
                <label style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 4, color: 'var(--fg-2)' }}>
                  <input type="checkbox" checked={marksEnabled} onChange={(e) => setMarksEnabled(e.target.checked)} />
                  {t('显示治理标记')}
                </label>
              </div>
            </Popover>
          )}
        </div>

        {/* Chart + bottom info */}
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          {/* Chart */}
          <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
            {activeQ.isLoading ? (
              <Loading />
            ) : activeQ.error ? (
              <ErrorState error={activeQ.error} onRetry={activeQ.refetch} />
            ) : isGrid ? (
              <div style={{
                position: 'absolute', inset: 0, display: 'grid', gap: 1,
                gridTemplateColumns: 'repeat(auto-fit, minmax(360px, 1fr))',
                background: 'var(--border)', overflow: 'auto',
              }}>
                {symbols.map((s) => {
                  const cs = multiQ.data?.series?.[s] || [];
                  return (
                    <div key={s} style={{ background: 'var(--bg)', display: 'flex', flexDirection: 'column', minHeight: 280 }}>
                      <div style={{ padding: '4px 10px', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 6 }}>
                        {s}
                        <span style={{ color: 'var(--fg-3)', fontSize: 10 }}>{cs.length} bars</span>
                        <span style={{ marginLeft: 'auto', cursor: 'pointer', color: 'var(--fg-3)' }} onClick={() => removeCompareSymbol(s)}>✕</span>
                      </div>
                      <div style={{ flex: 1, minHeight: 0 }}>
                        {cs.length ? (
                          <Kline candles={cs} mainIndicators={mainIndicators} subIndicators={['VOL']} />
                        ) : <Empty label={t('{s} 暂无数据', { s })} />}
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : mainCandles.length === 0 ? (
              <Empty label={t('{s} 暂无数据', { s: symbol })} />
            ) : (
              <Kline
                ref={klineRef}
                candles={mainCandles}
                marks={marksEnabled && !isMulti && !diffActive ? marksQ.data : null}
                mainIndicators={mainIndicators}
                subIndicators={subIndicators}
                showMktCap={showMktCap && !isMulti && !diffActive}
                onCrosshair={setHover}
                onLoadMore={loadMore}
                onMarkClick={(r) => r?.batch_id && nav(`/data/batches/${r.batch_id}`)}
              />
            )}
          </div>

          {/* Bottom info panel */}
          <div style={{
            height: 120, flexShrink: 0,
            borderTop: '1px solid var(--border-strong)',
            background: 'var(--surface-2)',
            display: 'flex',
          }}>
            {/* OHLCV */}
            <div style={{ flex: 1, padding: 10, borderRight: '1px solid var(--border)' }}>
              <div style={{
                fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)',
                textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4,
              }}>
                {hover ? 'HOVER' : 'LATEST'} · {infoCandle ? fmtTs(infoCandle.timestamp) : '—'}
              </div>
              {infoCandle ? (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 6, fontFamily: 'var(--font-mono)', fontSize: 11 }}>
                  {[
                    ['open',   fmtPrice(infoCandle.open)],
                    ['high',   fmtPrice(infoCandle.high)],
                    ['low',    fmtPrice(infoCandle.low)],
                    ['close',  fmtPrice(infoCandle.close)],
                    ['volume', fmtCompact(infoCandle.volume)],
                    ['amount', infoCandle.amount != null ? fmtCompact(infoCandle.amount) : '—'],
                    ['turn',   infoCandle.turnover != null ? `${Number(infoCandle.turnover).toFixed(2)}%` : '—'],
                    ['date',   fmtTs(infoCandle.timestamp)],
                  ].map(([k, v]) => (
                    <div key={k}>
                      <span style={{ color: 'var(--fg-3)' }}>{k} </span>
                      <span style={{ color: 'var(--fg)', fontVariantNumeric: 'tabular-nums' }}>{v}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-3)' }}>—</div>
              )}
            </div>

            {/* Metadata */}
            <div data-testid="kline-hover-meta" style={{ flex: 1, padding: 10, borderRight: '1px solid var(--border)' }}>
              <div style={{
                fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)',
                textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4,
              }}>METADATA</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-2)', lineHeight: 1.9 }}>
                <div>
                  _batch_id{' '}
                  {infoCandle?._batch_id ? (
                    <span
                      style={{ color: 'var(--info)', cursor: 'pointer' }}
                      onClick={() => nav(`/data/batches/${infoCandle._batch_id}`)}
                    >{infoCandle._batch_id}</span>
                  ) : <span style={{ color: 'var(--fg-4)' }}>—</span>}
                </div>
                <div>
                  _source{' '}
                  {infoCandle?._source
                    ? <span style={{ color: 'var(--fg)' }}>{infoCandle._source}</span>
                    : <span style={{ color: 'var(--fg-4)' }}>—</span>}
                </div>
                <div>
                  _ingested_at{' '}
                  {infoCandle?._ingested_at
                    ? <span style={{ color: 'var(--fg-2)' }}>{String(infoCandle._ingested_at).replace('T', ' ').slice(0, 19)}</span>
                    : <span style={{ color: 'var(--fg-4)' }}>—</span>}
                </div>
                <div style={{ color: 'var(--fg-3)', fontSize: 10 }}>
                  {mainCandles.length} candles · {freq} · {adjust} · {viewMode}
                </div>
              </div>
            </div>

            {/* Actions */}
            <div data-testid="kline-hover-actions" style={{ width: 200, padding: 10, display: 'flex', flexDirection: 'column', gap: 4 }}>
              <div style={{
                fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)',
                textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4,
              }}>ACTIONS</div>
              <button
                className="btn ghost sm"
                data-testid="kline-jump-batch"
                style={{ fontSize: 10.5, justifyContent: 'flex-start' }}
                disabled={!infoCandle?._batch_id}
                onClick={() => infoCandle?._batch_id && nav(`/data/batches/${infoCandle._batch_id}`)}
              >{t('↗ 跳到批次详情')}</button>
              <button
                className="btn ghost sm"
                style={{ fontSize: 10.5, justifyContent: 'flex-start' }}
                onClick={() => nav(`/data/tables/raw.bars_daily?symbol=${symbol}`)}
              >{t('↗ 查看行 lineage')}</button>
              <button
                className="btn ghost sm"
                data-testid="kline-jump-tablebrowser"
                style={{ fontSize: 10.5, justifyContent: 'flex-start' }}
                onClick={() => nav('/data/tables/raw.bars_daily')}
              >{t('↗ 在表浏览中打开')}</button>
              <button
                className="btn ghost sm"
                style={{ fontSize: 10.5, justifyContent: 'flex-start' }}
                disabled={!infoCandle}
                onClick={() => infoCandle && navigator.clipboard?.writeText(JSON.stringify(infoCandle, null, 2))}
              >{t('⎘ 复制行数据')}</button>
            </div>
          </div>
        </div>
      </div>
    </TerminalShell>
  );
}
