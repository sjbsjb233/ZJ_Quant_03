import React, { useState, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { TerminalShell, TPanel, FilterBar, FSelect, KpiStrip } from '../components/shell';
import { DataTable, EChart, StatusChip, CopyableId, RelTime, Loading, Empty, ErrorState, Modal } from '../components/ui';
import { endpoints } from '../lib/api';
import { fmtN, fmtCompact, absTime, relTime } from '../lib/format';
import { useAuth } from '../lib/auth';

const RAW_TABLES = [
  '', 'raw.bars_daily', 'raw.bars_minute', 'raw.fundamentals',
  'raw.universe', 'raw.calendar', 'raw.corporate_actions',
];

const LEVEL_OPTS = ['', 'warn', 'error'];
const ACK_OPTS = ['', 'true', 'false'];

export default function Quality() {
  const [tab, setTab] = useState('issues');
  const [filterTable, setFilterTable] = useState('');
  const [filterLevel, setFilterLevel] = useState('');
  const [filterAck, setFilterAck] = useState('');
  const [selectedIssue, setSelectedIssue] = useState(null);

  const issuesParams = {
    ...(filterTable && { table: filterTable }),
    ...(filterLevel && { level: filterLevel }),
    ...(filterAck !== '' && { acknowledged: filterAck }),
    page: 1, page_size: 200,
  };

  const issues = useQuery({
    queryKey: ['quality-issues', issuesParams],
    queryFn: () => endpoints.qualityIssues(issuesParams),
  });

  const timeseries = useQuery({
    queryKey: ['quality-timeseries'],
    queryFn: () => endpoints.qualityTimeseries(30, 'level'),
  });

  const byTable = useQuery({
    queryKey: ['quality-by-table'],
    queryFn: endpoints.qualityByTable,
  });

  const items = issues.data?.items || [];
  const tableItems = byTable.data?.items || [];
  const byCheck = byTable.data?.by_check || [];

  const totalIssues = issues.data?.total ?? 0;
  const totalWarn = items.filter(i => i.level === 'warn').length;
  const totalError = items.filter(i => i.level === 'error').length;
  const tablesAffected = new Set(tableItems.filter(t => t.total > 0).map(t => t.target_table)).size;
  const ackd = items.filter(i => i.acknowledged).length;

  const kpiCells = [
    { label: 'ISSUES', value: fmtN(totalIssues) },
    { label: 'WARN', value: fmtN(totalWarn), tone: totalWarn ? 'warn' : undefined },
    { label: 'ERROR', value: fmtN(totalError), tone: totalError ? 'err' : undefined },
    { label: 'TABLES AFFECTED', value: String(tablesAffected) },
    { label: "ACK'D", value: fmtN(ackd) },
  ];

  // Build EChart timeseries option
  const chartOption = useMemo(() => {
    const series = timeseries.data?.series || [];
    const dates = [...new Set(series.map(s => s.date))].sort();
    const groups = [...new Set(series.map(s => s.group))];
    const colorMap = { warn: '#d89900', error: '#c0392b' };
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      legend: { bottom: 4, textStyle: { fontSize: 10, fontFamily: 'var(--font-mono)' } },
      grid: { top: 16, left: 48, right: 16, bottom: 40 },
      xAxis: {
        type: 'category',
        data: dates,
        axisLabel: { fontSize: 9, fontFamily: 'var(--font-mono)', rotate: 30 },
        axisLine: { lineStyle: { color: 'var(--border)' } },
      },
      yAxis: {
        type: 'value',
        axisLabel: { fontSize: 9, fontFamily: 'var(--font-mono)' },
        splitLine: { lineStyle: { color: 'var(--border-faint)' } },
      },
      series: groups.map(g => ({
        name: g,
        type: 'bar',
        stack: 'total',
        data: dates.map(d => {
          const pt = series.find(s => s.date === d && s.group === g);
          return pt ? pt.count : 0;
        }),
        itemStyle: { color: colorMap[g] || '#999' },
      })),
    };
  }, [timeseries.data]);

  const TABS = [
    { id: 'issues', l: '问题列表' },
    { id: 'by-table', l: '按表聚合' },
    { id: 'by-check', l: '按检查聚合' },
  ];

  return (
    <TerminalShell
      module="data"
      current="quality"
      path={['data_mgmt/', 'quality']}
      fkeys={[
        { k: 'F2', l: '按表' },
        { k: 'F3', l: '按检查' },
        { k: 'F4', l: '导出' },
      ]}
      title="数据质量"
      subtitle={`quality · ${totalIssues} 问题 · ${tablesAffected} 表受影响`}
    >
      <KpiStrip cells={kpiCells} height={56} />

      {/* 30-day timeseries chart */}
      <div style={{ height: 140, flexShrink: 0, borderBottom: '1px solid var(--border)', background: 'var(--surface)', padding: '6px 8px 0' }}>
        {timeseries.isLoading ? (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--fg-3)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>
            加载中…
          </div>
        ) : (
          <EChart option={chartOption} height={128} />
        )}
      </div>

      {/* Tab bar */}
      <div style={{
        height: 30, padding: '0 14px',
        background: 'var(--surface-2)', borderBottom: '1px solid var(--border)',
        display: 'flex', alignItems: 'flex-end', flexShrink: 0,
      }}>
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)} style={{
            height: 30, padding: '0 14px',
            background: tab === t.id ? 'var(--surface)' : 'transparent',
            border: 'none',
            borderTop: tab === t.id ? '1px solid var(--fg)' : '1px solid transparent',
            borderLeft: tab === t.id ? '1px solid var(--border)' : 'none',
            borderRight: tab === t.id ? '1px solid var(--border)' : 'none',
            fontFamily: 'var(--font-mono)', fontSize: 11,
            color: tab === t.id ? 'var(--fg)' : 'var(--fg-3)',
            cursor: 'pointer', marginBottom: -1,
          }}>{t.l}</button>
        ))}
      </div>

      {/* Tab content */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
        {tab === 'issues' && (
          <IssuesTab
            issues={issues}
            items={items}
            filterTable={filterTable} setFilterTable={setFilterTable}
            filterLevel={filterLevel} setFilterLevel={setFilterLevel}
            filterAck={filterAck} setFilterAck={setFilterAck}
            onSelect={setSelectedIssue}
          />
        )}
        {tab === 'by-table' && <ByTableTab tableItems={tableItems} isLoading={byTable.isLoading} error={byTable.error} />}
        {tab === 'by-check' && <ByCheckTab byCheck={byCheck} isLoading={byTable.isLoading} error={byTable.error} />}
      </div>

      {selectedIssue && (
        <AcknowledgeModal
          issue={selectedIssue}
          onClose={() => setSelectedIssue(null)}
        />
      )}
    </TerminalShell>
  );
}

