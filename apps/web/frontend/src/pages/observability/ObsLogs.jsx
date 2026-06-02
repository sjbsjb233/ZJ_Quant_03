// 可观测 · 日志 — /observ/logs (可观测层重构方案 §7.3④)
// 跨层全量浏览 (含 monitor)。复用 <LogPanel>,顶部加「层快捷筛选」。接管原日志中心 / 因子日志。
import React, { useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { TerminalShell } from '../../components/shell';
import LogPanel from '../logs/LogPanel';
import { OBS_LAYERS, ObsKap } from './shared';

const LAYER_FILTERS = [
  { id: 'all', label: '全部' },
  { id: 'frontend', label: '前端' },
  { id: 'backend', label: '后端' },
  { id: 'data', label: '数据' },
  { id: 'factor', label: '因子' },
  { id: 'monitor', label: '监控' },
  { id: 'infra', label: '基础设施', note: '数据+因子+监控', source: 'data,factor,monitor' },
];

function sourceFor(id) {
  if (id === 'all') return '';
  if (id === 'infra') return 'data,factor,monitor';
  return id;
}

export default function ObsLogs() {
  const nav = useNavigate();
  const [sp] = useSearchParams();
  const initial = sp.get('source') || 'all';
  const [layer, setLayer] = useState(LAYER_FILTERS.some((f) => f.id === initial) ? initial : 'all');

  return (
    <TerminalShell
      module="observ" current="logs"
      path={['observ/', 'logs']}
      fkeys={[
        { k: 'F1', l: '总览', onClick: () => nav('/observ/overview') },
        { k: 'F2', l: '实时流', onClick: () => nav('/observ/stream') },
        { k: 'F3', l: '会话', onClick: () => nav('/observ/sessions') },
        { k: 'F5', l: '拓扑', onClick: () => nav('/observ/topology') },
      ]}
      title="日志"
      subtitle="logs · 跨所有层全量检索 (含 monitor) · 接管原日志中心 / 因子日志"
      badge={<span className="chip neutral" style={{ fontSize: 10 }}>{LAYER_FILTERS.find((f) => f.id === layer)?.label}</span>}
    >
      {/* layer quick-filter */}
      <div style={{ padding: '7px 14px', background: 'var(--surface)', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
        <ObsKap style={{ marginRight: 4 }}>层</ObsKap>
        {LAYER_FILTERS.map((f) => {
          const on = layer === f.id;
          const lc = OBS_LAYERS[f.id];
          return (
            <button key={f.id} onClick={() => setLayer(f.id)} title={f.note} data-testid={`obslogs-layer-${f.id}`} style={{
              display: 'inline-flex', alignItems: 'center', gap: 5, padding: '2px 9px', height: 22,
              border: `1px solid ${on ? (lc ? lc.c : 'var(--fg)') : 'var(--border-strong)'}`,
              background: on ? (lc ? `color-mix(in oklch, ${lc.c} 14%, var(--surface))` : 'var(--fg)') : 'var(--surface)',
              color: on ? (lc ? lc.c : 'var(--bg)') : 'var(--fg-2)',
              borderRadius: 3, cursor: 'pointer', fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: on ? 600 : 400,
            }}>
              {lc && <span style={{ width: 7, height: 7, borderRadius: 2, background: lc.c }} />}
              {f.label}
            </button>
          );
        })}
        <span style={{ flex: 1 }} />
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>后端按角色收窄 source · viewer 看不到 monitor</span>
      </div>

      {/* remount the panel when the quick-filter changes so the initial source applies */}
      <LogPanel
        key={layer}
        source={sourceFor(layer)}
        lockSource={false}
        defaultLevel={sp.get('level') || ''}
        defaultEvent={sp.get('event') || ''}
        defaultTrace={sp.get('trace_id') || ''}
        onTrace={(t) => nav(`/observ/trace/${t}`)}
      />
    </TerminalShell>
  );
}
