// 数据管理 · 总览 (V3 Terminal): KPI ticker + health table + hotspots + sources + activity.
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { TerminalShell, TPanel } from '../components/shell';
import { DataTable, Empty, ErrorState, Loading, StatusGlyph } from '../components/ui';
import { endpoints } from '../lib/api';
import { fmtBytes, fmtCompact, fmtPct, freshnessMeta, relTime, statusMeta, toneColor } from '../lib/format';

export default function Dashboard() {
  const nav = useNavigate();
  const summary = useQuery({ queryKey: ['dashboard'], queryFn: endpoints.dashboardSummary, refetchInterval: 60000 });
  const sources = useQuery({ queryKey: ['sources'], queryFn: () => endpoints.sources() });
  const quality = useQuery({ queryKey: ['quality-by-table'], queryFn: endpoints.qualityByTable });

  const fkeys = [
    { k: 'F1', l: '台账', onClick: () => nav('/data/batches') },
    { k: 'F2', l: '表浏览', onClick: () => nav('/data/tables') },
    { k: 'F3', l: '字段', onClick: () => nav('/data/fields') },
    { k: 'F4', l: '质量', onClick: () => nav('/data/quality') },
    { k: 'F5', l: '维护', onClick: () => nav('/data/maintenance') },
  ];

  const d = summary.data;
  const qTotal = (quality.data?.items || []).reduce((a, x) => a + x.total, 0);

  return (
    <TerminalShell module="data" current="overview" path={['data_mgmt/', 'overview']} fkeys={fkeys}
      titleBar={false}
      statusMiddle={<span>last sync: {d ? relTime(d.as_of) : '—'}</span>}>
      {summary.isLoading ? <Loading /> : summary.error ? <ErrorState error={summary.error} onRetry={summary.refetch} /> : (
        <>
          <Ticker kpis={d.kpis} qTotal={qTotal} />
          <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1.45fr 1fr 1.1fr', minHeight: 0 }}>
            <TPanel title="DATA ASSETS · HEALTH" meta={`${d.tables.length} tables`}>
              <HealthTable tables={d.tables} onOpen={(t) => nav(`/data/tables/${t}`)} />
            </TPanel>

            <div style={{ display: 'flex', flexDirection: 'column', borderRight: '1px solid var(--border)', minHeight: 0 }}>
              <TPanel title="GOVERNANCE · HOTSPOTS" meta={`${d.hotspots.length} items`} noBorderRight style={{ flex: '0 0 auto' }}>
                <Hotspots items={d.hotspots} nav={nav} />
              </TPanel>
              <TPanel title="SOURCES · PRIORITY" meta={`${(sources.data?.items || []).filter((s) => s.is_active).length} active`} noBorderRight style={{ flex: 1, minHeight: 0 }}>
                <SourcesMini items={sources.data?.items || []} nav={nav} />
              </TPanel>
            </div>

            <TPanel title="RECENT ACTIVITY" meta="last batches" noBorderRight last>
              <Activity batches={d.recent_batches} onOpen={(id) => nav(`/data/batches/${id}`)} />
            </TPanel>
          </div>
        </>
      )}
    </TerminalShell>
  );
}

function Ticker({ kpis, qTotal }) {
  const cells = [
    { label: 'BATCHES.TODAY', value: kpis.batches_today.total, sub: `${kpis.batches_today.success} ok` },
    { label: 'SUCCESS', value: kpis.batches_today.success, tone: 'ok' },
    { label: 'FAILED', value: kpis.batches_today.failed, tone: kpis.batches_today.failed ? 'err' : undefined },
    { label: 'RUNNING', value: kpis.batches_today.running, tone: 'info', pulse: kpis.batches_today.running > 0 },
    { label: 'SUCCESS.7D', value: fmtPct(kpis.success_rate_7d.value), tone: kpis.success_rate_7d.value < kpis.success_rate_7d.baseline ? 'warn' : 'ok' },
    { label: 'STALE.EXTRAS', value: kpis.stale_extras.count, tone: kpis.stale_extras.count ? 'warn' : undefined },
    { label: 'VACUUMABLE', value: fmtBytes(kpis.vacuumable.bytes), sub: `${kpis.vacuumable.files} files` },
    { label: 'Q.ISSUES', value: qTotal, tone: qTotal ? 'warn' : undefined },
  ];
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(8, 1fr)', background: 'var(--surface)', borderBottom: '1px solid var(--border-strong)', height: 64, flexShrink: 0 }}>
      {cells.map((c, i) => (
        <div key={i} style={{ padding: '8px 12px', borderRight: i < 7 ? '1px solid var(--border-faint)' : 'none', display: 'flex', flexDirection: 'column', justifyContent: 'space-between' }}>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.04em', display: 'flex', alignItems: 'center', gap: 4 }}>
            {c.pulse && <span className="dot running" style={{ width: 5, height: 5 }} />}{c.label}
          </div>
          <div style={{ fontFamily: 'var(--font-mono)', fontVariantNumeric: 'tabular-nums', fontSize: 22, fontWeight: 500, color: toneColor(c.tone), lineHeight: 1 }}>{c.value}</div>
          {c.sub && <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>{c.sub}</div>}
        </div>
      ))}
    </div>
  );
}