function IssuesTab({ issues, items, filterTable, setFilterTable, filterLevel, setFilterLevel, filterAck, setFilterAck, onSelect }) {
  const applied = [filterTable, filterLevel, filterAck].filter(Boolean).map(v => ({ v }));

  const columns = [
    {
      key: 'detected_at', label: '发现时间', mono: true,
      render: (v) => <RelTime value={v} />,
    },
    {
      key: 'batch_id', label: 'batch_id', mono: true,
      render: (v) => (
        <a href={`/data/batches/${v}`} onClick={e => e.stopPropagation()} style={{ color: 'var(--info)', textDecoration: 'none' }}>
          <CopyableId value={v} length={16} />
        </a>
      ),
    },
    { key: 'target_table', label: '表', mono: true, fontSize: 10.5, color: 'var(--fg-2)' },
    { key: 'check_name', label: '检查名', mono: true },
    {
      key: 'level', label: '级别',
      render: (v) => <StatusChip status={v} size={10} />,
    },
    { key: 'count', label: '行数', align: 'right', mono: true, render: (v) => fmtN(v) },
    {
      key: 'message', label: '消息', fontSize: 10.5, color: 'var(--fg-2)',
      render: (v) => <span title={v} style={{ maxWidth: 240, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'block' }}>{v}</span>,
    },
    {
      key: 'acknowledged', label: '已知',
      render: (v) => <span style={{ color: v ? 'var(--ok)' : 'var(--fg-4)', fontFamily: 'var(--font-mono)' }}>{v ? '✓' : '○'}</span>,
    },
  ];

  return (
    <>
      <FilterBar applied={applied} onClear={() => { setFilterTable(''); setFilterLevel(''); setFilterAck(''); }}>
        <FSelect
          label="表"
          value={filterTable}
          options={RAW_TABLES.map(v => ({ value: v, label: v || '全部' }))}
          onChange={setFilterTable}
          width={160}
        />
        <FSelect
          label="级别"
          value={filterLevel}
          options={LEVEL_OPTS.map(v => ({ value: v, label: v || '全部' }))}
          onChange={setFilterLevel}
          width={90}
        />
        <FSelect
          label="已知"
          value={filterAck}
          options={[{ value: '', label: '全部' }, { value: 'false', label: '未标记' }, { value: 'true', label: '已标记' }]}
          onChange={setFilterAck}
          width={90}
        />
      </FilterBar>
      <div className="scroll" style={{ flex: 1, minHeight: 0 }}>
        {issues.isLoading ? <Loading /> :
         issues.error ? <ErrorState error={issues.error} onRetry={issues.refetch} /> :
         !items.length ? <Empty label="暂无质量问题" hint="调整筛选条件后重试" /> : (
          <DataTable
            columns={columns}
            rows={items}
            rowKey={(r) => r.id}
            onRowClick={(r) => onSelect(r)}
            height={26}
          />
        )}
      </div>
    </>
  );
}

