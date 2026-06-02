// 可观测 · 规则 — /observ/rules (可观测层重构方案 §7.3⑧)
// 列出 D1–D12: 规则名/触发源/参数(可编辑)/严重级/最近触发/启停。admin 可编辑,其余只读。
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { TerminalShell } from '../../components/shell';
import { Loading, ErrorState } from '../../components/ui';
import { useAuth } from '../../lib/auth';
import { endpoints } from '../../lib/api';
import { ObsSev, ObsLayerMark, ObsKap } from './shared';

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
      subtitle={`rules · 监控层检测规则与阈值 · ${rules.length} 条 · ${enabledCount} 启用`}
      badge={<span className={`chip ${isAdmin ? 'warn' : 'neutral'}`} style={{ fontSize: 10 }}>{isAdmin ? 'ADMIN · 可编辑' : '只读'}</span>}
      actions={<button className="btn ghost sm" onClick={() => q.refetch()}>↻ 刷新</button>}
    >
      {/* role banner */}
      <div style={{ padding: '7px 14px', background: isAdmin ? 'var(--warn-bg)' : 'var(--surface-2)', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10, fontFamily: 'var(--font-mono)', fontSize: 11, flexShrink: 0 }}>
        {isAdmin ? (
          <>
            <span style={{ color: 'var(--warn)', fontWeight: 600 }}>● 管理员模式</span>
            <span style={{ color: 'var(--fg-2)' }}>阈值输入可编辑 (回车/失焦保存) · 启用开关可切换</span>
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
              <div style={{ display: 'grid', gridTemplateColumns: '170px 90px 1fr 96px 90px 64px 30px', gap: 12, padding: '7px 16px', borderBottom: '1px solid var(--border-strong)', position: 'sticky', top: 0, background: 'var(--surface)', zIndex: 1 }}>
                {['规则名', '触发来源', '当前阈值参数', '严重级', '最近触发', '启用', ''].map((c) => (
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
  return (
    <div style={{ borderBottom: '1px solid var(--border-faint)', opacity: off ? 0.6 : 1 }}>
      <div onClick={() => setOpen((o) => !o)} style={{ display: 'grid', gridTemplateColumns: '170px 90px 1fr 96px 90px 64px 30px', gap: 12, padding: '9px 16px', alignItems: 'center', cursor: 'pointer', background: open ? 'var(--surface-2)' : 'transparent' }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontFamily: 'var(--font-mono)', fontSize: 12.5, color: 'var(--fg)', fontWeight: 500 }}>
          <ObsLayerMark layer={r.layer} /> {r.name}
        </span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-2)' }}>{r.source}</span>
        <span style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          {r.params.length === 0 && <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-mute)' }}>即时 · 无参数</span>}
          {r.params.map((p) => (
            <span key={p.key} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontFamily: 'var(--font-mono)', fontSize: 10.5 }}>
              <span style={{ color: 'var(--fg-3)' }}>{p.label}</span>
              <input defaultValue={p.value} disabled={!isAdmin || busy} onClick={(e) => e.stopPropagation()}
                onKeyDown={(e) => { if (e.key === 'Enter') e.target.blur(); }}
                onBlur={(e) => { if (isAdmin && String(e.target.value) !== String(p.value)) onParam(p.key, e.target.value); }}
                style={{
                  width: String(p.value).length > 3 ? 56 : 40, height: 22, textAlign: 'center', padding: '0 4px',
                  background: isAdmin ? 'var(--surface)' : 'var(--surface-2)', border: '1px solid var(--border-strong)', borderRadius: 3,
                  fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)', cursor: isAdmin ? 'text' : 'not-allowed',
                }} />
            </span>
          ))}
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
              <ObsKap>参数含义</ObsKap>
              <div style={{ marginTop: 6, color: 'var(--fg-2)', lineHeight: 1.55 }}>{r.detail}</div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
