// Viz center: 5 workbench cards (+ 市场全景 / 行业视图 NEW) + recently viewed.
import React, { useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { TerminalShell } from '../../components/shell';

const RECENT_KEY = 'zjq_recent_charts';

const ICONS = {
  market: <g><path d="M3 21h18" /><rect x="4" y="12" width="3.4" height="9" /><rect x="10.3" y="7" width="3.4" height="14" /><rect x="16.6" y="15" width="3.4" height="6" /></g>,
  industry: <g><circle cx="12" cy="4" r="2" /><path d="M12 6v3M12 9L6 13M12 9l6 4" /><rect x="3" y="13" width="6" height="6" /><rect x="15" y="13" width="6" height="6" /></g>,
  kline: <g><path d="M4 18V8M4 6V3M10 19V12M10 10V6M16 16V9M16 7V4M22 20V11M22 9V5" /></g>,
  fundamentals: <g><path d="M3 18L8 12L12 14L21 5" /><circle cx="8" cy="12" r="1.5" /><circle cx="12" cy="14" r="1.5" /><circle cx="21" cy="5" r="1.5" /></g>,
  explorer: <g><rect x="3" y="3" width="8" height="8" /><circle cx="17" cy="7" r="4" /><path d="M3 13h8v8H3z" /><path d="M13 13l8 8M21 13l-8 8" /></g>,
};

const CARDS = [
  {
    id: 'market', to: '/data/charts/market', l: '市场全景', en: 'market',
    d: '全市场截面与广度 · 看某天整个市场长什么样',
    cases: ['看今天市场情绪强弱', '看市值分布与市场结构', '跟踪指数成分调样'],
    tags: ['breadth', 'snapshot'], isNew: true,
  },
  {
    id: 'industry', to: '/data/charts/industry', l: '行业视图', en: 'industry',
    d: '行业分类树 · 成分 · 横向对比 · 个股反查',
    cases: ['看行业怎么分层、谁最大', '行业之间横向对比指标', '反查某股属于哪个行业'],
    tags: ['申万/中信', 'drill'], isNew: true,
  },
  {
    id: 'kline', to: '/data/charts/kline', l: 'K 线工作台', en: 'kline',
    d: '分钟/日 K 线 · 技术指标 · 公司行动 · 市值副图 · 多 symbol / 多源对比',
    cases: ['看某只股票的行情走势', '检查批次修订是否影响图表', '横向对比多只股票'],
    tags: ['KLineChart', 'OHLCV'],
  },
  {
    id: 'fundamentals', to: '/data/charts/fundamentals', l: '财务时间序列', en: 'fundamentals',
    d: '净利润 / ROE / 毛利率 · 报告期视角 · 同比环比 · 行业对比',
    cases: ['跟踪某只股票财务趋势', '横向对比同行业指标', 'as_of 视角下真实可用数据'],
    tags: ['ECharts', 'as_of'],
  },
  {
    id: 'explorer', to: '/data/charts/explorer', l: '通用图表浏览器', en: 'explorer',
    d: '任意表 / 列 / 聚合 / 图表类型 · 临时探索 · 存为模板',
    cases: ['临时分析一张表', '出一张一次性的图', '探索字段之间的关系'],
    tags: ['ECharts', '*'],
  },
];

const TYPE_TO_PATH = {
  全景: '/data/charts/market', 行业: '/data/charts/industry',
  K线: '/data/charts/kline', 财务: '/data/charts/fundamentals', Explorer: '/data/charts/explorer',
};

export default function ChartsHub() {
  const nav = useNavigate();
  const recent = useMemo(() => {
    try { return JSON.parse(localStorage.getItem(RECENT_KEY) || '[]'); } catch { return []; }
  }, []);

  return (
    <TerminalShell
      module="viz" current="center" path={['viz/', 'center']}
      fkeys={[
        { k: 'F1', l: '市场全景', onClick: () => nav('/data/charts/market') },
        { k: 'F2', l: '行业视图', onClick: () => nav('/data/charts/industry') },
        { k: 'F3', l: 'K线', onClick: () => nav('/data/charts/kline') },
        { k: 'F4', l: '财务', onClick: () => nav('/data/charts/fundamentals') },
      ]}
      title="数据可视化中心"
      subtitle="5 workbenches · 市场全景 / 行业视图 / K线 / 财务时序 / Explorer · pick one to start"
    >
      <div style={{ flex: 1, padding: 20, display: 'flex', flexDirection: 'column', gap: 22, minHeight: 0, overflow: 'auto' }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12 }}>
          {CARDS.map((c) => (
            <div key={c.id} style={{
              padding: 16, background: 'var(--surface)',
              border: c.isNew ? '1px solid var(--ok)' : '1px solid var(--border)', borderRadius: 4,
              display: 'flex', flexDirection: 'column', gap: 9, minHeight: 340, position: 'relative',
            }}>
              {c.isNew && (
                <span style={{
                  position: 'absolute', top: 10, right: 10, fontFamily: 'var(--font-mono)', fontSize: 9,
                  color: 'var(--ok)', letterSpacing: '0.08em', fontWeight: 600,
                  border: '1px solid var(--ok)', padding: '0 6px', borderRadius: 2,
                }}>NEW</span>
              )}
              <div style={{
                width: 38, height: 38, borderRadius: 4,
                background: c.isNew ? 'var(--ok-bg)' : 'var(--surface-2)', border: '1px solid var(--border)',
                display: 'grid', placeItems: 'center', color: c.isNew ? 'var(--ok)' : 'var(--fg)',
              }}>
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.4">{ICONS[c.id]}</svg>
              </div>
              <div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 3 }}>{c.en}</div>
                <div style={{ fontSize: 16, fontWeight: 500, color: 'var(--fg)', letterSpacing: '-0.01em' }}>{c.l}</div>
              </div>
              <div style={{ fontSize: 11.5, color: 'var(--fg-2)', lineHeight: 1.5 }}>{c.d}</div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.08em', marginTop: 4, marginBottom: 2 }}>典型场景</div>
              <ul style={{ margin: 0, padding: '0 0 0 15px', fontSize: 10.5, color: 'var(--fg-2)', lineHeight: 1.55 }}>
                {c.cases.map((s) => <li key={s}>{s}</li>)}
              </ul>
              <div style={{ flex: 1 }} />
              <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                {c.tags.map((t) => (
                  <span key={t} style={{
                    fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--fg-3)', background: 'var(--surface-2)',
                    border: '1px solid var(--border)', padding: '1px 6px', borderRadius: 2,
                  }}>{t}</span>
                ))}
              </div>
              <button className="btn sm" onClick={() => nav(c.to)} style={{
                marginTop: 4, background: c.isNew ? 'var(--ok)' : 'var(--fg)', color: 'var(--bg)',
                borderColor: c.isNew ? 'var(--ok)' : 'var(--fg)', justifyContent: 'space-between',
              }}>
                <span>进入</span><span>→</span>
              </button>
            </div>
          ))}
        </div>

        <div>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 10 }}>
            RECENTLY VIEWED · 你最近打开过
          </div>
          {recent.length === 0 ? (
            <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-4)' }}>— 暂无记录 —</div>
          ) : (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {recent.map((r, i) => (
                <span key={i} onClick={() => nav(TYPE_TO_PATH[r.type] || '/data/charts')} style={{
                  fontFamily: 'var(--font-mono)', fontSize: 11, padding: '4px 10px', background: 'var(--surface)',
                  border: '1px solid var(--border)', borderRadius: 3, color: 'var(--fg-2)', cursor: 'pointer',
                  display: 'inline-flex', alignItems: 'center', gap: 8,
                }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--hover)')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--surface)')}>
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
