// Future-module placeholder (factor / strategy / live) — nav visible, body "敬请期待".
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { ModuleStrip, StatusBar } from '../components/shell';
import { MODULES } from '../components/shell/nav';

const COPY = {
  factor: { feats: [['ic.timeseries', 'IC / IR 时间序列'], ['turnover', '换手率分析'], ['corr', '相关性矩阵']], body: '将提供因子注册、版本化、IC/IR/换手率分析、因子相关性矩阵、批量回测等能力。计划于 2026 Q3 上线。' },
  strategy: { feats: [['backtest', '向量化回测'], ['grid.search', '参数搜索'], ['attribution', '归因分析']], body: '将提供策略组合管理、参数搜索、向量化回测、归因分析等能力。计划于 2026 Q4 上线。' },
  live: { feats: [['monitor', '实时监控'], ['risk', '风控告警'], ['orders', '订单流']], body: '将与执行端对接,提供盘前/盘中/盘后监控、订单流、风控告警等能力。计划于 2027 年上线。' },
};

export default function Placeholder({ module = 'factor' }) {
  const nav = useNavigate();
  const m = MODULES.find((x) => x.id === module) || MODULES[2];
  const c = COPY[module] || COPY.factor;
  return (
    <div style={{ width: '100%', height: '100%', background: 'var(--bg)', color: 'var(--fg)', display: 'flex', flexDirection: 'column', overflow: 'hidden', fontFamily: 'var(--font-mono)' }}>
      <ModuleStrip current={module} />
      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        <aside style={{ width: 184, borderRight: '1px solid var(--border)', background: 'var(--surface-2)', padding: 14, display: 'flex', flexDirection: 'column', gap: 6 }}>
          <div style={{ fontSize: 10, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 6 }}>{m.label}</div>
          {[1, 2, 3, 4, 5].map((i) => (
            <div key={i} style={{ padding: '5px 8px', borderRadius: 4, opacity: 0.5, display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ width: 13, height: 13, border: '1px dashed var(--fg-4)', borderRadius: 2 }} />
              <div style={{ flex: 1, height: 8, background: 'var(--surface-3)', borderRadius: 1 }} />
            </div>
          ))}
        </aside>
        <main style={{ flex: 1, display: 'grid', placeItems: 'center', padding: 24, minHeight: 0 }}>
          <div style={{ textAlign: 'center', maxWidth: 520 }}>
            <div style={{ width: 80, height: 80, borderRadius: 4, border: '1px dashed var(--fg-4)', display: 'grid', placeItems: 'center', margin: '0 auto 18px', color: 'var(--fg-4)', fontSize: 34 }}>◌</div>
            <div style={{ fontSize: 10.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.12em' }}>{m.en} · {m.phase}</div>
            <h1 style={{ margin: '8px 0 0', fontFamily: 'var(--font-mono)', fontSize: 28, fontWeight: 500 }}>{m.label} · 敬请期待</h1>
            <p style={{ fontSize: 12.5, color: 'var(--fg-2)', lineHeight: 1.7, margin: '14px auto 0', maxWidth: 460 }}>{c.body}</p>
            <div style={{ marginTop: 28, display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 10 }}>
              {c.feats.map(([k, l]) => (
                <div key={k} style={{ padding: '12px 14px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 3, opacity: 0.65, textAlign: 'left' }}>
                  <div style={{ fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase' }}>{k}</div>
                  <div style={{ fontSize: 12, color: 'var(--fg-2)', marginTop: 4 }}>{l}</div>
                </div>
              ))}
            </div>
            <div style={{ marginTop: 22 }}>
              <button className="btn sm primary" onClick={() => nav('/data/dashboard')}>← 返回数据管理</button>
            </div>
          </div>
        </main>
      </div>
      <StatusBar />
    </div>
  );
}
