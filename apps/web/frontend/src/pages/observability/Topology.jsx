// 可观测 · 拓扑 — /observ/topology (可观测层重构方案 §7.3③)
// 外部观察的可视化: 容器健康/重启/OOM/内存 地图 + 明细表。节点集以真实容器为准。
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { TerminalShell } from '../../components/shell';
import { Loading, ErrorState } from '../../components/ui';
import { endpoints } from '../../lib/api';
import { ObsLayerMark, ObsHealthChip, ObsKap, ObsDrill, ObsLive, fmtUptime, agoFrom } from './shared';

const NW = 168;
const NH = 80;
const LAYER_X = { frontend: 40, backend: 300, data: 560, factor: 840 };

// derive a node's visual state from the raw observation (topology has no severity)
function nodeState(n) {
  if (n.synthetic) return 'unknown';
  if (n.oom_killed || n.status === 'exited' || n.status === 'dead' || (n.mem_pct ?? 0) >= 90) return 'down';
  if ((n.alerts || []).length > 0 || (n.mem_pct ?? 0) >= 75 || (n.restart_count || 0) > 0) return 'degraded';
  return 'ok';
}
const STATE_COL = { ok: 'var(--ok)', degraded: 'var(--warn)', down: 'var(--err)', unknown: 'var(--fg-mute)' };

function layout(containers) {
  const byLayer = {};
  containers.forEach((c) => { (byLayer[c.layer] = byLayer[c.layer] || []).push(c); });
  const placed = [];
  Object.entries(byLayer).forEach(([layer, list]) => {
    const x = LAYER_X[layer] ?? 560;
    list.forEach((c, i) => {
      const isMonitor = layer === 'monitor';
      placed.push({
        ...c, _state: nodeState(c),
        x: isMonitor ? 700 : x,
        y: isMonitor ? 410 : 40 + i * (NH + 26),
      });
    });
  });
  return placed;
}

