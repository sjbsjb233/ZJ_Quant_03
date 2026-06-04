// 可观测 · 规则 — /observ/rules (可观测层重构方案 §7A.5)
// 列出 L0 + D1–D15: 规则名/检测方/触发源/参数/严重级/最近触发/启停。admin 可编辑后端规则
// 阈值;哨兵规则 (detector_owner=sentinel) 阈值真相源是哨兵 env → 标「哨兵配置」并只读。
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { TerminalShell } from '../../components/shell';
import { Loading, ErrorState } from '../../components/ui';
import { useAuth } from '../../lib/auth';
import { endpoints } from '../../lib/api';
import { ObsSev, ObsLayerMark, ObsKap, ObsDetector, ObsResMark } from './shared';

const GRID = '168px 78px 80px 1fr 84px 82px 58px 26px';
const RES_OF = { 'D13.disk': 'disk', 'D14.cpu_saturation': 'cpu', 'D15.core_dump': 'dump' };

export default function Rules() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const { user } = useAuth();
  const isAdmin = user?.role === 'admin';
  const q = useQuery({ queryKey: ['monitorRules'], queryFn: endpoints.monitorRules, refetchInterval: 60_000 });
  const mut = useMutation({
    mutationFn: ({ id, body }) => endpoints.updateRule(id, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['monitorRules'] }),
  });
  const rules = q.data?.items || [];
  const enabledCount = rules.filter((r) => r.enabled).length;
  const sentinelCount = rules.filter((r) => r.detector_owner === 'sentinel').length;

  return (
    <TerminalShell
      module="observ" current="rules"
      path={['observ/', 'rules']}
      fkeys={[
        { k: 'F1', l: '总览', onClick: () => nav('/observ/overview') },
        { k: 'F2', l: '告警', onClick: () => nav('/observ/alerts') },
        { k: 'F5', l: '刷新', onClick: () => q.refetch() },
      ]}
      title="规则"
      subtitle={`rules · L0 + D1–D15 · ${rules.length} 条 · ${enabledCount} 启用 · ${sentinelCount} 哨兵检测`}
      badge={<span className={`chip ${isAdmin ? 'warn' : 'neutral'}`} style={{ fontSize: 10 }}>{isAdmin ? 'ADMIN · 可编辑' : '只读'}</span>}
      actions={<button className="btn ghost sm" onClick={() => q.refetch()}>↻ 刷新</button>}
    >
      {/* role banner */}
      <div style={{ padding: '7px 14px', background: isAdmin ? 'var(--warn-bg)' : 'var(--surface-2)', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10, fontFamily: 'var(--font-mono)', fontSize: 11, flexShrink: 0 }}>
        {isAdmin ? (
          <>
            <span style={{ color: 'var(--warn)', fontWeight: 600 }}>● 管理员模式</span>
            <span style={{ color: 'var(--fg-2)' }}>后端规则阈值可编辑 (回车/失焦保存) · 启用开关可切换</span>
            <span style={{ color: 'var(--fg-mute)' }}>│</span>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}><ObsDetector kind="external" mini /><span style={{ color: 'var(--fg-3)' }}>阈值来自哨兵 env · 只读</span></span>
          </>
        ) : (
          <span style={{ color: 'var(--fg-2)' }}>非管理员角色: 所有输入与开关呈禁用态（只读）</span>
        )}
        <span style={{ flex: 1 }} />
        <span style={{ color: 'var(--fg-4)' }}>变更将记录到审计日志</span>
      </div>

      {q.isLoading ? <Loading label="加载规则…" />
        : q.error ? <ErrorState error={q.error} onRetry={q.refetch} />
          : (
            <div className="scroll" style={{ flex: 1, overflow: 'auto', background: 'var(--surface)' }}>
              <div style={{ display: 'grid', gridTemplateColumns: GRID, gap: 10, padding: '7px 16px', borderBottom: '1px solid var(--border-strong)', position: 'sticky', top: 0, background: 'var(--surface)', zIndex: 1 }}>
                {['规则名', '检测方', '触发来源', '当前阈值参数', '严重级', '最近触发', '启用', ''].map((c) => (
                  <span key={c} style={{ fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{c}</span>
                ))}
              </div>
              {rules.map((r) => (
                <RuleRow key={r.id} r={r} isAdmin={isAdmin} busy={mut.isPending}
                  onParam={(key, value) => mut.mutate({ id: r.id, body: { params: { [key]: value } } })}
                  onToggle={() => mut.mutate({ id: r.id, body: { enabled: !r.enabled } })} />
              ))}
            </div>
          )}
    </TerminalShell>
  );
}

