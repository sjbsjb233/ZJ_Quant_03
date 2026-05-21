// 数据源详情
import React, { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { TerminalShell, TPanel, Stat } from '../components/shell';
import { Loading, ErrorState, Empty } from '../components/ui';
import { endpoints } from '../lib/api';
import { relTime, absTime } from '../lib/format';
import { useAuth } from '../lib/auth';

const TABLES_8 = [
  'raw.bars_daily', 'raw.bars_minute', 'raw.fundamentals', 'raw.universe',
  'raw.calendar', 'raw.corporate_actions', 'reference.symbols', 'reference.sectors',
];

export default function SourceDetail() {
  const { sourceId } = useParams();
  const nav = useNavigate();
  const { hasRole } = useAuth();
  const qc = useQueryClient();
  const [tab, setTab] = useState('overview');

  const sourceQ = useQuery({
    queryKey: ['source', sourceId],
    queryFn: () => endpoints.source(sourceId),
    enabled: !!sourceId,
  });

  const deactivateMut = useMutation({
    mutationFn: () => endpoints.deactivateSource(sourceId),
    onSuccess: () => { qc.invalidateQueries(['sources']); qc.invalidateQueries(['source', sourceId]); },
  });

  const s = sourceQ.data;

  const tabs = ['overview', 'tables', 'config'];

  if (sourceQ.isLoading) {
    return (
      <TerminalShell module="data" current="sources" path={['data_mgmt/', 'sources/', sourceId]} title={sourceId} subtitle="加载中…">
        <Loading />
      </TerminalShell>
    );
  }

  if (sourceQ.error || !s) {
    return (
      <TerminalShell module="data" current="sources" path={['data_mgmt/', 'sources/', sourceId]} title={sourceId} subtitle="错误">
        <ErrorState error={sourceQ.error} onRetry={sourceQ.refetch} />
      </TerminalShell>
    );
  }

  const coveringTables = s.field_mapping ? Object.keys(s.field_mapping).filter(t => {
    const tmap = s.field_mapping[t];
    return tmap && Object.keys(tmap).length > 0;
  }) : [];

  const tone = s.is_active ? 'ok' : 'neutral';

  return (
    <TerminalShell
      module="data" current="sources"
      path={['data_mgmt/', 'sources/', s.source_id]}
      fkeys={[
        { k: 'F1', l: '编辑', onClick: () => nav('/data/sources') },
        { k: 'F2', l: '禁用', onClick: () => hasRole('editor') && deactivateMut.mutate() },
        { k: 'ESC', l: '返回', onClick: () => nav('/data/sources') },
      ]}
      title={s.source_id}
      subtitle={`${s.display_name} · priority ${s.priority} · ${coveringTables.length} tables · ${s.category}`}
      badge={<span className={`chip ${tone}`} style={{ fontSize: 10 }}>{s.is_active ? 'ACTIVE' : 'INACTIVE'}</span>}
      actions={
        <>
          <button className="btn ghost sm" onClick={() => nav('/data/sources')}>← 数据源列表</button>
          {hasRole('editor') && (
            <button className="btn ghost sm" style={{ color: 'var(--warn)' }}
              onClick={() => deactivateMut.mutate()}
              disabled={deactivateMut.isPending}>
              {deactivateMut.isPending ? '处理中…' : '禁用'}
            </button>
          )}
        </>
      }
    >
      {/* Tab bar */}
      <div style={{
        height: 30, padding: '0 14px',
        background: 'var(--surface-2)',
        borderBottom: '1px solid var(--border)',
        display: 'flex', alignItems: 'flex-end',
        flexShrink: 0,
      }}>
        {tabs.map(t => (
          <button key={t} onClick={() => setTab(t)} style={{
            height: 30, padding: '0 14px',
            background: tab === t ? 'var(--surface)' : 'transparent',
            border: 'none',
            borderTop: tab === t ? '1px solid var(--fg)' : '1px solid transparent',
            borderLeft: tab === t ? '1px solid var(--border)' : 'none',
            borderRight: tab === t ? '1px solid var(--border)' : 'none',
            fontFamily: 'var(--font-mono)', fontSize: 11,
            color: tab === t ? 'var(--fg)' : 'var(--fg-3)',
            cursor: 'pointer', marginBottom: -1,
          }}>{t}</button>
        ))}
      </div>

      {/* Overview */}
      {tab === 'overview' && (
        <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1.2fr 1fr', minHeight: 0, overflow: 'auto' }}>
          {/* Connection panel */}
          <TPanel title="CONNECTION" meta="配置详情">
            <div style={{ padding: '8px 14px', fontFamily: 'var(--font-mono)', fontSize: 11.5 }}>
              {[
                ['source_id', s.source_id],
                ['display_name', s.display_name],
                ['category', s.category],
                ['endpoint', s.endpoint || '—'],
                ['auth_method', s.auth_method || '—'],
                ['rate_limit', s.rate_limit_per_min ? `${s.rate_limit_per_min} / min` : '—'],
                ['priority', String(s.priority)],
                ['is_active', s.is_active ? 'true' : 'false'],
                ['description', s.description || '—'],
                ['updated_at', s.updated_at ? absTime(s.updated_at) : '—'],
              ].map(([k, v]) => (
                <div key={k} style={{ display: 'grid', gridTemplateColumns: '130px 1fr', padding: '5px 0', borderBottom: '1px solid var(--border-faint)' }}>
                  <span style={{ color: 'var(--fg-3)' }}>{k}</span>
                  <span style={{ color: k === 'is_active' ? (s.is_active ? 'var(--ok)' : 'var(--warn)') : 'var(--fg)' }}>
                    {k === 'auth_method' && v !== '—' ? (
                      <span title={v}>{v} <span style={{ color: 'var(--fg-3)' }}>(token: **********)</span></span>
                    ) : v}
                  </span>
                </div>
              ))}
            </div>
          </TPanel>

          {/* Tables Covered panel */}
          <TPanel title="TABLES COVERED" meta={`${coveringTables.length} tables`} last>
            {coveringTables.length === 0 ? (
              <Empty label="暂无 field_mapping 配置" />
            ) : (
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                <thead>
                  <tr style={{ borderBottom: '1px solid var(--border)' }}>
                    {['table', 'mapped fields', 'primary?'].map(c => (
                      <th key={c} style={{ textAlign: 'left', padding: '5px 10px', fontSize: 9.5, color: 'var(--fg-3)', fontWeight: 500, fontFamily: 'var(--font-mono)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {coveringTables.map((t, i) => {
                    const entries = Object.entries(s.field_mapping[t] || {});
                    return (
                      <tr key={i} style={{ borderBottom: '1px solid var(--border-faint)', height: 28 }}>
                        <td style={{ padding: '0 10px', fontFamily: 'var(--font-mono)', color: 'var(--fg)', fontSize: 11 }}>{t}</td>
                        <td style={{ padding: '0 10px', fontFamily: 'var(--font-mono)', color: 'var(--fg-2)' }}>{entries.length}</td>
                        <td style={{ padding: '0 10px', fontFamily: 'var(--font-mono)', color: s.priority <= 2 ? 'var(--ok)' : 'var(--fg-3)' }}>
                          {s.priority <= 2 ? '✓ yes' : 'no'}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </TPanel>
        </div>
      )}

      {/* Tables tab */}
      {tab === 'tables' && (
        <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
          {coveringTables.length === 0 ? (
            <Empty label="此数据源无 field_mapping 配置" hint="请在编辑页面配置 Field Mapping" />
          ) : (
            <div>
              {coveringTables.map(tbl => {
                const entries = Object.entries(s.field_mapping[tbl] || {});
                return (
                  <div key={tbl}>
                    <div style={{ padding: '8px 14px', background: 'var(--surface-2)', borderBottom: '1px solid var(--border)', fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 500, color: 'var(--fg)' }}>
                      {tbl} <span style={{ color: 'var(--fg-3)', fontWeight: 400, fontSize: 10 }}>({entries.length} mappings)</span>
                    </div>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
                      <thead>
                        <tr style={{ borderBottom: '1px solid var(--border-faint)' }}>
                          <th style={{ textAlign: 'left', padding: '4px 14px', fontSize: 9.5, color: 'var(--fg-3)', fontWeight: 500, fontFamily: 'var(--font-mono)', textTransform: 'uppercase' }}>源字段</th>
                          <th style={{ textAlign: 'left', padding: '4px 14px', fontSize: 9.5, color: 'var(--fg-3)', fontWeight: 500, fontFamily: 'var(--font-mono)', textTransform: 'uppercase' }}>→ 标准字段</th>
                        </tr>
                      </thead>
                      <tbody>
                        {entries.map(([src, std], i) => (
                          <tr key={i} style={{ borderBottom: '1px solid var(--border-faint)', height: 26 }}>
                            <td style={{ padding: '0 14px', fontFamily: 'var(--font-mono)', color: 'var(--fg-2)' }}>{src}</td>
                            <td style={{ padding: '0 14px', fontFamily: 'var(--font-mono)', color: 'var(--fg)' }}>{std}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Config tab — raw JSON */}
      {tab === 'config' && (
        <div style={{ flex: 1, overflow: 'auto', minHeight: 0, padding: 16 }}>
          <pre style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 4, padding: 14, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {JSON.stringify(s, null, 2)}
          </pre>
        </div>
      )}
    </TerminalShell>
  );
}