function HealthTable({ tables, onOpen }) {
  const cols = [
    { key: 'name', label: 'table', mono: true, color: 'var(--fg)', render: (v) => <span style={{ fontWeight: 500 }}>{v}</span> },
    { key: 'row_count', label: 'rows', align: 'right', mono: true, render: (v) => fmtCompact(v) },
    { key: 'disk_bytes', label: 'size', align: 'right', mono: true, render: (v) => fmtBytes(v) },
    { key: 'freshness', label: 'fresh', align: 'center', render: (v) => { const m = freshnessMeta(v); return <span style={{ color: toneColor(m.tone), fontFamily: 'var(--font-mono)' }}>{m.sym}</span>; } },
    { key: 'latest_batch', label: 'last batch', mono: true, render: (v) => v ? relTime(v.started_at) : '—' },
    { key: 'src', label: 'src', mono: true, fontSize: 10.5, render: (_v, r) => r.latest_batch?.source || '—' },
  ];
  return <DataTable columns={cols} rows={tables} rowKey={(r) => r.name} onRowClick={(r) => onOpen(r.name)} height={26} />;
}

function Hotspots({ items, nav }) {
  const target = { stale_running: '/data/batches', stale_extras: '/data/fields', vacuumable: '/data/maintenance' };
  return (
    <div>
      {items.map((h, i) => (
        <div key={i} onClick={() => nav(target[h.type] || '/data/dashboard')} style={{ padding: '8px 14px', borderBottom: '1px solid var(--border-faint)', fontSize: 11.5, cursor: 'pointer' }}
          onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--hover)')} onMouseLeave={(e) => (e.currentTarget.style.background = '')}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 3 }}>
            <span className={`chip ${h.type === 'vacuumable' ? 'info' : 'warn'}`} style={{ fontSize: 9.5 }}>{h.type.replace('_', '·').toUpperCase()}</span>
            <span className="mono" style={{ color: 'var(--fg)', fontWeight: 600 }}>{h.count}</span>
            <div style={{ flex: 1 }} />
            <span style={{ color: 'var(--info)', fontSize: 10 }}>查看 →</span>
          </div>
          <div style={{ fontSize: 10.5, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)' }}>{h.detail}</div>
        </div>
      ))}
    </div>
  );
}

function SourcesMini({ items, nav }) {
  const cols = [
    { key: 'priority', label: '#', align: 'center', mono: true, color: 'var(--fg)', render: (v) => <b>{v}</b> },
    { key: 'source_id', label: 'source', mono: true, color: 'var(--fg)' },
    { key: 'category', label: 'kind', mono: true, fontSize: 10.5, color: 'var(--fg-3)' },
    { key: 'rate_limit_per_min', label: 'rate', mono: true, render: (v) => (v ? `${v}/min` : '—') },
    { key: 'is_active', label: 'on', align: 'center', render: (v) => <span style={{ color: v ? 'var(--ok)' : 'var(--fg-4)', fontFamily: 'var(--font-mono)' }}>{v ? '●' : '○'}</span> },
  ];
  const sorted = [...items].sort((a, b) => b.priority - a.priority);
  return <DataTable columns={cols} rows={sorted} rowKey={(r) => r.source_id} onRowClick={(r) => nav(`/data/sources/${r.source_id}`)} height={26} />;
}

function Activity({ batches, onOpen }) {
  if (!batches?.length) return <Empty label="暂无近期批次" />;
  return (
    <div>
      {batches.map((b) => {
        const m = statusMeta(b.status);
        return (
          <div key={b.batch_id} onClick={() => onOpen(b.batch_id)} style={{ padding: '8px 14px', borderBottom: '1px solid var(--border-faint)', fontFamily: 'var(--font-mono)', fontSize: 11, cursor: 'pointer', display: 'grid', gridTemplateColumns: '14px 1fr auto', gap: 10, alignItems: 'baseline' }}
            onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--hover)')} onMouseLeave={(e) => (e.currentTarget.style.background = '')}>
            <StatusGlyph status={b.status} />
            <div style={{ minWidth: 0 }}>
              <div><span style={{ fontWeight: 500, color: 'var(--fg)' }}>{b.target_table}</span><span style={{ color: 'var(--fg-3)' }}> ← {b.source}</span></div>
              <div style={{ color: 'var(--fg-3)', fontSize: 10, marginTop: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{b.batch_id.slice(0, 18)}… · {b.summary}</div>
            </div>
            <span style={{ color: 'var(--fg-3)', fontSize: 10 }}>{relTime(b.started_at)}</span>
          </div>
        );
      })}
    </div>
  );
}
