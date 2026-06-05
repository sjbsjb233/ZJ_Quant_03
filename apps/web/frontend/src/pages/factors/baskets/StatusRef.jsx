// /factors/statusref — 状态徽标速查 (因子层重构 §10.6). The single source of
// truth for the whole status vocabulary. Every basket page renders status via
// the kit's <Badge group value/>; this page lists every group + item so the
// vocabulary stays legible (color + character double-encoding).
import React from 'react';
import { TerminalShell } from '../../../components/shell';
import { BADGE_DEFS, Badge, StaleBanner, LightHint } from './kit';

const GROUP_ORDER = [
  'run', 'mode', 'trigger', 'degrade', 'settle', 'stale',
  'tracking', 'wait', 'exclude', 'selection', 'decision',
];

const FAKE_STALE = {
  is: true, fromRev: 3, toRev: 4,
  scope: '全量重建',
  reason: '股票池由 沪深300 改为 中证800',
};

export default function StatusRef() {
  const total = GROUP_ORDER.reduce((s, gid) => s + (BADGE_DEFS[gid]?.items.length || 0), 0);

  return (
    <TerminalShell
      module="factor"
      current="statusref"
      path={['factor_mgmt/', '状态徽标速查']}
      title="状态徽标速查"
      subtitle="全站统一徽标系统 · 双编码(颜色+字符)"
      actions={<span className="chip neutral" style={{ height: 22 }}>{total} 个徽标 · {GROUP_ORDER.length} 类</span>}
    >
      <div className="scroll" style={{ flex: 1, minHeight: 0, background: 'var(--bg)' }}>
        {/* intro / header note */}
        <div style={{ padding: '14px 20px', borderBottom: '1px solid var(--border)', background: 'var(--surface)' }}>
          <div style={{ fontSize: 12, color: 'var(--fg-2)', lineHeight: 1.7, maxWidth: 880 }}>
            这些状态会大量出现在表格行、卡片、详情头部。每个徽标都同时携带
            <b style={{ color: 'var(--fg)' }}> 颜色语义</b>（ok 绿 / warn 黄 / err 红 / info 蓝 / neutral 灰）与一个
            <b style={{ color: 'var(--fg)' }}> 字符标记</b>，保证在去色 / 高密度下仍可辨认。
            <b style={{ color: 'var(--fg)' }}> 全站所有页面统一调用该 Badge 组件</b>，改这里即改全站。
          </div>
        </div>

        {/* badge groups grid */}
        <div style={{ padding: 20, display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
          {GROUP_ORDER.map((gid) => {
            const g = BADGE_DEFS[gid];
            if (!g) return null;
            return (
              <div key={gid} style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
                <div style={{ padding: '8px 14px', background: 'var(--surface-2)', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 500, color: 'var(--fg)' }}>{g.title}</span>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--fg-mute)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{gid}</span>
                  <div style={{ flex: 1 }} />
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)' }}>{g.where}</span>
                </div>
                <div style={{ padding: '12px 14px', display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
                  {g.items.map((it) => (
                    <div key={it.v} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 5, minWidth: 70 }}>
                      <Badge group={gid} value={it.v} />
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 8.5, color: 'var(--fg-mute)' }}>{it.char} · {it.tone}</span>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>

        {/* §10.6 · component formes */}
        <div style={{ padding: '4px 20px 8px' }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.08em', margin: '10px 0' }}>
            组件形态 · 会反复出现的徽标组合
          </div>
        </div>
        <div style={{ padding: '0 20px 24px', display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
          {/* persistent banner · stale */}
          <RefCard title="持久横幅 · stale">
            <StaleBanner stale={FAKE_STALE} onRerun={() => {}} />
          </RefCard>

          {/* light hints · info + warn */}
          <RefCard title="轻提示条 · 两档">
            <div style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 8 }}>
              <LightHint tone="info" action={<button className="btn sm">切到最新</button>}>
                当前为非最新快照，建议基于篮子最新结果优选
              </LightHint>
              <LightHint tone="warn">3 个条目因样本不足未参与剪枝</LightHint>
            </div>
          </RefCard>

          {/* badges applied inside a table row */}
          <RefCard title="徽标在表格行内的实际用法">
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
              <tbody>
                {[
                  ['反转·5d', '成功', '无'],
                  ['动量·60d', '降级', '部分'],
                  ['动量·10d', '失败', '严重'],
                ].map(([label, run, deg]) => (
                  <tr key={label} style={{ borderTop: '1px solid var(--border-faint)', height: 30 }}>
                    <td style={{ padding: '0 14px', fontFamily: 'var(--font-mono)', color: 'var(--fg)' }}>{label}</td>
                    <td style={{ padding: '0 8px' }}><Badge group="run" value={run} sm /></td>
                    <td style={{ padding: '0 14px' }}><Badge group="degrade" value={deg} sm /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </RefCard>
        </div>
      </div>
    </TerminalShell>
  );
}

function RefCard({ title, children }) {
  return (
    <div style={{ background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 6, overflow: 'hidden' }}>
      <div style={{ padding: '7px 14px', background: 'var(--surface-2)', borderBottom: '1px solid var(--border)', fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-2)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{title}</div>
      <div>{children}</div>
    </div>
  );
}
