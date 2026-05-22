// 跨源对比
import React, { useState } from 'react';
import { useQuery, useMutation } from '@tanstack/react-query';
import { TerminalShell, FilterBar, FSelect, FInput, Stat, KpiStrip } from '../components/shell';
import { DataTable, ErrorState, Loading, Empty } from '../components/ui';
import { endpoints } from '../lib/api';

const TABLES_8 = [
  'raw.bars_daily', 'raw.bars_minute', 'raw.fundamentals', 'raw.universe',
  'raw.calendar', 'raw.corporate_actions', 'reference.symbols', 'reference.sectors',
];

export default function SourcesDiff() {
  const [table, setTable] = useState('raw.bars_daily');
  const [sourceA, setSourceA] = useState('');
  const [sourceB, setSourceB] = useState('');
  const [start, setStart] = useState('');
  const [end, setEnd] = useState('');
  const [sample, setSample] = useState('1000');
  const [diffTab, setDiffTab] = useState('only_a');
  const [result, setResult] = useState(null);

  const sourcesQ = useQuery({
    queryKey: ['sources', true],
    queryFn: () => endpoints.sources(true),
    staleTime: 60000,
  });
  const activeItems = sourcesQ.data?.items || [];
  const sourceOpts = activeItems.map(s => ({ value: s.source_id, label: `${s.source_id} (${s.display_name})` }));
  const tableOpts = TABLES_8.map(t => ({ value: t, label: t }));

  const diffMut = useMutation({
    mutationFn: (body) => endpoints.diffSources(body),
    onSuccess: (data) => { setResult(data); setDiffTab('only_a'); },
  });

  const handleRun = () => {
    if (!sourceA || !sourceB) return;
    diffMut.mutate({
      table,
      source_a: sourceA,
      source_b: sourceB,
      start: start || undefined,
      end: end || undefined,
      sample: parseInt(sample) || 1000,
    });
  };

  const canRun = sourceA && sourceB && sourceA !== sourceB;

  // Flatten mismatches for the table: one row per {key, column, a, b}
  const mismatchRows = (result?.mismatches || []).flatMap(m => {
    return Object.entries(m.diffs || {}).map(([col, { a, b }]) => ({
      _key: JSON.stringify(m.key) + col,
      key: Array.isArray(m.key) ? m.key.join(' / ') : String(m.key),
      col,
      a: String(a ?? '—'),
      b: String(b ?? '—'),
    }));
  });

  const onlyARows = (result?.only_in_a || []).map((keys, i) => ({
    _key: i,
    key: Array.isArray(keys) ? keys.join(' / ') : String(keys),
  }));
  const onlyBRows = (result?.only_in_b || []).map((keys, i) => ({
    _key: i,
    key: Array.isArray(keys) ? keys.join(' / ') : String(keys),
  }));

  const diffTabs = [
    { k: 'only_a', l: `只在 A (${result?.n_only_in_a ?? 0})` },
    { k: 'only_b', l: `只在 B (${result?.n_only_in_b ?? 0})` },
    { k: 'mismatch', l: `值不一致 (${result?.n_mismatch ?? 0})` },
  ];

  return (
    <TerminalShell
      module="data" current="sources"
      path={['data_mgmt/', 'sources/', 'diff']}
      fkeys={[
        { k: 'F5', l: '运行', onClick: canRun ? handleRun : undefined },
      ]}
      title="跨源对比"
      subtitle="cross-source diff · 选择两个源比较指定表的数据差异"
      actions={
        <button className="btn sm" data-testid="diff-run-btn" onClick={handleRun} disabled={!canRun || diffMut.isPending}
          style={{ background: canRun ? 'var(--fg)' : undefined, color: canRun ? 'var(--bg)' : undefined, borderColor: canRun ? 'var(--fg)' : undefined }}>
          {diffMut.isPending ? '运行中…' : '运行对比'}
        </button>
      }
      filterBar={
        <FilterBar>
          <FSelect label="table" value={table} options={tableOpts} onChange={setTable} width={180} />
          <FSelect label="source_a" value={sourceA} width={180}
            options={[{ value: '', label: '— 选择源 A —' }, ...sourceOpts]}
            onChange={setSourceA} />
          <FSelect label="source_b" value={sourceB} width={180}
            options={[{ value: '', label: '— 选择源 B —' }, ...sourceOpts]}
            onChange={setSourceB} />
          <FInput label="start" placeholder="YYYY-MM-DD" value={start} onChange={setStart} width={110} />
          <FInput label="end" placeholder="YYYY-MM-DD" value={end} onChange={setEnd} width={110} />
          <FInput label="sample" placeholder="1000" value={sample} onChange={setSample} width={80} />
        </FilterBar>
      }
    >
      {diffMut.isError && (
        <div style={{ padding: '12px 16px' }}>
          <ErrorState error={diffMut.error} onRetry={handleRun} />
        </div>
      )}

      {!result && !diffMut.isPending && !diffMut.isError && (
        <div style={{ flex: 1, display: 'grid', placeItems: 'center' }}>
          <div style={{ textAlign: 'center', color: 'var(--fg-3)', fontFamily: 'var(--font-mono)' }}>
            <div style={{ fontSize: 32, marginBottom: 12, color: 'var(--fg-4)' }}>⇄</div>
            <div style={{ fontSize: 12 }}>选择两个数据源和日期范围，点击「运行对比」</div>
            {!canRun && sourceA === sourceB && sourceA !== '' && (
              <div style={{ color: 'var(--err)', fontSize: 11, marginTop: 8 }}>source_a 和 source_b 不能相同</div>
            )}
          </div>
        </div>
      )}

      {diffMut.isPending && <Loading label="正在运行跨源对比…" />}

      {result && !diffMut.isPending && (
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          {/* KPI summary */}
          <KpiStrip cells={[
            { label: 'SOURCE A', value: result.source_a },
            { label: 'ROWS A', value: result.n_a?.toLocaleString() ?? '—' },
            { label: 'SOURCE B', value: result.source_b },
            { label: 'ROWS B', value: result.n_b?.toLocaleString() ?? '—' },
            { label: '只在 A', value: result.n_only_in_a ?? 0, tone: result.n_only_in_a > 0 ? 'warn' : undefined },
            { label: '只在 B', value: result.n_only_in_b ?? 0, tone: result.n_only_in_b > 0 ? 'warn' : undefined },
            { label: '值不一致', value: result.n_mismatch ?? 0, tone: result.n_mismatch > 0 ? 'err' : undefined },
          ]} height={56} />

          <div style={{ padding: '6px 14px', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)', background: 'var(--surface-2)', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
            table: <b style={{ color: 'var(--fg)' }}>{result.table}</b>
            {start && <> · start: <b style={{ color: 'var(--fg)' }}>{start}</b></>}
            {end && <> · end: <b style={{ color: 'var(--fg)' }}>{end}</b></>}
          </div>

          {/* Tab bar */}
          <div style={{
            height: 30, padding: '0 14px',
            background: 'var(--surface-2)',
            borderBottom: '1px solid var(--border)',
            display: 'flex', alignItems: 'flex-end',
            flexShrink: 0,
          }}>
            {diffTabs.map(t => (
              <button key={t.k} onClick={() => setDiffTab(t.k)} style={{
                height: 30, padding: '0 14px',
                background: diffTab === t.k ? 'var(--surface)' : 'transparent',
                border: 'none',
                borderTop: diffTab === t.k ? '1px solid var(--fg)' : '1px solid transparent',
                borderLeft: diffTab === t.k ? '1px solid var(--border)' : 'none',
                borderRight: diffTab === t.k ? '1px solid var(--border)' : 'none',
                fontFamily: 'var(--font-mono)', fontSize: 11,
                color: diffTab === t.k ? 'var(--fg)' : 'var(--fg-3)',
                cursor: 'pointer', marginBottom: -1,
              }}>{t.l}</button>
            ))}
          </div>

          {/* Tab content */}
          <div style={{ flex: 1, minHeight: 0, overflow: 'auto' }}>
            {diffTab === 'only_a' && (
              onlyARows.length === 0
                ? <Empty label={`A (${result.source_a}) 中无独有行`} />
                : <DataTable
                    columns={[{ key: 'key', label: '主键 (只在 A 中)', mono: true, color: 'var(--warn)' }]}
                    rows={onlyARows} rowKey={r => r._key} height={26} />
            )}
            {diffTab === 'only_b' && (
              onlyBRows.length === 0
                ? <Empty label={`B (${result.source_b}) 中无独有行`} />
                : <DataTable
                    columns={[{ key: 'key', label: '主键 (只在 B 中)', mono: true, color: 'var(--warn)' }]}
                    rows={onlyBRows} rowKey={r => r._key} height={26} />
            )}
            {diffTab === 'mismatch' && (
              mismatchRows.length === 0
                ? <Empty label="无值不一致记录" />
                : <DataTable
                    columns={[
                      { key: 'key', label: '主键', mono: true, color: 'var(--fg)' },
                      { key: 'col', label: '字段', mono: true, color: 'var(--fg-2)' },
                      { key: 'a', label: `A值 (${result.source_a})`, mono: true, color: 'var(--ok)', render: v => <span style={{ color: 'var(--ok)' }}>{v}</span> },
                      { key: 'b', label: `B值 (${result.source_b})`, mono: true, color: 'var(--warn)', render: v => <span style={{ color: 'var(--warn)' }}>{v}</span> },
                    ]}
                    rows={mismatchRows} rowKey={r => r._key} height={26} />
            )}
          </div>
        </div>
      )}
    </TerminalShell>
  );
}
