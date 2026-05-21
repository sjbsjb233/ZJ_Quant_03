// 表浏览 · Table picker page (V3 Terminal)
import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { TerminalShell } from '../components/shell';
import { FInput, FSelect, Stat } from '../components/shell';
import { Loading, ErrorState } from '../components/ui';
import { endpoints } from '../lib/api';
import { fmtCompact, fmtBytes, relTime, freshnessMeta, toneColor } from '../lib/format';

export default function Tables() {
  const nav = useNavigate();
  const [search, setSearch] = useState('');
  const [kindFilter, setKindFilter] = useState('all');
  const [freshnessFilter, setFreshnessFilter] = useState('any');

  const tablesQ = useQuery({ queryKey: ['tables'], queryFn: endpoints.tables });
  const tables = tablesQ.data?.items || [];

  // Group raw before reference
  const filtered = [...tables]
    .filter((t) => {
      if (search && !t.name.toLowerCase().includes(search.toLowerCase())) return false;
      if (kindFilter !== 'all' && t.kind !== kindFilter) return false;
      if (freshnessFilter !== 'any' && t.freshness !== freshnessFilter) return false;
      return true;
    })
    .sort((a, b) => {
      if (a.kind === b.kind) return a.name.localeCompare(b.name);
      return a.kind === 'raw' ? -1 : 1;
    });

  const fkeys = [
    { k: 'F1', l: '搜表' },
    { k: 'F2', l: '收藏' },
    { k: 'F3', l: '导出 schema' },
  ];

  return (
    <TerminalShell
      module="data"
      current="tables"
      path={['data_mgmt/', 'tables']}
      fkeys={fkeys}
      title="表浏览"
      subtitle={`tables · ${tables.length} managed · select a table to browse its rows`}
      filterBar={
        <div style={{
          padding: '6px 14px',
          background: 'var(--surface)',
          borderBottom: '1px solid var(--border)',
          display: 'flex', alignItems: 'center', gap: 8,
          flexWrap: 'wrap',
          fontFamily: 'var(--font-mono)', fontSize: 11,
          flexShrink: 0,
        }}>
          <span style={{ fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>filters</span>
          <span style={{ color: 'var(--fg-mute)' }}>│</span>
          <FInput label="search" placeholder="table name" value={search} onChange={setSearch} width={180} />
          <FSelect
            label="schema"
            value={kindFilter}
            options={[
              { value: 'all', label: 'all (3)' },
              { value: 'raw', label: 'raw' },
              { value: 'reference', label: 'reference' },
            ]}
            onChange={setKindFilter}
            width={120}
          />
          <FSelect
            label="freshness"
            value={freshnessFilter}
            options={[
              { value: 'any', label: 'any' },
              { value: 'ok', label: 'FRESH' },
              { value: 'warn', label: 'STALE' },
              { value: 'critical', label: 'CRITICAL' },
              { value: 'info', label: 'STATIC' },
            ]}
            onChange={setFreshnessFilter}
            width={100}
          />
        </div>
      }
    >
      {tablesQ.isLoading ? (
        <Loading />
      ) : tablesQ.error ? (
        <ErrorState error={tablesQ.error} onRetry={tablesQ.refetch} />
      ) : (
        <div style={{
          flex: 1,
          display: 'grid',
          gridTemplateColumns: 'repeat(4, 1fr)',
          gridAutoRows: 'min-content',
          minHeight: 0,
          overflow: 'auto',
        }}>
          {filtered.map((t, i) => {
            const fm = freshnessMeta(t.freshness);
            const tc = toneColor(fm.tone);
            return (
              <div
                key={t.name}
                style={{
                  padding: 14,
                  borderRight: (i + 1) % 4 === 0 ? 'none' : '1px solid var(--border)',
                  borderBottom: '1px solid var(--border)',
                  background: 'var(--surface)',
                  minHeight: 180,
                  display: 'flex', flexDirection: 'column', gap: 8,
                  cursor: 'pointer',
                }}
                onClick={() => nav(`/data/tables/${encodeURIComponent(t.name)}`)}
                onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--hover)')}
                onMouseLeave={(e) => (e.currentTarget.style.background = 'var(--surface)')}
              >
                {/* header */}
                <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 500, color: 'var(--fg)' }}>{t.name}</div>
                    <div style={{ fontSize: 10.5, color: 'var(--fg-3)', marginTop: 2 }}>{t.note}</div>
                    <div style={{ fontSize: 10, color: 'var(--fg-4)', marginTop: 2, fontFamily: 'var(--font-mono)' }}>{t.kind}</div>
                  </div>
                  <span style={{
                    fontFamily: 'var(--font-mono)', fontSize: 9,
                    color: tc, letterSpacing: '0.05em',
                    border: `1px solid ${tc}`, padding: '0 5px', borderRadius: 2,
                    whiteSpace: 'nowrap',
                  }}>
                    {fm.sym} {fm.label}
                  </span>
                </div>

                <div style={{ flex: 1 }} />

                {/* metrics */}
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6 }}>
                  <Stat label="rows" value={fmtCompact(t.row_count)} />
                  <Stat label="size" value={fmtBytes(t.disk_bytes)} />
                  <Stat label="files" value={fmtCompact(t.file_count)} />
                </div>

                {/* footer */}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 4 }}>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>
                    updated <span style={{ color: 'var(--fg-2)' }}>{relTime(t.latest_batch_at)}</span>
                  </span>
                  <button
                    className="btn ghost sm"
                    style={{ height: 22, fontSize: 10.5 }}
                    onClick={(e) => { e.stopPropagation(); nav(`/data/tables/${encodeURIComponent(t.name)}`); }}
                  >
                    浏览 →
                  </button>
                </div>
              </div>
            );
          })}
          {filtered.length === 0 && (
            <div style={{ gridColumn: '1 / -1', padding: 60, textAlign: 'center', color: 'var(--fg-3)', fontFamily: 'var(--font-mono)' }}>
              ∅ 未找到匹配的表
            </div>
          )}
        </div>
      )}
    </TerminalShell>
  );
}
