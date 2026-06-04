// useObsStatus — the double-source 合流 hook (可观测层重构方案 §7.2 / §7A.0②).
//
// 轴线: 存活看哨兵 (第一层 SSE 实时帧, 绕开 backend), 历史/告警看后端 (第二层轮询).
//   首选  /obs/v1/status/stream (SSE)  → 各层存活/基建真相 + 实时
//   回退  /monitor/health (轮询)        → 富裁决 + 告警明细 (firing/根因)
// 合流裁决: 哨兵存活/基建优先 (reachable=false ⇒ down), backend 在线时叠加告警明细;
// 若 down 的恰是 backend, 以哨兵帧为准把 backend 染红; 两路皆断 ⇒ 整体 down (绝不伪绿).
//
// source 三态 (用户可见 实时/延迟/中断, 内部键 live/delayed/stale):
//   live    = SSE 开 + 最近收到帧
//   delayed = SSE 断但 /monitor/health 仍在线 (功能不减)
//   stale   = SSE 断 且 /monitor/health 也连不上 (后端 + 哨兵皆不可达)
import React from 'react';
import { useSystemHealth } from './health';
import { obsStatusStreamUrl, tokenStore } from './api';

const STALE_MS = 35_000;                 // 超过 ~3× 心跳没收到帧 ⇒ 视 SSE 不再实时
const LAYER_KEYS = ['frontend', 'backend', 'data', 'factor', 'monitor'];
const MON_STATE = { ok: 'ok', degraded: 'warn', down: 'error' };     // health → frame tone
const TONE_RANK = { ok: 0, warn: 1, error: 2 };

function worse(a, b) {
  return (TONE_RANK[a] ?? 0) >= (TONE_RANK[b] ?? 0) ? (a || 'ok') : (b || 'ok');
}

// ── single shared EventSource for the whole app (状态栏/标识/总览/拓扑 复用一条连接) ──
let _es = null;
let _refs = 0;
const _state = { frame: null, sseOpen: false, lastFrameAt: 0 };
const _subs = new Set();
function _emit() { _subs.forEach((fn) => fn()); }
function _connect() {
  if (_es || typeof EventSource === 'undefined' || !tokenStore.get()) return;
  try {
    _es = new EventSource(obsStatusStreamUrl());
  } catch { return; }
  _es.addEventListener('status', (e) => {
    try { _state.frame = JSON.parse(e.data); _state.lastFrameAt = Date.now(); _state.sseOpen = true; _emit(); } catch { /* skip */ }
  });
  _es.onopen = () => { _state.sseOpen = true; _emit(); };
  _es.onerror = () => { _state.sseOpen = false; _emit(); };   // 断线 → 回退轮询;EventSource 自动重连
}
function _acquire() { _refs += 1; _connect(); }
function _release() {
  _refs -= 1;
  if (_refs <= 0) { _refs = 0; if (_es) { _es.close(); _es = null; } _state.sseOpen = false; }
}

function useObsFrame() {
  const [, bump] = React.useState(0);
  React.useEffect(() => {
    _acquire();
    const fn = () => bump((n) => n + 1);
    _subs.add(fn);
    const id = setInterval(fn, 5_000);          // freshness watchdog re-render
    return () => { _subs.delete(fn); clearInterval(id); _release(); };
  }, []);
  return _state;
}

// merge one layer from the sentinel frame (truth) + the backend health verdict (rich)
function mergeLayer(key, frame, health, sseLive) {
  const f = sseLive ? (frame?.layers?.[key]) : null;
  const h = health?.layers?.[key];
  const hState = MON_STATE[h?.state] || (h ? 'ok' : undefined);
  let reachable = h?.reachable ?? null;
  // 既无哨兵帧也无后端裁决 ⇒ 该层「未知」(undefined → CompDot 渲染「…」灰), 绝不伪绿
  // (两路皆断时必走此分支, AC-08)。
  let state;
  if (f) {
    reachable = f.reachable ?? reachable;
    // 存活真相优先: 哨兵判 down ⇒ down (优先级最高), 否则取两者更差
    state = f.reachable === false ? 'error' : worse(f.state || 'ok', hState || 'ok');
  } else if (h) {
    state = hState || 'ok';
  } else {
    state = undefined;            // unknown — neither plane reporting
  }
  return {
    state,
    reachable,
    cpu_pct: f?.cpu_pct ?? h?.cpu_pct ?? null,
    mem_pct: f?.mem_pct ?? h?.mem_pct ?? null,
    restart_count: f?.restart_count ?? h?.restart_count ?? null,
    oom_killed: f?.oom_killed ?? h?.oom_killed ?? false,
    health: h || null,                 // backend 明细 (worker / 上次成功 / 崩溃 等)
    self: f?.self,
  };
}

export function useObsStatus() {
  const health = useSystemHealth();               // 第二层: 轮询 /monitor/health
  const { frame, sseOpen, lastFrameAt } = useObsFrame();

  const fresh = frame != null && (Date.now() - lastFrameAt) < STALE_MS;
  const sseLive = sseOpen && fresh;
  const backendOk = !health.isError && !!health.data;
  const source = sseLive ? 'live' : backendOk ? 'delayed' : 'stale';

  const layers = {};
  for (const k of LAYER_KEYS) layers[k] = mergeLayer(k, frame, health.data, sseLive);

  // 整体 = 各层取最差 (两路皆断 ⇒ down, 绝不伪绿)
  let overall = source === 'stale' ? 'error' : 'ok';
  for (const k of LAYER_KEYS) overall = worse(overall, layers[k].state);
  const status = { ok: 'ok', warn: 'degraded', error: 'down' }[overall];

  const infra = (sseLive && frame?.infra) ? frame.infra : (health.data?.infra || {});
  const infraAlerts = sseLive ? (frame?.infra_alerts || []) : [];

  return {
    frame, health: health.data, layers, infra, infraAlerts,
    source, sseLive, backendOk, status,
    firing: health.data?.alerts?.firing || 0,
    alertsSummary: health.data?.alerts || { firing: 0, by_severity: {}, top: [] },
    sentinelOnline: health.data?.sentinel_online ?? (frame != null),
    refetch: health.refetch,
  };
}
