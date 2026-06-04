// 可观测 · 拓扑 — /observ/topology (可观测层重构方案 §7A.3)
// 容器健康/重启/OOM/内存/CPU/写层 地图 + 宿主资源(磁盘) + 明细表。L0 存活、CPU 饱和、
// core-dump 纳入节点态;后端离线时用哨兵实时帧渲染存活 (简版地图),不空白报错。
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { TerminalShell } from '../../components/shell';
import { Loading } from '../../components/ui';
import { endpoints } from '../../lib/api';
import { useObsStatus } from '../../lib/obsStatus';
import {
  ObsLayerMark, ObsHealthChip, ObsKap, ObsDrill, ObsMeter, ObsResMark,
  ObsRealtime, ObsDegradeBanner, fmtUptime, agoFrom,
} from './shared';

const NW = 168; const NH = 88;
const LAYER_X = { frontend: 40, backend: 300, data: 560, factor: 840 };
const CORE_DUMP_GB = 2;            // D15 默认阈值

// derive a node's visual state from the raw observation (topology has no severity)
function nodeState(n) {
  if (n.synthetic && n.reachable == null) return 'unknown';
  if (n.reachable === false || n.oom_killed || n.status === 'exited' || n.status === 'dead'
      || (n.mem_pct ?? 0) >= 90 || (n.rw_size_gb ?? 0) >= CORE_DUMP_GB) return 'down';
  if ((n.alerts || []).length > 0 || (n.mem_pct ?? 0) >= 75 || (n.cpu_pct ?? 0) >= 90
      || (n.restart_count || 0) > 0) return 'degraded';
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
      placed.push({ ...c, _state: nodeState(c), x: isMonitor ? 700 : x, y: isMonitor ? 430 : 40 + i * (NH + 26) });
    });
  });
  return placed;
}

// 后端离线时, 用哨兵实时帧 (各层存活/资源) 合成最小拓扑节点
function nodesFromFrame(layers) {
  return ['frontend', 'backend', 'data', 'factor', 'monitor'].map((k) => ({
    name: `${k}-layer`, layer: k, status: layers[k]?.reachable === false ? 'unreachable' : 'running',
    health: 'none', restart_count: layers[k]?.restart_count ?? null, oom_killed: !!layers[k]?.oom_killed,
    mem_pct: layers[k]?.mem_pct ?? null, cpu_pct: layers[k]?.cpu_pct ?? null,
    reachable: layers[k]?.reachable, rw_size_gb: null, uptime_s: null, last_seen: null,
    alerts: [], synthetic: true,
  }));
}