function ByTableTab({ tableItems, isLoading, error }) {
  const byTableChartOption = useMemo(() => {
    const sorted = [...tableItems].sort((a, b) => b.total - a.total);
    return {
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { top: 12, left: 120, right: 16, bottom: 24 },
      xAxis: { type: 'value', axisLabel: { fontSize: 9, fontFamily: 'var(--font-mono)' }, splitLine: { lineStyle: { color: 'var(--border-faint)' } } },
      yAxis: {
        type: 'category',
        data: sorted.map(t => t.target_table),
        axisLabel: { fontSize: 9, fontFamily: 'var(--font-mono)' },
        axisLine: { lineStyle: { color: 'var(--border)' } },
      },
      series: [
        { name: 'warn', type: 'bar', stack: 'total', data: sorted.map(t => t.warn), itemStyle: { color: '#d89900' } },
        { name: 'error', type: 'bar', stack: 'total', data: sorted.map(t => t.error), itemStyle: { color: '#c0392b' } },
      ],
    };
  }, [tableItems]);

  if (isLoading) return <Loading />;
  if (error) return <ErrorState error={error} />;
  if (!tableItems.length) return <Empty label="暂无按表数据" />;

  return (
    <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1fr 1fr', minHeight: 0 }}>
      <TPanel title="PER-TABLE ISSUE COUNT" meta={`${tableItems.length} tables`}>
        <EChart option={byTableChartOption} height={300} />
      </TPanel>
      <TPanel title="TABLE SUMMARY" last>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--font-mono)', fontSize: 11 }}>
          <thead style={{ position: 'sticky', top: 0, background: 'var(--surface)' }}>
            <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
              {['表名', 'warn', 'error', '合计'].map((h, i) => (
                <th key={h} style={{ padding: '6px 10px', textAlign: i > 0 ? 'right' : 'left', fontSize: 9.5, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {tableItems.map((t, i) => (
              <tr key={t.target_table} style={{ borderBottom: '1px solid var(--border-faint)', height: 26, background: i % 2 === 0 ? 'transparent' : 'rgba(0,0,0,0.012)' }}>
                <td style={{ padding: '0 10px', color: 'var(--fg)', fontWeight: 500 }}>{t.target_table}</td>
                <td style={{ padding: '0 10px', textAlign: 'right', color: t.warn ? 'var(--warn)' : 'var(--fg-4)', fontVariantNumeric: 'tabular-nums' }}>{fmtN(t.warn)}</td>
                <td style={{ padding: '0 10px', textAlign: 'right', color: t.error ? 'var(--err)' : 'var(--fg-4)', fontVariantNumeric: 'tabular-nums' }}>{fmtN(t.error)}</td>
                <td style={{ padding: '0 10px', textAlign: 'right', color: 'var(--fg)', fontVariantNumeric: 'tabular-nums', fontWeight: 500 }}>{fmtN(t.total)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TPanel>
    </div>
  );
}

function ByCheckTab({ byCheck, isLoading, error }) {
  const chartOption = useMemo(() => {
    const sorted = [...byCheck].sort((a, b) => b.total - a.total).slice(0, 15);
    return {
      tooltip: { trigger: 'axis' },
      grid: { top: 12, left: 150, right: 16, bottom: 24 },
      xAxis: { type: 'value', axisLabel: { fontSize: 9, fontFamily: 'var(--font-mono)' }, splitLine: { lineStyle: { color: 'var(--border-faint)' } } },
      yAxis: {
        type: 'category',
        data: sorted.map(c => c.check_name),
        axisLabel: { fontSize: 9, fontFamily: 'var(--font-mono)' },
        axisLine: { lineStyle: { color: 'var(--border)' } },
      },
      series: [{ type: 'bar', data: sorted.map(c => c.total), itemStyle: { color: 'var(--info)' } }],
    };
  }, [byCheck]);

  if (isLoading) return <Loading />;
  if (error) return <ErrorState error={error} />;
  if (!byCheck.length) return <Empty label="暂无按检查数据" />;

  return (
    <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '1fr 1fr', minHeight: 0 }}>
      <TPanel title="PER-CHECK ISSUE COUNT" meta="top 15">
        <EChart option={chartOption} height={300} />
      </TPanel>
      <TPanel title="CHECK SUMMARY" last>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--font-mono)', fontSize: 11 }}>
          <thead style={{ position: 'sticky', top: 0, background: 'var(--surface)' }}>
            <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
              {['检查名', '合计'].map((h, i) => (
                <th key={h} style={{ padding: '6px 10px', textAlign: i > 0 ? 'right' : 'left', fontSize: 9.5, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.05em' }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {byCheck.map((c, i) => (
              <tr key={c.check_name} style={{ borderBottom: '1px solid var(--border-faint)', height: 26, background: i % 2 === 0 ? 'transparent' : 'rgba(0,0,0,0.012)' }}>
                <td style={{ padding: '0 10px', color: 'var(--fg)', fontWeight: 500 }}>{c.check_name}</td>
                <td style={{ padding: '0 10px', textAlign: 'right', color: 'var(--fg)', fontVariantNumeric: 'tabular-nums' }}>{fmtN(c.total)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TPanel>
    </div>
  );
}

function AcknowledgeModal({ issue, onClose }) {
  const { hasRole } = useAuth();
  const qc = useQueryClient();
  const [reason, setReason] = useState('');
  const [expiresAt, setExpiresAt] = useState('');

  const ackMutation = useMutation({
    mutationFn: () => endpoints.acknowledgeIssue(issue.id, {
      acknowledged: true,
      ack_reason: reason,
      ...(expiresAt && { ack_expires_at: expiresAt }),
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['quality-issues'] });
      onClose();
    },
  });

  const canEdit = hasRole('editor');

  return (
    <Modal width={620} onClose={onClose} title={`问题详情 · ${issue.check_name}`}
      footer={
        <>
          <button className="btn ghost sm" onClick={onClose}>关闭</button>
          {canEdit && !issue.acknowledged && (
            <button className="btn sm" disabled={!reason.trim() || ackMutation.isPending}
              onClick={() => ackMutation.mutate()}
              style={{ background: 'var(--fg)', color: 'var(--bg)', borderColor: 'var(--fg)' }}>
              {ackMutation.isPending ? '提交中…' : '标记为已知问题'}
            </button>
          )}
        </>
      }
    >
      {/* Issue metadata */}
      <div style={{ display: 'grid', gridTemplateColumns: '110px 1fr', gap: 4, fontFamily: 'var(--font-mono)', fontSize: 11, marginBottom: 14 }}>
        {[
          ['id', issue.id],
          ['batch_id', issue.batch_id],
          ['表名', issue.target_table],
          ['检查名', issue.check_name],
          ['级别', issue.level],
          ['行数', fmtN(issue.count)],
          ['发现时间', absTime(issue.detected_at)],
          ['已标记', issue.acknowledged ? `是 · by ${issue.ack_by || '—'}` : '否'],
          ...(issue.ack_reason ? [['标记原因', issue.ack_reason]] : []),
          ...(issue.ack_expires_at ? [['过期时间', absTime(issue.ack_expires_at)]] : []),
        ].map(([k, v]) => (
          <React.Fragment key={k}>
            <span style={{ color: 'var(--fg-3)' }}>{k}</span>
            <span style={{ color: 'var(--fg)' }}>{v}</span>
          </React.Fragment>
        ))}
      </div>

      {/* Message */}
      <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>MESSAGE</div>
      <div style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3, padding: '8px 12px', fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-2)', marginBottom: 14 }}>
        {issue.message || '—'}
      </div>

      {/* Sample JSON */}
      {issue.sample && (
        <>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 }}>SAMPLE JSON</div>
          <pre style={{
            background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3,
            padding: '8px 12px', fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg)',
            overflowX: 'auto', maxHeight: 160, overflowY: 'auto', marginBottom: 14,
          }}>
            {typeof issue.sample === 'string' ? issue.sample : JSON.stringify(issue.sample, null, 2)}
          </pre>
        </>
      )}

      {/* Acknowledge form (editor+, only if not already acked) */}
      {canEdit && !issue.acknowledged && (
        <>
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>标记为已知问题</div>
          <div style={{ marginBottom: 10 }}>
            <label style={{ fontSize: 10, color: 'var(--fg-3)', display: 'block', marginBottom: 3 }}>标记原因 *</label>
            <textarea
              value={reason}
              onChange={e => setReason(e.target.value)}
              rows={3}
              placeholder="请填写标记为已知问题的原因…"
              style={{
                width: '100%', padding: '6px 10px',
                background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3,
                fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)',
                resize: 'vertical', boxSizing: 'border-box',
              }}
            />
          </div>
          <div>
            <label style={{ fontSize: 10, color: 'var(--fg-3)', display: 'block', marginBottom: 3 }}>过期时间（可选）</label>
            <input
              type="date"
              value={expiresAt}
              onChange={e => setExpiresAt(e.target.value)}
              style={{
                height: 28, padding: '0 10px',
                background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3,
                fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)',
              }}
            />
          </div>
          {ackMutation.error && (
            <div style={{ marginTop: 10, color: 'var(--err)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>
              ✕ {ackMutation.error?.apiError?.message || '提交失败'}
            </div>
          )}
        </>
      )}

      {issue.acknowledged && (
        <div style={{ padding: '8px 12px', background: 'var(--ok-bg, rgba(0,160,100,0.08))', border: '1px solid var(--ok)', borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--ok)' }}>
          ✓ 已标记为已知问题{issue.ack_by ? ` · by ${issue.ack_by}` : ''}
        </div>
      )}
    </Modal>
  );
}