export default function Topology() {
  const nav = useNavigate();
  const q = useQuery({ queryKey: ['monitorTopology'], queryFn: endpoints.monitorTopology, refetchInterval: 30_000 });
  const containers = q.data?.containers || [];
  const nodes = layout(containers);
  const byId = (name) => nodes.find((n) => n.name === name);
  const cy = (n) => n.y + NH / 2;
  const cx = (n) => n.x + NW / 2;

  // data-flow edges: connect a layer's first node to the next layer's first node.
  const first = (layer) => nodes.find((n) => n.layer === layer);
  const flow = [['frontend', 'backend'], ['backend', 'data'], ['backend', 'factor']];
  const factorNodes = nodes.filter((n) => n.layer === 'factor');
  const edges = flow.map(([a, b]) => [first(a), first(b)]).filter(([a, b]) => a && b);
  if (factorNodes.length === 2) edges.push([factorNodes[0], factorNodes[1]]);
  const sentinel = nodes.find((n) => n.layer === 'monitor');
  const watch = nodes.filter((n) => n.layer !== 'monitor');

  const down = nodes.filter((n) => n._state === 'down').length;
  const degraded = nodes.filter((n) => n._state === 'degraded').length;
  const height = Math.max(500, 40 + Math.max(...[1, ...Object.values(groupBy(nodes)).map((g) => g.length)]) * (NH + 26) + 120);

  return (
    <TerminalShell
      module="observ" current="topology"
      path={['observ/', 'topology']}
      fkeys={[
        { k: 'F1', l: '总览', onClick: () => nav('/observ/overview') },
        { k: 'F2', l: '告警', onClick: () => nav('/observ/alerts') },
        { k: 'F3', l: '日志', onClick: () => nav('/observ/logs') },
        { k: 'F5', l: '刷新', onClick: () => q.refetch() },
      ]}
      title="拓扑"
      subtitle="topology · 容器 / 层健康地图 · 外部观察视角 (30s 刷新)"
      badge={<ObsLive>实时</ObsLive>}
      actions={<button className="btn ghost sm" onClick={() => q.refetch()}>↻ 刷新</button>}
      statusMiddle={<span>{containers.length} 容器 · {q.data?.sentinel ? '哨兵在线' : '哨兵未上线 (合成视图)'}</span>}
    >
      {q.isLoading ? <Loading label="加载拓扑…" />
        : q.error ? <ErrorState error={q.error} onRetry={q.refetch} />
          : (
            <div className="scroll" style={{ flex: 1, minHeight: 0, overflow: 'auto', background: 'var(--bg)' }}>
              {/* graph */}
              <div style={{ background: 'var(--surface)', borderBottom: '1px solid var(--border-strong)' }}>
                <div style={{ padding: '7px 14px', display: 'flex', alignItems: 'center', gap: 14, background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}>
                  <ObsKap>健康地图</ObsKap>
                  <span style={{ display: 'inline-flex', gap: 12, fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>
                    <span><span className="dot ok" />正常</span>
                    <span><span className="dot warn" />降级</span>
                    <span><span className="dot err" />故障</span>
                    <span style={{ color: 'var(--fg-4)' }}>—— 数据流</span>
                    <span style={{ color: 'var(--fg-4)' }}>┄┄ 哨兵观察</span>
                  </span>
                  <span style={{ flex: 1 }} />
                  {(down + degraded) > 0 && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--err)', fontWeight: 600 }}>⚠ {down} 故障 · {degraded} 降级</span>}
                </div>
                <div style={{ position: 'relative', height, padding: '0 24px' }}>
                  <svg width="100%" height={height} style={{ position: 'absolute', inset: 0, left: 24, overflow: 'visible' }}>
                    {edges.map(([a, b], i) => {
                      const x1 = a.x + NW; const y1 = cy(a); const x2 = b.x; const y2 = cy(b); const mx = (x1 + x2) / 2;
                      const col = STATE_COL[b._state] === STATE_COL.ok ? 'var(--border-strong)' : STATE_COL[b._state];
                      return (
                        <g key={i}>
                          <path d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`} fill="none" stroke={col} strokeWidth={b._state === 'ok' ? 1.4 : 2} opacity={b._state === 'ok' ? 0.5 : 0.85} />
                          <circle cx={x2 - 2} cy={y2} r={2.6} fill={col} />
                        </g>
                      );
                    })}
                    {sentinel && watch.map((b, i) => (
                      <path key={`w${i}`} d={`M${cx(sentinel)},${sentinel.y} Q${cx(sentinel)},${cy(b)} ${cx(b)},${b.y + NH}`} fill="none" stroke="var(--fg-mute)" strokeWidth={1} strokeDasharray="2 3" opacity={0.5} />
                    ))}
                  </svg>
                  {nodes.map((n) => <TopoNode key={n.name} n={n} onClick={() => nav(`/observ/logs?source=${n.layer}`)} />)}
                </div>
              </div>

              {/* container detail table */}
              <div style={{ background: 'var(--surface)' }}>
                <div style={{ padding: '7px 14px', display: 'flex', alignItems: 'center', gap: 10, background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}>
                  <ObsKap>容器明细</ObsKap><span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>· {containers.length} 容器 · 点击行 → 日志</span>
                </div>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
                  <thead><tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
                    {['容器', '层', '健康', '重启', '被强杀', '内存%', '运行时长', '最后观测', '当前告警', ''].map((c) => (
                      <th key={c} style={{ textAlign: c === '内存%' || c === '重启' ? 'right' : 'left', padding: '5px 10px', fontSize: 9, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.05em', whiteSpace: 'nowrap' }}>{c}</th>
                    ))}
                  </tr></thead>
                  <tbody>
                    {nodes.map((n, i) => {
                      const dn = n._state === 'down'; const dg = n._state === 'degraded';
                      return (
                        <tr key={n.name} onClick={() => nav(`/observ/logs?source=${n.layer}`)} style={{ borderBottom: '1px solid var(--border-faint)', height: 30, cursor: 'pointer', background: dn ? 'var(--err-bg)' : dg ? 'color-mix(in oklch, var(--warn-bg) 50%, transparent)' : i % 2 ? 'rgba(0,0,0,0.012)' : 'transparent' }}>
                          <td style={{ padding: '0 10px', color: 'var(--fg)', fontWeight: 500 }}>{n.name}</td>
                          <td style={{ padding: '0 10px' }}><ObsLayerMark layer={n.layer} /></td>
                          <td style={{ padding: '0 10px' }}>{n._state === 'unknown' ? <span style={{ color: 'var(--fg-mute)' }}>—</span> : <ObsHealthChip state={n._state} mini />}</td>
                          <td style={{ padding: '0 10px', textAlign: 'right', color: n.restart_count > 0 ? 'var(--err)' : 'var(--fg-2)', fontWeight: n.restart_count > 0 ? 600 : 400, fontVariantNumeric: 'tabular-nums' }}>{n.restart_count != null ? (n.restart_count > 0 ? `↑ ${n.restart_count}` : 0) : '—'}</td>
                          <td style={{ padding: '0 10px' }}>{n.oom_killed ? <span className="chip err" style={{ fontSize: 9.5, height: 15 }}>OOM</span> : <span style={{ color: 'var(--fg-mute)' }}>—</span>}</td>
                          <td style={{ padding: '0 10px', textAlign: 'right', color: (n.mem_pct ?? 0) >= 85 ? 'var(--err)' : (n.mem_pct ?? 0) >= 70 ? 'var(--warn)' : 'var(--fg-2)', fontVariantNumeric: 'tabular-nums' }}>{n.mem_pct != null ? `${n.mem_pct}%` : '—'}</td>
                          <td style={{ padding: '0 10px', color: dn ? 'var(--err)' : 'var(--fg-2)' }}>{fmtUptime(n.uptime_s)}</td>
                          <td style={{ padding: '0 10px', color: 'var(--fg-3)', fontSize: 10.5 }}>{n.last_seen ? agoFrom(n.last_seen) : '—'}</td>
                          <td style={{ padding: '0 10px' }}>{n.alerts?.length ? <span className="chip err" style={{ fontSize: 9.5, height: 15 }}>▲ {n.alerts.length}</span> : <span style={{ color: 'var(--fg-mute)' }}>—</span>}</td>
                          <td style={{ padding: '0 10px', textAlign: 'right' }}><ObsDrill>日志</ObsDrill></td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}
    </TerminalShell>
  );
}

function groupBy(nodes) {
  const o = {};
  nodes.forEach((n) => { (o[n.layer] = o[n.layer] || []).push(n); });
  return o;
}

function TopoNode({ n, onClick }) {
  const dn = n._state === 'down'; const dg = n._state === 'degraded';
  const col = STATE_COL[n._state];
  const mem = n.mem_pct;
  return (
    <div onClick={onClick} title={`运行时长 ${fmtUptime(n.uptime_s)}${n.alerts?.length ? ` · ${n.alerts.length} 活跃告警` : ''}`}
      style={{
        position: 'absolute', left: n.x, top: n.y, width: NW, height: NH,
        background: dn ? 'var(--err-bg)' : 'var(--surface)',
        border: `1px solid ${dn || dg ? col : 'var(--border-strong)'}`,
        borderRadius: 5, padding: '8px 10px', cursor: 'pointer',
        boxShadow: dn ? '0 0 0 4px color-mix(in oklch, var(--err) 18%, transparent), 0 2px 10px rgba(0,0,0,0.08)' : dg ? '0 0 0 3px color-mix(in oklch, var(--warn) 14%, transparent)' : '0 1px 4px rgba(0,0,0,0.05)',
        animation: dn ? 'pulse 1.6s ease-in-out infinite' : 'none',
        display: 'flex', flexDirection: 'column', gap: 6, zIndex: 2,
      }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <ObsLayerMark layer={n.layer} />
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11.5, color: 'var(--fg)', fontWeight: 500, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{n.name}</span>
        <span className="dot" style={{ margin: 0, background: col, animation: dn ? 'pulse 1.4s ease-in-out infinite' : 'none' }} />
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)' }}>
        <span style={{ color: 'var(--fg-4)' }}>{n.status}</span>
        <span style={{ flex: 1 }} />
        {n.restart_count > 0 && <span style={{ color: 'var(--err)', fontWeight: 600 }}>↺{n.restart_count}</span>}
        {n.oom_killed && <span className="chip err" style={{ fontSize: 8.5, height: 13, padding: '0 3px' }}>OOM</span>}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <div style={{ flex: 1, height: 4, background: 'var(--surface-3)', borderRadius: 2, position: 'relative', overflow: 'hidden' }}>
          <div style={{ position: 'absolute', inset: '0 auto 0 0', width: `${dn ? 0 : (mem || 0)}%`, background: (mem ?? 0) >= 85 ? 'var(--err)' : (mem ?? 0) >= 70 ? 'var(--warn)' : 'var(--ok)' }} />
        </div>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: dn ? 'var(--err)' : (mem ?? 0) >= 85 ? 'var(--err)' : (mem ?? 0) >= 70 ? 'var(--warn)' : 'var(--fg-3)', width: 34, textAlign: 'right' }}>{n.oom_killed ? 'killed' : mem != null ? `${mem}%` : '—'}</span>
      </div>
    </div>
  );
}
