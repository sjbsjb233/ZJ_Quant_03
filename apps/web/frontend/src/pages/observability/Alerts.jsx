// 可观测 · 告警 — /observ/alerts (可观测层重构方案 §7.3②)
// 活跃/历史告警, ack/resolve (editor+), 规则溯源 + 关联日志 (trace embed)。
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { TerminalShell, FilterBar, FInput, FSelect } from '../../components/shell';
import { Loading, ErrorState, Empty } from '../../components/ui';
import { useAuth } from '../../lib/auth';
import { endpoints } from '../../lib/api';
import LogPanel from '../logs/LogPanel';
import { ObsSev, ObsLayerMark, ObsAlertChip, ObsKap, ObsDrill, agoFrom } from './shared';

const STATUS_OPTIONS = [
  { value: 'firing,acked', label: '触发中 + 已确认' },
  { value: 'firing', label: '仅触发中' },
  { value: 'resolved', label: '已解决 (历史)' },
  { value: '', label: '全部' },
];
const SEV_OPTIONS = [
  { value: '', label: '全部' },
  { value: 'critical', label: 'CRITICAL' },
  { value: 'error', label: 'ERROR' },
  { value: 'warn', label: 'WARN' },
  { value: 'info', label: 'INFO' },
];
const LAYER_OPTIONS = [
  { value: '', label: '全部 (5)' },
  { value: 'frontend', label: '前端' }, { value: 'backend', label: '后端' },
  { value: 'data', label: '数据' }, { value: 'factor', label: '因子' }, { value: 'monitor', label: '监控' },
];

