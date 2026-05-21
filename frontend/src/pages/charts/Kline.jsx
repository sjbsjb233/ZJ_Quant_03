// K-line workstation: centerpiece with quote header, control bar, chart, bottom info panel
import React, { useCallback, useEffect, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { TerminalShell, FSelect } from '../../components/shell';
import { Kline, SymbolSearch, Loading, Empty, ErrorState } from '../../components/ui';
import { endpoints } from '../../lib/api';
import { fmtCompact, absTime } from '../../lib/format';

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
  { value: 'pre',  label: '前复权' },
  { value: 'none', label: '不复权' },
  { value: 'post', label: '后复权' },
];

const SOURCE_OPTIONS = [
  { value: 'current_view', label: 'current_view' },
  { value: 'akshare',      label: 'akshare' },
  { value: 'tushare_pro',  label: 'tushare_pro' },
];

const RANGE_OPTIONS = [
  { display: '1M',  months: 1 },
  { display: '3M',  months: 3 },
  { display: '6M',  months: 6 },
  { display: '1Y',  months: 12 },
  { display: '3Y',  months: 36 },
  { display: 'ALL', months: null },
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

export default function KlinePage() {
  const nav = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  const [symbol, setSymbol] = useState(searchParams.get('symbol') || '600519.SH');
  const [freq, setFreq] = useState('1d');
  const [adjust, setAdjust] = useState('pre');
  const [source, setSource] = useState('current_view');
  const [rangeIdx, setRangeIdx] = useState(3); // default 1Y
  const [hover, setHover] = useState(null);

  useEffect(() => {
    setSearchParams({ symbol }, { replace: true });
    pushRecent(`${symbol} · ${freq === '1d' ? '1D' : freq}`);
  }, [symbol, freq]); // eslint-disable-line react-hooks/exhaustive-deps

  const rangeOpt = RANGE_OPTIONS[rangeIdx];
  const start = computeStart(rangeOpt.months);

  const klineQ = useQuery({
    queryKey: ['kline', symbol, freq, adjust, source, rangeIdx],
    queryFn: () => endpoints.kline({
      symbol, frequency: freq, adjust, source,
      ...(start ? { start } : {}),
      count: 250,
    }),
    enabled: !!symbol,
  });

  const marksQ = useQuery({
    queryKey: ['klineMarks', symbol, freq],
    queryFn: () => endpoints.klineMarks({ symbol, frequency: freq, include: 'corporate_actions,batch_revisions' }),
    enabled: !!symbol,
  });

  const candles = klineQ.data?.candles || [];
  const batchRevisions = marksQ.data?.batch_revisions || [];

  const lastCandle = candles.length ? candles[candles.length - 1] : null;
  const prevClose = candles.length > 1 ? candles[candles.length - 2].close : lastCandle?.open;
  const change = lastCandle && prevClose != null ? lastCandle.close - prevClose : null;
  const changePct = change !== null && prevClose ? change / prevClose : null;
  const isUp = change !== null ? change >= 0 : null;
  const priceColor = isUp === null ? 'var(--fg)' : isUp ? 'var(--err)' : 'var(--ok)';

  const infoCandle = hover || lastCandle;

  const handleSymbolChange = useCallback((sym) => setSymbol(sym), []);

  return (
    <TerminalShell
      module="viz"
      current="kline"
      path={['viz/', 'kline/', symbol]}
      fkeys={[
        { k: 'F1', l: '指标' },
        { k: 'F2', l: '标注' },
        { k: 'F3', l: '对比' },
        { k: 'F4', l: '导出' },
        { k: 'F5', l: '刷新', onClick: () => klineQ.refetch() },
      ]}
      titleBar={false}
    >
      {/* Custom top control bar */}
      <div style={{
        padding: '0 14px', height: 40,
        background: 'var(--surface)', borderBottom: '1px solid var(--border)',
        display: 'flex', alignItems: 'center', gap: 0,
        flexShrink: 0, fontFamily: 'var(--font-mono)', fontSize: 11,
      }}>
        <SymbolSearch value={symbol} onChange={handleSymbolChange} width={240} />

        <span style={{ color: 'var(--fg-mute)', margin: '0 10px' }}>│</span>

        {/* Period buttons */}
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

        {/* Adjust buttons */}
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
        <FSelect
          label="src"
          value={source}
          options={SOURCE_OPTIONS}
          onChange={setSource}
          width={130}
        />

        <div style={{ flex: 1 }} />

        {/* Range buttons */}
        <div style={{ display: 'inline-flex', border: '1px solid var(--border-strong)', borderRadius: 3, overflow: 'hidden' }}>
          {RANGE_OPTIONS.map((r, i) => (
            <button
              key={r.display}
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
        ].map(([k, v]) => (
          <div key={k}>
            <div style={{ fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{k}</div>
            <div style={{ fontSize: 13, color: 'var(--fg)', fontVariantNumeric: 'tabular-nums', fontWeight: 500 }}>{v}</div>
          </div>
        ))}
        <div style={{ flex: 1 }} />
        {batchRevisions.length > 0 && (
          <span style={{ fontSize: 10, color: 'var(--warn)' }}>
            ⚠ {batchRevisions[0].start?.slice(0, 10)} → {batchRevisions[0].end?.slice(0, 10)} 区间被批次修订
          </span>
        )}
        {klineQ.isFetching && (
          <span style={{ fontSize: 10, color: 'var(--fg-3)', marginLeft: 8 }}>⟳</span>
        )}
      </div>

      {/* Body: left toolbar + chart area */}
      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        {/* Left tool rail */}
        <div style={{
          width: 40, background: 'var(--surface-2)', borderRight: '1px solid var(--border)',
          display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '8px 0', gap: 4,
          flexShrink: 0,
        }}>
          {[
            { i: '∿', l: '主图指标', active: true },
            { i: '⊟', l: '副图指标' },
            { i: '✎', l: '绘图工具' },
            { i: '⚑', l: '标记控制' },
            { i: '☰', l: '设置' },
            null,
            { i: '⇲', l: '全屏' },
            { i: '↗', l: '导出' },
          ].map((b, i) => b ? (
            <button
              key={i}
              title={b.l}
              className="btn ghost"
              style={{
                width: 28, height: 28, padding: 0,
                background: b.active ? 'var(--surface)' : 'transparent',
                border: b.active ? '1px solid var(--border)' : 'none',
                fontFamily: 'var(--font-mono)', fontSize: 14,
                color: b.active ? 'var(--fg)' : 'var(--fg-2)',
              }}
            >{b.i}</button>
          ) : (
            <div key={i} style={{ width: 24, height: 1, background: 'var(--border)', margin: '4px 0' }} />
          ))}
        </div>

        {/* Chart + bottom info */}
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          {/* Kline chart */}
          <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
            {klineQ.isLoading ? (
              <Loading />
            ) : klineQ.error ? (
              <ErrorState error={klineQ.error} onRetry={klineQ.refetch} />
            ) : candles.length === 0 ? (
              <Empty label={`${symbol} 暂无数据`} />
            ) : (
              <Kline
                candles={candles}
                mainIndicators={['MA']}
                subIndicators={['VOL', 'MACD']}
                onCrosshair={setHover}
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
              {infoCandle?._extras && Object.keys(infoCandle._extras).length > 0 && (
                <div style={{ display: 'flex', gap: 6, marginTop: 6, flexWrap: 'wrap' }}>
                  {Object.entries(infoCandle._extras).map(([k, v]) => (
                    <span key={k} style={{
                      fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--warn)',
                      background: 'var(--warn-bg)', padding: '1px 6px', borderRadius: 2,
                    }}>{`{${k}: ${v}}`}</span>
                  ))}
                </div>
              )}
            </div>

            {/* Metadata */}
            <div style={{ flex: 1, padding: 10, borderRight: '1px solid var(--border)' }}>
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
                <div style={{ color: 'var(--fg-3)', fontSize: 10 }}>
                  {candles.length} candles · {freq} · {adjust}
                </div>
              </div>
            </div>

            {/* Actions */}
            <div style={{ width: 200, padding: 10, display: 'flex', flexDirection: 'column', gap: 4 }}>
              <div style={{
                fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)',
                textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4,
              }}>ACTIONS</div>
              <button
                className="btn ghost sm"
                style={{ fontSize: 10.5, justifyContent: 'flex-start' }}
                disabled={!infoCandle?._batch_id}
                onClick={() => infoCandle?._batch_id && nav(`/data/batches/${infoCandle._batch_id}`)}
              >↗ 跳到批次详情</button>
              <button
                className="btn ghost sm"
                style={{ fontSize: 10.5, justifyContent: 'flex-start' }}
                onClick={() => nav(`/data/tables/raw.bars_daily?symbol=${symbol}`)}
              >↗ 查看行 lineage</button>
              <button
                className="btn ghost sm"
                style={{ fontSize: 10.5, justifyContent: 'flex-start' }}
                onClick={() => nav('/data/tables/raw.bars_daily')}
              >↗ 在表浏览中打开</button>
            </div>
          </div>
        </div>
      </div>
    </TerminalShell>
  );
}