export default function Topology() {
  const nav = useNavigate();
  const obs = useObsStatus();
  const q = useQuery({ queryKey: ['monitorTopology'], queryFn: endpoints.monitorTopology, refetchInterval: 30_000, enabled: obs.backendOk });
  const beDown = !obs.backendOk;
  const containers = beDown ? nodesFromFrame(obs.layers) : (q.data?.containers || []);
  const disks = (beDown ? obs.infra?.disk : q.data?.infra?.disk) || [];
  const nodes = layout(containers);

  const cy = (n) => n.y + NH / 2;
  const cx = (n) => n.x + NW / 2;
  const first = (layer) => nodes.find((n) => n.layer === layer);
  const flow = [['frontend', 'backend'], ['backend', 'data'], ['backend', 'factor']];
  const factorNodes = nodes.filter((n) => n.layer === 'factor');
  const edges = flow.map(([a, b]) => [first(a), first(b)]).filter(([a, b]) => a && b);
  if (factorNodes.length === 2) edges.push([factorNodes[0], factorNodes[1]]);
  const sentinel = nodes.find((n) => n.layer === 'monitor');
  const watch = nodes.filter((n) => n.layer !== 'monitor');

  const down = nodes.filter((n) => n._state === 'down').length;
  const degraded = nodes.filter((n) => n._state === 'degraded').length;
  const height = Math.max(540, 40 + Math.max(...[1, ...Object.values(groupBy(nodes)).map((g) => g.length)]) * (NH + 26) + 120);

  return (
    <TerminalShell
      module="observ" current="topology"
      path={['observ/', 'topology']}
      fkeys={[
        { k: 'F1', l: '总览', onClick: () => nav('/observ/overview') },
        { k: 'F2', l: '告警', onClick: () => nav('/observ/alerts') },
        { k: 'F3', l: '日志', onClick: () => nav('/observ/logs') },
        { k: 'F5', l: '刷新', onClick: () => { q.refetch(); obs.refetch(); } },
      ]}
      title="拓扑"
      subtitle={beDown ? 'topology · 后端不可达 · 哨兵实时存活 (简版地图)' : 'topology · 容器 / 层健康地图 · 外部观察视角'}
      badge={<ObsRealtime state={obs.source} detail={beDown ? '后端故障' : ''} />}
      actions={<button className="btn ghost sm" onClick={() => { q.refetch(); obs.refetch(); }}>↻ 刷新</button>}
      statusMiddle={<span>{containers.length} 节点 · {beDown ? '哨兵实时帧' : q.data?.sentinel ? '哨兵在线' : '哨兵未上线 (合成视图)'}</span>}
    >
      {q.isLoading && !beDown ? <Loading label="加载拓扑…" />
        : (
          <div className="scroll" style={{ flex: 1, minHeight: 0, overflow: 'auto', background: 'var(--bg)' }}>
            {beDown && <ObsDegradeBanner scope="topology" />}

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
                {nodes.map((n) => <TopoNode key={n.name} n={n} onClick={() => !n.synthetic && nav(`/observ/logs?source=${n.layer}`)} />)}
              </div>
            </div>

            {/* 宿主资源 (磁盘 — 宿主级, 不属单容器, §7A.3) */}
            <div style={{ background: 'var(--surface)', borderBottom: '1px solid var(--border-strong)' }}>
              <div style={{ padding: '7px 14px', display: 'flex', alignItems: 'center', gap: 10, background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}>
                <ObsResMark res="disk" tone={disks.some((d) => d.state === 'error') ? 'err' : disks.some((d) => d.state === 'warn') ? 'warn' : 'ok'} />
                <ObsKap>宿主资源 · 磁盘水位</ObsKap>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>· 宿主级 · 哨兵 statvfs 直采 · 非单容器</span>
              </div>
              <div style={{ padding: '12px 18px', display: 'flex', flexDirection: 'column', gap: 11, maxWidth: 640 }}>
                {disks.length === 0 ? <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-mute)' }}>哨兵未上报磁盘 (未挂载宿主分区)</span>
                  : disks.map((d) => <ObsMeter key={d.mount} label={d.mount} pct={d.used_pct} warnAt={85} dangerAt={95} />)}
              </div>
            </div>

            {/* container detail table */}
            <div style={{ background: 'var(--surface)' }}>
              <div style={{ padding: '7px 14px', display: 'flex', alignItems: 'center', gap: 10, background: 'var(--surface-2)', borderBottom: '1px solid var(--border)' }}>
                <ObsKap>容器明细</ObsKap><span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>· {containers.length} 节点 · 点击行 → 日志</span>
              </div>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
                <thead><tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
                  {['容器', '层', '探测', '健康', '重启', '强杀', 'CPU%', '内存%', '写层', '运行时长', '最后观测', '告警', ''].map((c) => (
                    <th key={c} style={{ textAlign: ['CPU%', '内存%', '重启', '写层'].includes(c) ? 'right' : 'left', padding: '5px 9px', fontSize: 9, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.04em', whiteSpace: 'nowrap' }}>{c}</th>
                  ))}
                </tr></thead>
                <tbody>
                  {nodes.map((n, i) => {
                    const dn = n._state === 'down'; const dg = n._state === 'degraded';
                    const dump = (n.rw_size_gb ?? 0) >= CORE_DUMP_GB;
                    return (
                      <tr key={n.name} onClick={() => !n.synthetic && nav(`/observ/logs?source=${n.layer}`)} style={{ borderBottom: '1px solid var(--border-faint)', height: 30, cursor: n.synthetic ? 'default' : 'pointer', background: dn ? 'var(--err-bg)' : dg ? 'color-mix(in oklch, var(--warn-bg) 50%, transparent)' : i % 2 ? 'rgba(0,0,0,0.012)' : 'transparent' }}>
                        <td style={{ padding: '0 9px', color: 'var(--fg)', fontWeight: 500 }}>{n.name}</td>
                        <td style={{ padding: '0 9px' }}><ObsLayerMark layer={n.layer} /></td>
                        <td style={{ padding: '0 9px' }}>{n.reachable === false ? <span style={{ color: 'var(--err)', fontWeight: 600 }}>不可达</span> : n.reachable === true ? <span style={{ color: 'var(--ok)' }}>可达</span> : <span style={{ color: 'var(--fg-mute)' }}>—</span>}</td>
                        <td style={{ padding: '0 9px' }}>{n._state === 'unknown' ? <span style={{ color: 'var(--fg-mute)' }}>—</span> : <ObsHealthChip state={n._state === 'down' ? 'down' : n._state === 'degraded' ? 'degraded' : 'ok'} mini />}</td>
                        <td style={{ padding: '0 9px', textAlign: 'right', color: n.restart_count > 0 ? 'var(--err)' : 'var(--fg-2)', fontWeight: n.restart_count > 0 ? 600 : 400, fontVariantNumeric: 'tabular-nums' }}>{n.restart_count != null ? (n.restart_count > 0 ? `↑ ${n.restart_count}` : 0) : '—'}</td>
                        <td style={{ padding: '0 9px' }}>{n.oom_killed ? <span className="chip err" style={{ fontSize: 9.5, height: 15 }}>OOM</span> : <span style={{ color: 'var(--fg-mute)' }}>—</span>}</td>
                        <td style={{ padding: '0 9px', textAlign: 'right', color: (n.cpu_pct ?? 0) >= 90 ? 'var(--err)' : (n.cpu_pct ?? 0) >= 70 ? 'var(--warn)' : 'var(--fg-2)', fontVariantNumeric: 'tabular-nums' }}>{n.cpu_pct != null ? `${Math.round(n.cpu_pct)}%` : '—'}</td>
                        <td style={{ padding: '0 9px', textAlign: 'right', color: (n.mem_pct ?? 0) >= 85 ? 'var(--err)' : (n.mem_pct ?? 0) >= 70 ? 'var(--warn)' : 'var(--fg-2)', fontVariantNumeric: 'tabular-nums' }}>{n.mem_pct != null ? `${n.mem_pct}%` : '—'}</td>
                        <td style={{ padding: '0 9px', textAlign: 'right' }}>{dump ? <span style={{ display: 'inline-flex', alignItems: 'center', gap: 3, color: 'var(--err)', fontWeight: 600 }}><ObsResMark res="dump" tone="err" />{n.rw_size_gb}G</span> : n.rw_size_gb != null ? <span style={{ color: 'var(--fg-3)' }}>{n.rw_size_gb}G</span> : <span style={{ color: 'var(--fg-mute)' }}>—</span>}</td>
                        <td style={{ padding: '0 9px', color: dn ? 'var(--err)' : 'var(--fg-2)' }}>{fmtUptime(n.uptime_s)}</td>
                        <td style={{ padding: '0 9px', color: 'var(--fg-3)', fontSize: 10.5 }}>{n.last_seen ? agoFrom(n.last_seen) : '—'}</td>
                        <td style={{ padding: '0 9px' }}>{n.alerts?.length ? <span className="chip err" style={{ fontSize: 9.5, height: 15 }}>▲ {n.alerts.length}</span> : <span style={{ color: 'var(--fg-mute)' }}>—</span>}</td>
                        <td style={{ padding: '0 9px', textAlign: 'right' }}>{!n.synthetic && <ObsDrill>日志</ObsDrill>}</td>
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
  const mem = n.mem_pct; const cpu = n.cpu_pct;
  const dump = (n.rw_size_gb ?? 0) >= CORE_DUMP_GB;
  return (
    <div onClick={onClick} title={`运行时长 ${fmtUptime(n.uptime_s)}${n.cpu_pct != null ? ` · cpu ${Math.round(n.cpu_pct)}%` : ''}${n.alerts?.length ? ` · ${n.alerts.length} 活跃告警` : ''}`}
      style={{
        position: 'absolute', left: n.x, top: n.y, width: NW, height: NH,
        background: dn ? 'var(--err-bg)' : 'var(--surface)',
        border: `1px solid ${dn || dg ? col : 'var(--border-strong)'}`,
        borderRadius: 5, padding: '8px 10px', cursor: onClick ? 'pointer' : 'default',
        boxShadow: dn ? '0 0 0 4px color-mix(in oklch, var(--err) 18%, transparent), 0 2px 10px rgba(0,0,0,0.08)' : dg ? '0 0 0 3px color-mix(in oklch, var(--warn) 14%, transparent)' : '0 1px 4px rgba(0,0,0,0.05)',
        animation: dn ? 'pulse 1.6s ease-in-out infinite' : 'none',
        display: 'flex', flexDirection: 'column', gap: 5, zIndex: 2,
      }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <ObsLayerMark layer={n.layer} />
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11.5, color: 'var(--fg)', fontWeight: 500, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{n.name}</span>
        {dump && <span title="崩溃转储"><ObsResMark res="dump" tone="err" /></span>}
        <span className="dot" style={{ margin: 0, background: col, animation: dn ? 'pulse 1.4s ease-in-out infinite' : 'none' }} />
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)' }}>
        <span style={{ color: n.reachable === false ? 'var(--err)' : 'var(--fg-4)' }}>{n.reachable === false ? '不可达' : n.status}</span>
        <span style={{ flex: 1 }} />
        {n.restart_count > 0 && <span style={{ color: 'var(--err)', fontWeight: 600 }}>↺{n.restart_count}</span>}
        {n.oom_killed && <span className="chip err" style={{ fontSize: 8.5, height: 13, padding: '0 3px' }}>OOM</span>}
      </div>
      {/* mem bar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 8, color: 'var(--fg-4)', width: 22 }}>mem</span>
        <div style={{ flex: 1, height: 4, background: 'var(--surface-3)', borderRadius: 2, position: 'relative', overflow: 'hidden' }}>
          <div style={{ position: 'absolute', inset: '0 auto 0 0', width: `${dn ? 0 : (mem || 0)}%`, background: (mem ?? 0) >= 85 ? 'var(--err)' : (mem ?? 0) >= 70 ? 'var(--warn)' : 'var(--ok)' }} />
        </div>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: dn ? 'var(--err)' : 'var(--fg-3)', width: 30, textAlign: 'right' }}>{n.oom_killed ? 'kill' : mem != null ? `${mem}%` : '—'}</span>
      </div>
      {/* cpu bar */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 8, color: 'var(--fg-4)', width: 22 }}>cpu</span>
        <div style={{ flex: 1, height: 4, background: 'var(--surface-3)', borderRadius: 2, position: 'relative', overflow: 'hidden' }}>
          <div style={{ position: 'absolute', inset: '0 auto 0 0', width: `${dn ? 0 : Math.min(cpu || 0, 100)}%`, background: (cpu ?? 0) >= 90 ? 'var(--err)' : (cpu ?? 0) >= 70 ? 'var(--warn)' : 'var(--ok)' }} />
        </div>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: (cpu ?? 0) >= 90 ? 'var(--err)' : 'var(--fg-3)', width: 30, textAlign: 'right' }}>{cpu != null ? `${Math.round(cpu)}%` : '—'}</span>
      </div>
    </div>
  );
}