export default function Alerts() {
  const nav = useNavigate();
  const qc = useQueryClient();
  const { user } = useAuth();
  const canAct = user && (user.role === 'admin' || user.role === 'editor');

  const [status, setStatus] = useState('firing,acked');
  const [severity, setSeverity] = useState('');
  const [layer, setLayer] = useState('');
  const [target, setTarget] = useState('');
  const [search, setSearch] = useState('');
  const [selId, setSelId] = useState(null);

  const params = {
    ...(status && { status }), ...(severity && { severity }), ...(layer && { layer }),
    ...(target && { target }), ...(search && { search }), page_size: 100,
  };
  const q = useQuery({
    queryKey: ['monitorAlerts', params],
    queryFn: () => endpoints.monitorAlerts(params),
    refetchInterval: 30_000, keepPreviousData: true,
  });
  const items = q.data?.items || [];
  const sel = items.find((a) => a.id === selId) || items[0] || null;

  const ackMut = useMutation({
    mutationFn: (id) => endpoints.ackAlert(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['monitorAlerts'] }),
  });
  const resolveMut = useMutation({
    mutationFn: (id) => endpoints.resolveAlert(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['monitorAlerts'] }),
  });

  const applied = [status, severity, layer, target, search].filter(Boolean).map((k) => ({ k }));

  return (
    <TerminalShell
      module="observ" current="alerts"
      path={['observ/', 'alerts']}
      fkeys={[
        { k: 'F1', l: '总览', onClick: () => nav('/observ/overview') },
        { k: 'F2', l: '确认', onClick: () => canAct && sel?.status === 'firing' && ackMut.mutate(sel.id), active: false },
        { k: 'F3', l: '解决', onClick: () => canAct && sel && sel.status !== 'resolved' && resolveMut.mutate(sel.id) },
        { k: 'F4', l: '规则', onClick: () => nav('/observ/rules') },
        { k: 'F5', l: '刷新', onClick: () => q.refetch() },
      ]}
      title="告警"
      subtitle="alerts · 查看与处理告警 · 去重键 (rule, layer, target) · 自动 resolve"
      badge={<span className="chip err" style={{ fontSize: 10 }}>▲ {items.filter((a) => a.status === 'firing').length} 触发中</span>}
      actions={<button className="btn ghost sm" onClick={() => q.refetch()}>↻ 刷新</button>}
      filterBar={
        <FilterBar applied={applied} onClear={() => { setSeverity(''); setLayer(''); setTarget(''); setSearch(''); }}>
          <FSelect label="状态" value={status} options={STATUS_OPTIONS} onChange={setStatus} width={150} />
          <FSelect label="严重级" value={severity} options={SEV_OPTIONS} onChange={setSeverity} width={120} />
          <FSelect label="层" value={layer} options={LAYER_OPTIONS} onChange={setLayer} width={110} />
          <FInput label="目标" placeholder="容器 / 任务" value={target} onChange={setTarget} width={140} />
          <FInput label="search" placeholder="标题 / 规则" value={search} onChange={setSearch} width={160} />
        </FilterBar>
      }
    >
      <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1.5fr 1fr', minHeight: 0 }}>
        {/* left list */}
        <div style={{ borderRight: '1px solid var(--border)', display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          <div style={{ padding: '6px 14px', background: 'var(--surface-2)', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 8 }}>
            <ObsKap>告警列表</ObsKap>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>· {items.length} 条 · 触发中置顶</span>
          </div>
          <div className="scroll" style={{ flex: 1, overflow: 'auto' }}>
            {q.isLoading ? <Loading /> : q.error ? <ErrorState error={q.error} onRetry={q.refetch} />
              : items.length === 0 ? <Empty label="无匹配告警 🎉" />
                : items.map((a) => <AlertRow key={a.id} a={a} sel={a.id === sel?.id} onClick={() => setSelId(a.id)} />)}
          </div>
        </div>

        {/* right detail */}
        <div style={{ display: 'flex', flexDirection: 'column', minWidth: 0, background: 'var(--surface)' }}>
          <div style={{ padding: '6px 12px', background: 'var(--surface-2)', borderBottom: '1px solid var(--border)', display: 'flex', alignItems: 'center', gap: 10, height: 28 }}>
            <ObsKap>告警详情</ObsKap><span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>· {sel ? `#${sel.id}` : '—'}</span>
          </div>
          <div className="scroll" style={{ flex: 1, overflow: 'auto' }}>
            {sel ? (
              <AlertDetail a={sel} canAct={canAct} nav={nav}
                onAck={() => ackMut.mutate(sel.id)} onResolve={() => resolveMut.mutate(sel.id)}
                busy={ackMut.isPending || resolveMut.isPending} />
            ) : <div style={{ padding: 40, textAlign: 'center', color: 'var(--fg-3)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>← 选择一条告警</div>}
          </div>
        </div>
      </div>
    </TerminalShell>
  );
}

function AlertRow({ a, sel, onClick }) {
  const firing = a.status === 'firing';
  return (
    <div onClick={onClick} style={{
      padding: '9px 14px', borderBottom: '1px solid var(--border-faint)', cursor: 'pointer',
      background: sel ? 'var(--info-bg)' : a.status === 'resolved' ? 'transparent' : firing ? 'color-mix(in oklch, var(--err-bg) 55%, transparent)' : 'transparent',
      borderLeft: sel ? '2px solid var(--info)' : firing ? '2px solid var(--err)' : a.status === 'acked' ? '2px solid var(--warn)' : '2px solid transparent',
      opacity: a.status === 'resolved' ? 0.62 : 1, display: 'flex', flexDirection: 'column', gap: 5,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <ObsSev sev={a.severity} mini />
        <ObsLayerMark layer={a.layer} />
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--fg)', fontWeight: 500, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.title}</span>
        <ObsAlertChip state={a.status} mini />
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>
        <span>{a.target || '—'}</span>
        <span>触发 ×{a.count}</span>
        <span>最近 {agoFrom(a.last_seen)}</span>
        {a.acked_by && <span style={{ color: 'var(--fg-4)' }}>· {a.status === 'resolved' ? '解决' : '确认'} by {a.acked_by}</span>}
      </div>
    </div>
  );
}

function AlertDetail({ a, canAct, nav, onAck, onResolve, busy }) {
  const ctx = Object.entries(a.detail || {}).filter(([, v]) => typeof v !== 'object');
  return (
    <div style={{ padding: '14px 16px', fontFamily: 'var(--font-mono)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 6 }}>
        <ObsSev sev={a.severity} /><ObsLayerMark layer={a.layer} full /><ObsAlertChip state={a.status} />
      </div>
      <div style={{ fontSize: 15, color: 'var(--fg)', fontWeight: 600, marginBottom: 4 }}>{a.title}</div>
      <div style={{ fontSize: 11, color: 'var(--fg-3)' }}>
        触发规则: <span style={{ color: 'var(--info)', cursor: 'pointer' }} onClick={() => nav('/observ/rules')}>{a.rule_id} →</span>
        <span style={{ marginLeft: 12 }}>目标: <span style={{ color: 'var(--fg-2)' }}>{a.target || '—'}</span></span>
      </div>

      {a.acked_by && (
        <div style={{ marginTop: 10, padding: '7px 10px', background: a.status === 'resolved' ? 'var(--ok-bg)' : 'var(--warn-bg)', border: `1px solid ${a.status === 'resolved' ? 'var(--ok)' : 'var(--warn)'}`, borderRadius: 3, fontSize: 11, color: 'var(--fg-2)' }}>
          {a.status === 'resolved' ? '✓ 已解决' : '◑ 已确认'} · by <span style={{ color: 'var(--fg)' }}>{a.acked_by}</span> · {agoFrom(a.acked_at || a.resolved_at)}
        </div>
      )}

      <Section>上下文 · 明细</Section>
      <div style={{ display: 'grid', gridTemplateColumns: '130px 1fr', gap: 4, fontSize: 11 }}>
        {[['触发次数', String(a.count)], ['首次', agoFrom(a.first_seen)], ['最近', agoFrom(a.last_seen)],
          ...ctx.map(([k, v]) => [k, String(v)])].map(([k, v], i) => (
          <React.Fragment key={i}>
            <span style={{ color: 'var(--fg-3)' }}>{k}</span>
            <span style={{ color: 'var(--fg)', wordBreak: 'break-all' }}>{v}</span>
          </React.Fragment>
        ))}
      </div>

      {a.trace_id && (<>
        <Section>关联 trace</Section>
        <div style={{ padding: '8px 10px', background: 'var(--surface-2)', borderRadius: 3, display: 'flex', alignItems: 'center', gap: 8, fontSize: 11 }}>
          <span style={{ color: 'var(--info)' }}>t_{a.trace_id.replace(/^t_/, '').slice(-8)}</span>
          <span style={{ flex: 1 }} />
          <ObsDrill onClick={() => nav(`/observ/trace/${a.trace_id}`)}>Trace 联查</ObsDrill>
        </div>
      </>)}

      <Section>关联日志</Section>
      <div style={{ height: 240 }}>
        <LogPanel embed height={216} traceId={a.trace_id || undefined}
          defaultEvent={a.trace_id ? '' : (a.detail?.event || '')}
          onTrace={(t) => nav(`/observ/trace/${t}`)} />
      </div>

      {/* actions — editor+ */}
      <div style={{ marginTop: 18, paddingTop: 14, borderTop: '1px solid var(--border)' }}>
        <Section style={{ margin: '0 0 8px' }}>操作 · 编辑及以上角色</Section>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {a.status === 'firing' && <button className="btn sm" disabled={!canAct || busy} style={{ background: 'var(--warn)', borderColor: 'var(--warn)', color: 'var(--surface)', opacity: canAct ? 1 : 0.4 }} onClick={onAck}>◑ 确认</button>}
          {a.status !== 'resolved' && <button className="btn sm primary" disabled={!canAct || busy} style={{ opacity: canAct ? 1 : 0.4 }} onClick={onResolve}>✓ 解决</button>}
          <button className="btn ghost sm" onClick={() => navigator.clipboard?.writeText(a.trace_id || '')}>⎘ trace_id</button>
        </div>
        {!canAct && <div style={{ marginTop: 8, fontSize: 10, color: 'var(--fg-4)' }}>· 只读角色: 上述按钮为禁用态</div>}
      </div>
    </div>
  );
}

function Section({ children, style }) {
  return <div style={{ fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', margin: '16px 0 6px', ...style }}>{children}</div>;
}
