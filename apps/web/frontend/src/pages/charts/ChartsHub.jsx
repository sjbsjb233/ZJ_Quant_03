// Viz center: 3 workbench cards + recently viewed
import React, { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { TerminalShell } from '../../components/shell';

const RECENT_KEY = 'zjq_recent_charts';

const CARDS = [
  {
    id: 'kline',
    to: '/data/charts/kline',
    l: 'K 线工作台',
    en: 'kline',
    d: '股票分钟/日 K 线 · 技术指标 · 公司行动 · 治理标记 · 多 symbol 对比 · 多源对比',
    cases: ['看某只股票的行情走势', '检查批次修订是否影响图表', '横向对比多只股票'],
    tags: ['KLineChart', 'OHLCV', 'PIT'],
    hot: true,
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4">
        <path d="M4 18V8M4 6V3M10 19V12M10 10V6M16 16V9M16 7V4M22 20V11M22 9V5" />
      </svg>
    ),
  },
  {
    id: 'fundamentals',
    to: '/data/charts/fundamentals',
    l: '财务时间序列',
    en: 'fundamentals',
    d: '净利润 / ROE / 毛利率 · 报告期视角 · 同比环比 · TTM · 强 PIT 约束',
    cases: ['跟踪某只股票财务指标的趋势', '横向对比同行业指标', '研究 as_of 视角下的真实可用数据'],
    tags: ['ECharts', 'PIT', 'as_of'],
    hot: false,
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4">
        <path d="M3 18L8 12L12 14L21 5" />
        <circle cx="8" cy="12" r="1.5" />
        <circle cx="12" cy="14" r="1.5" />
        <circle cx="21" cy="5" r="1.5" />
      </svg>
    ),
  },
  {
    id: 'explorer',
    to: '/data/charts/explorer',
    l: '通用图表浏览器',
    en: 'explorer',
    d: '任意表 · 任意列 · 任意聚合 · 任意图表类型 · 保存为模板',
    cases: ['临时分析一张表', '出一张一次性的图分享', '探索字段之间的关系'],
    tags: ['ECharts', '*'],
    hot: false,
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4">
        <rect x="3" y="3" width="8" height="8" />
        <circle cx="17" cy="7" r="4" />
        <path d="M3 13h8v8H3z" />
        <path d="M13 13l8 8M21 13l-8 8" />
      </svg>
    ),
  },
];

const TYPE_TO_PATH = { 'K线': '/data/charts/kline', '财务': '/data/charts/fundamentals', 'Explorer': '/data/charts/explorer' };

export default function ChartsHub() {
  const nav = useNavigate();

  const recent = useMemo(() => {
    try {
      return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
    } catch {
      return [];
    }
  }, []);

  return (
    <TerminalShell
      module="viz"
      current="center"
      path={['viz/', 'center']}
      fkeys={[
        { k: 'F1', l: 'K线', onClick: () => nav('/data/charts/kline') },
        { k: 'F2', l: '财务', onClick: () => nav('/data/charts/fundamentals') },
        { k: 'F3', l: 'Explorer', onClick: () => nav('/data/charts/explorer') },
      ]}
      title="数据可视化中心"
      subtitle="3 workbenches · backed by KLineChart + ECharts · pick one to start"
    >
      <div style={{ flex: 1, padding: 24, display: 'flex', flexDirection: 'column', gap: 24, minHeight: 0, overflow: 'auto' }}>
        {/* 3 workbench cards */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
          {CARDS.map((c) => (
            <div
              key={c.id}
              style={{
                padding: 18,
                background: 'var(--surface)',
                border: '1px solid var(--border)',
                borderRadius: 4,
                display: 'flex',
                flexDirection: 'column',
                gap: 10,
                minHeight: 340,
                position: 'relative',
              }}
            >
              {c.hot && (
                <span style={{
                  position: 'absolute', top: 10, right: 10,
                  fontFamily: 'var(--font-mono)', fontSize: 9,
                  color: 'var(--info)', letterSpacing: '0.08em',
                  border: '1px solid var(--info)', padding: '0 6px', borderRadius: 2,
                }}>● CORE</span>
              )}

              <div style={{
                width: 40, height: 40, borderRadius: 4,
                background: 'var(--surface-2)', border: '1px solid var(--border)',
                display: 'grid', placeItems: 'center', color: 'var(--fg)',
              }}>
                {c.icon}
              </div>

              <div>
                <div style={{
                  fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)',
                  textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 3,
                }}>{c.en}</div>
                <div style={{ fontSize: 18, fontWeight: 500, color: 'var(--fg)', letterSpacing: '-0.01em' }}>{c.l}</div>
              </div>

              <div style={{ fontSize: 12, color: 'var(--fg-2)', lineHeight: 1.5 }}>{c.d}</div>

              <div style={{
                fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)',
                textTransform: 'uppercase', letterSpacing: '0.08em', marginTop: 6, marginBottom: 2,
              }}>典型场景</div>
              <ul style={{ margin: 0, padding: '0 0 0 16px', fontSize: 11, color: 'var(--fg-2)', lineHeight: 1.6 }}>
                {c.cases.map((s) => <li key={s}>{s}</li>)}
              </ul>

              <div style={{ flex: 1 }} />

              <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                {c.tags.map((t) => (
                  <span key={t} style={{
                    fontFamily: 'var(--font-mono)', fontSize: 9.5,
                    color: 'var(--fg-3)', background: 'var(--surface-2)',
                    border: '1px solid var(--border)', padding: '1px 6px', borderRadius: 2,
                  }}>{t}</span>
                ))}
              </div>

              <button
                className="btn sm"
                onClick={() => nav(c.to)}
                style={{
                  marginTop: 4, background: 'var(--fg)', color: 'var(--bg)', borderColor: 'var(--fg)',
                  justifyContent: 'space-between',
                }}
              >
                <span>进入 {c.l}</span>
                <span>→</span>
              </button>
            </div>
          ))}
        </div>

        {/* Recently viewed */}
        <div>
          <div style={{
            fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)',
            textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 10,
          }}>
            RECENTLY VIEWED · 你最近打开过
          </div>
          {recent.length === 0 ? (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-4)' }}>— 暂无记录 —</div>
          ) : (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {recent.map((r, i) => (
                <span
                  key={i}
                  onClick={() => nav(TYPE_TO_PATH[r.type] || '/data/charts')}
                  style={{
                    fontFamily: 'var(--font-mono)', fontSize: 11,
                    padding: '4px 10px', background: 'var(--surface)',
                    border: '1px solid var(--border)', borderRadius: 3,
                    color: 'var(--fg-2)', cursor: 'pointer',
                    display: 'inline-flex', alignItems: 'center', gap: 8,
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--hover)')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--surface)')}
                >
                  <span style={{ fontSize: 9, color: 'var(--fg-3)', textTransform: 'uppercase' }}>{r.type}</span>
                  <span style={{ color: 'var(--fg)' }}>{r.label}</span>
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </TerminalShell>
  );
}