function RuleRow({ r, isAdmin, onParam, onToggle, busy }) {
  const [open, setOpen] = useState(false);
  const off = !r.enabled;
  const sentinel = r.detector_owner === 'sentinel';
  const res = RES_OF[r.id];
  // 哨兵规则的阈值真相源是哨兵 env (§7A.5) → 参数输入只读 (下行通道为增强项 §14)。
  const editable = isAdmin && !sentinel;
  return (
    <div style={{ borderBottom: '1px solid var(--border-faint)', opacity: off ? 0.6 : 1 }}>
      <div onClick={() => setOpen((o) => !o)} style={{ display: 'grid', gridTemplateColumns: GRID, gap: 10, padding: '9px 16px', alignItems: 'center', cursor: 'pointer', background: open ? 'var(--surface-2)' : 'transparent' }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--fg)', fontWeight: 500 }}>
          <ObsLayerMark layer={r.layer} />
          {res && <ObsResMark res={res} tone={r.severity === 'warn' ? 'warn' : 'err'} />}
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</span>
        </span>
        <span><ObsDetector kind={sentinel ? 'external' : 'internal'} mini /></span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-2)' }}>{r.source}</span>
        <span style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          {r.params.length === 0 && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-mute)' }}>即时 · 无参数</span>}
          {r.params.map((p) => (
            <span key={p.key} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: 'var(--font-mono)', fontSize: 10.5 }}>
              <span style={{ color: 'var(--fg-3)' }}>{p.label}</span>
              <input defaultValue={p.value} disabled={!editable || busy} onClick={(e) => e.stopPropagation()}
                onKeyDown={(e) => { if (e.key === 'Enter') e.target.blur(); }}
                onBlur={(e) => { if (editable && String(e.target.value) !== String(p.value)) onParam(p.key, e.target.value); }}
                style={{
                  width: String(p.value).length > 3 ? 56 : 40, height: 22, textAlign: 'center', padding: '0 4px',
                  background: editable ? 'var(--surface)' : 'var(--surface-2)', border: '1px solid var(--border-strong)', borderRadius: 3,
                  fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)', cursor: editable ? 'text' : 'not-allowed',
                }} />
            </span>
          ))}
          {sentinel && r.params.length > 0 && (
            <span title="阈值真相源是哨兵 env · 只读 (下行覆盖为增强项)" className="chip" style={{ fontSize: 9, height: 14, background: 'var(--info-bg)', color: 'var(--info)' }}>哨兵配置</span>
          )}
        </span>
        <span><ObsSev sev={r.severity} mini /></span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: r.fired_24h > 10 ? 'var(--err)' : r.fired_24h > 0 ? 'var(--warn)' : 'var(--fg-3)', fontWeight: r.fired_24h > 0 ? 600 : 400, fontVariantNumeric: 'tabular-nums' }}>{r.fired_24h} 次</span>
        <span onClick={(e) => { e.stopPropagation(); if (isAdmin && !busy) onToggle(); }}>
          <span style={{
            display: 'inline-flex', alignItems: 'center', width: 32, height: 18, borderRadius: 9, padding: 2,
            cursor: isAdmin ? 'pointer' : 'not-allowed',
            background: r.enabled ? 'var(--ok)' : 'var(--surface-3)', justifyContent: r.enabled ? 'flex-end' : 'flex-start',
          }}>
            <span style={{ width: 14, height: 14, borderRadius: '50%', background: 'var(--surface)' }} />
          </span>
        </span>
        <span style={{ color: 'var(--fg-3)', fontSize: 11, textAlign: 'center' }}>{open ? '▾' : '▸'}</span>
      </div>
      {open && (
        <div style={{ padding: '4px 16px 14px 16px', background: 'var(--surface-2)' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, fontFamily: 'var(--font-mono)', fontSize: 11.5 }}>
            <div style={{ padding: '10px 12px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 4 }}>
              <ObsKap>抓的是什么故障</ObsKap>
              <div style={{ marginTop: 6, color: 'var(--fg-2)', lineHeight: 1.55 }}>{r.catches}</div>
            </div>
            <div style={{ padding: '10px 12px', background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 4 }}>
              <ObsKap>参数含义 · 检测方</ObsKap>
              <div style={{ marginTop: 6, color: 'var(--fg-2)', lineHeight: 1.55 }}>{r.detail}</div>
              <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 7 }}>
                <ObsDetector kind={sentinel ? 'external' : 'internal'} />
                <span style={{ color: 'var(--fg-3)', fontSize: 10.5 }}>{sentinel ? '哨兵 (obs-sentinel) 直采 · 绕开 backend · 阈值来自哨兵 env' : '后端评估器 · 日志派生 · 阈值可在此编辑'}</span>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
