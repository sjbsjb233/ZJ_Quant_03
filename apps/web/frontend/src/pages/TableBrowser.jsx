// 单表浏览 · TableBrowser (v0.2 core page)
// 3 view modes, internal cols, extras-as-columns, row lineage drawer
import React, { useState, useMemo, useCallback } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { TerminalShell } from '../components/shell';
import { FInput, FSelect, Stat } from '../components/shell';
import {
  Loading, ErrorState, Empty, CopyableId, StatusChip, StatusGlyph,
  Drawer, Modal, TableNameCode,
} from '../components/ui';
import { endpoints } from '../lib/api';
import TableQuickChart from '../components/TableQuickChart';
import {
  fmtCompact, fmtBytes, relTime, absTime, freshnessMeta, statusMeta, toneColor,
} from '../lib/format';

// ─── constants ─────────────────────────────────────────────────────────────────
const INTERNAL_COLS = new Set(['_batch_id', '_source', '_ingested_at', '_publish_date', '_status', '_invalidated_at', '_superseded_by']);
const EXTRAS_TYPES = ['VARCHAR', 'DOUBLE', 'BIGINT', 'INTEGER', 'BOOLEAN', 'DATE', 'TIMESTAMP'];
const TODAY = new Date().toISOString().slice(0, 10);

// ─── RowLineageDrawer ───────────────────────────────────────────────────────────
function RowLineageDrawer({ tableName, pk, onClose, nav }) {
  const lineageQ = useQuery({
    queryKey: ['row-lineage', tableName, pk],
    queryFn: () => endpoints.tableRowLineage(tableName, pk),
    enabled: !!tableName && !!pk,
  });

  const pkDisplay = Object.entries(pk).map(([k, v]) => `${k}=${v}`).join(', ');

  const copyJson = () => {
    if (lineageQ.data) {
      navigator.clipboard?.writeText(JSON.stringify(lineageQ.data, null, 2));
    }
  };

  return (
    <Drawer width={540} onClose={onClose}>
      {/* Drawer header */}
      <div data-testid="lineage-drawer" style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{
            fontFamily: 'var(--font-mono)', fontSize: 10,
            color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.08em',
          }}>ROW LINEAGE</span>
          <div style={{ flex: 1 }} />
          <button data-testid="lineage-close" className="btn ghost sm" style={{ height: 20 }} onClick={onClose}>✕</button>
        </div>
        <div data-testid="lineage-pk" style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--fg)', marginTop: 4 }}>
          {pkDisplay}
        </div>
        {lineageQ.data && (
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', marginTop: 2 }}>
            {lineageQ.data.history?.length || 0} 次写入 · {lineageQ.data.current ? '当前生效' : '无生效数据'}
          </div>
        )}
      </div>

      <div className="scroll" style={{ flex: 1, minHeight: 0 }}>
        {lineageQ.isLoading && <Loading />}
        {lineageQ.error && <ErrorState error={lineageQ.error} onRetry={lineageQ.refetch} />}
        {lineageQ.data && (() => {
          const { current, history = [], diff_columns = [] } = lineageQ.data;
          return (
            <>
              {/* Current effective */}
              {current && (
                <div data-testid="lineage-current" style={{
                  margin: 12, padding: 12,
                  border: '1px solid var(--fg)', borderRadius: 4,
                  background: 'var(--ok-bg)',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                    <span style={{ color: 'var(--ok)' }}>●</span>
                    <span style={{
                      fontFamily: 'var(--font-mono)', fontSize: 9.5,
                      color: 'var(--ok)', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 600,
                    }}>CURRENTLY EFFECTIVE</span>
                    <div style={{ flex: 1 }} />
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>
                      {absTime(current.ingested_at)}
                    </span>
                  </div>
                  {current.values && (
                    <div style={{
                      display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 8,
                      fontFamily: 'var(--font-mono)', fontSize: 11,
                    }}>
                      {Object.entries(current.values).map(([k, v]) => (
                        <div key={k}>
                          <div style={{ color: 'var(--fg-3)', fontSize: 9.5, textTransform: 'uppercase', letterSpacing: '0.04em' }}>{k}</div>
                          <div style={{ color: 'var(--fg)', fontWeight: 500, fontSize: 12 }}>{v ?? '·'}</div>
                        </div>
                      ))}
                    </div>
                  )}
                  <div style={{
                    marginTop: 10, padding: '6px 8px',
                    background: 'var(--surface)', borderRadius: 3,
                    fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-2)',
                    display: 'flex', alignItems: 'center', gap: 6,
                  }}>
                    <span style={{ color: 'var(--fg-3)' }}>from</span>
                    <span
                      style={{ color: 'var(--info)', textDecoration: 'underline', textDecorationStyle: 'dotted', cursor: 'pointer' }}
                      onClick={() => { nav(`/data/batches/${current.batch_id}`); onClose(); }}
                    >
                      <CopyableId value={current.batch_id} length={22} />
                    </span>
                    <span style={{ color: 'var(--fg-3)' }}>·</span>
                    <span style={{ color: 'var(--fg-2)' }}>{current.source}</span>
                  </div>
                </div>
              )}

              {/* History */}
              {history.length > 0 && (
                <div style={{ padding: '0 12px 4px' }}>
                  <div style={{
                    fontFamily: 'var(--font-mono)', fontSize: 9.5,
                    color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.08em',
                    padding: '8px 0 4px',
                  }}>
                    HISTORY · {history.length} WRITES (newest first)
                  </div>
                </div>
              )}
              {history.map((h, i) => {
                const m = statusMeta(h.status);
                const tc = toneColor(m.tone);
                const isCurrent = h.is_current;
                return (
                  <div key={h.batch_id + i} data-testid="lineage-history-item" style={{
                    margin: '0 12px 8px', padding: 10,
                    border: '1px solid var(--border)', borderRadius: 4,
                    background: 'var(--surface)',
                    opacity: isCurrent ? 1 : 0.85,
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                      <span style={{ color: tc }}>●</span>
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg)', fontWeight: 500 }}>
                        <CopyableId value={h.batch_id} length={22} />
                      </span>
                      <StatusChip status={h.status} size={9} />
                      {isCurrent && <span className="chip info" style={{ fontSize: 9, height: 14 }}>HEAD</span>}
                      <div style={{ flex: 1 }} />
                      <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)' }}>
                        {relTime(h.ingested_at)}
                      </span>
                    </div>
                    <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-2)', marginBottom: 4 }}>
                      <span style={{ color: 'var(--fg-3)' }}>src</span> {h.source}
                      {h.values && Object.entries(h.values).slice(0, 3).map(([k, v]) => (
                        <span key={k}>
                          <span style={{ color: 'var(--fg-3)', margin: '0 4px' }}>·</span>
                          {k}=<span style={{ color: 'var(--fg)' }}>{v}</span>
                        </span>
                      ))}
                    </div>
                    <div style={{
                      fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)',
                      display: 'flex', justifyContent: 'space-between',
                    }}>
                      <span>
                        {h.status === 'invalidated'
                          ? `作废原因: `
                          : h.status === 'superseded'
                            ? `覆盖原因: `
                            : `说明: `}
                        <span style={{ color: 'var(--fg-2)' }}>
                          {h.invalidated_reason || (h.status === 'superseded' ? '被覆盖,优先级低' : '当前生效')}
                        </span>
                      </span>
                      <span
                        style={{ color: 'var(--info)', cursor: 'pointer' }}
                        onClick={() => { nav(`/data/batches/${h.batch_id}`); onClose(); }}
                      >
                        批次详情 →
                      </span>
                    </div>
                  </div>
                );
              })}

              {/* Divergent columns */}
              {diff_columns.length > 0 && (
                <div data-testid="lineage-diff" style={{ padding: '4px 12px 14px' }}>
                  <div style={{
                    fontFamily: 'var(--font-mono)', fontSize: 9.5,
                    color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.08em',
                    padding: '6px 0',
                  }}>
                    DIVERGENT COLUMNS · 跨批次值不一致
                  </div>
                  <table style={{
                    width: '100%', borderCollapse: 'collapse', fontSize: 11,
                    fontFamily: 'var(--font-mono)', background: 'var(--surface-2)',
                    border: '1px solid var(--border)',
                  }}>
                    <thead>
                      <tr style={{ borderBottom: '1px solid var(--border)' }}>
                        <th style={{ textAlign: 'left', padding: '5px 8px', fontSize: 9.5, color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.04em' }}>col</th>
                        {history.map((h) => (
                          <th key={h.batch_id} style={{ textAlign: 'left', padding: '5px 8px', fontSize: 9.5, color: h.is_current ? 'var(--ok)' : h.status === 'invalidated' ? 'var(--err)' : 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.04em' }}>
                            {h.batch_id.slice(2, 10)} ({h.status.slice(0, 3)})
                          </th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {diff_columns.map((col) => (
                        <tr key={col} style={{ borderBottom: '1px solid var(--border-faint)' }}>
                          <td style={{ padding: '4px 8px', color: 'var(--fg)', fontWeight: 500 }}>{col}</td>
                          {history.map((h, hi) => (
                            <td key={hi} style={{
                              padding: '4px 8px',
                              color: h.is_current ? 'var(--ok)' : h.status === 'invalidated' ? 'var(--err)' : 'var(--fg-3)',
                              textDecoration: h.status === 'invalidated' ? 'line-through' : 'none',
                            }}>
                              {h.values?.[col] ?? '·'}
                            </td>
                          ))}
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {!current && history.length === 0 && (
                <Empty label="无 lineage 数据" />
              )}
            </>
          );
        })()}
      </div>

      {/* Drawer footer */}
      <div style={{
        padding: '8px 12px', borderTop: '1px solid var(--border)',
        background: 'var(--surface-2)',
        display: 'flex', gap: 6,
        flexShrink: 0,
      }}>
        <button className="btn ghost sm" style={{ height: 22, fontSize: 10 }} onClick={copyJson}>复制为 JSON</button>
        {lineageQ.data?.current?.batch_id && (
          <button
            className="btn ghost sm"
            style={{ height: 22, fontSize: 10 }}
            onClick={() => { nav(`/data/batches/${lineageQ.data.current.batch_id}`); onClose(); }}
          >
            跳到批次详情
          </button>
        )}
        <div style={{ flex: 1 }} />
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-4)' }}>Esc 关闭</span>
      </div>
    </Drawer>
  );
}

// ─── ExtrasModal ──────────────────────────────────────────────────────────────
function ExtrasModal({ extrasObj, onClose }) {
  const entries = extrasObj ? Object.entries(extrasObj) : [];
  return (
    <Modal width={420} onClose={onClose} title="Extras 字段详情">
      {entries.length === 0 ? (
        <Empty label="无 extras 数据" />
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontFamily: 'var(--font-mono)', fontSize: 11 }}>
          <thead>
            <tr style={{ borderBottom: '1px solid var(--border)' }}>
              <th style={{ textAlign: 'left', padding: '5px 8px', color: 'var(--fg-3)', fontSize: 9.5, textTransform: 'uppercase', letterSpacing: '0.05em', fontWeight: 500 }}>key</th>
              <th style={{ textAlign: 'left', padding: '5px 8px', color: 'var(--fg-3)', fontSize: 9.5, textTransform: 'uppercase', letterSpacing: '0.05em', fontWeight: 500 }}>value</th>
            </tr>
          </thead>
          <tbody>
            {entries.map(([k, v]) => (
              <tr key={k} style={{ borderBottom: '1px solid var(--border-faint)' }}>
                <td style={{ padding: '4px 8px', color: 'var(--warn)' }}>{k}</td>
                <td style={{ padding: '4px 8px', color: 'var(--fg)' }}>{String(v)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Modal>
  );
}

// ─── ExtrasConfigPanel ────────────────────────────────────────────────────────
function ExtrasConfigPanel({ tableName, extrasAsColumns, setExtrasAsColumns }) {
  const [showKeysModal, setShowKeysModal] = useState(false);
  const keysQ = useQuery({
    queryKey: ['extras-keys', tableName],
    queryFn: () => endpoints.tableExtrasKeys(tableName),
    enabled: showKeysModal,
  });

  const addRow = () => setExtrasAsColumns((prev) => [...prev, { key: '', type: 'DOUBLE' }]);
  const removeRow = (i) => setExtrasAsColumns((prev) => prev.filter((_, idx) => idx !== i));
  const updateRow = (i, field, val) =>
    setExtrasAsColumns((prev) => prev.map((r, idx) => idx === i ? { ...r, [field]: val } : r));
  const pickKey = (k) => {
    setExtrasAsColumns((prev) => {
      if (prev.some((r) => r.key === k)) return prev;
      return [...prev, { key: k, type: 'DOUBLE' }];
    });
    setShowKeysModal(false);
  };

  return (
    <div style={{ display: 'inline-flex', flexDirection: 'column', gap: 4, verticalAlign: 'top' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <span style={{ color: 'var(--warn)', fontSize: 10, fontFamily: 'var(--font-mono)' }}>◇ extras as cols</span>
        <button className="btn ghost sm" style={{ height: 20, fontSize: 9.5 }} onClick={addRow}>+ 添加</button>
        <button className="btn ghost sm" style={{ height: 20, fontSize: 9.5 }} onClick={() => setShowKeysModal(true)}>可用 keys</button>
      </div>
      {extrasAsColumns.map((row, i) => (
        <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <input
            value={row.key}
            onChange={(e) => updateRow(i, 'key', e.target.value)}
            placeholder="key name"
            style={{
              width: 100, height: 20, padding: '0 5px', background: 'var(--surface-2)',
              border: '1px solid var(--border)', borderRadius: 3, fontSize: 10,
              fontFamily: 'var(--font-mono)', color: 'var(--fg)',
            }}
          />
          <select
            value={row.type}
            onChange={(e) => updateRow(i, 'type', e.target.value)}
            style={{
              height: 20, padding: '0 4px', background: 'var(--surface-2)',
              border: '1px solid var(--border)', borderRadius: 3, fontSize: 10,
              fontFamily: 'var(--font-mono)', color: 'var(--fg)', cursor: 'pointer',
            }}
          >
            {EXTRAS_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <button className="btn ghost sm" style={{ height: 20, fontSize: 9, padding: '0 5px' }} onClick={() => removeRow(i)}>✕</button>
        </div>
      ))}

      {showKeysModal && (
        <Modal width={360} onClose={() => setShowKeysModal(false)} title="可用 Extras Keys">
          {keysQ.isLoading && <Loading />}
          {keysQ.error && <ErrorState error={keysQ.error} />}
          {keysQ.data?.keys && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {keysQ.data.keys.map((k) => (
                <button key={k} className="btn ghost sm" style={{ fontSize: 10.5 }} onClick={() => pickKey(k)}>
                  {k}
                </button>
              ))}
            </div>
          )}
        </Modal>
      )}
    </div>
  );
}

// ─── Main TableBrowser ────────────────────────────────────────────────────────
export default function TableBrowser() {
  const { tableName } = useParams();
  const nav = useNavigate();

  // View state
  const [viewMode, setViewMode] = useState('current'); // 'current' | 'all' | 'batch'
  const [viewTab, setViewTab] = useState('table');      // 'table' | 'chart'
  const [showInternal, setShowInternal] = useState(true);

  // Filters
  const [symbols, setSymbols] = useState('');
  const [dateStart, setDateStart] = useState('');
  const [dateEnd, setDateEnd] = useState('');
  const [batchId, setBatchId] = useState('');
  const [source, setSource] = useState('');
  const [limit, setLimit] = useState('200');
  const [asOfDate, setAsOfDate] = useState(TODAY);
  const [adjust, setAdjust] = useState('none');
  const [frequency, setFrequency] = useState('1m');

  // Extras as columns
  const [extrasAsColumns, setExtrasAsColumns] = useState([]);

  // Lineage drawer
  const [lineageRow, setLineageRow] = useState(null); // { pk: {...} }

  // Extras modal
  const [extrasModal, setExtrasModal] = useState(null);

  const isBarsDaily = tableName === 'raw.bars_daily';
  const isBarsMinute = tableName === 'raw.bars_minute';
  const isFundamentals = tableName === 'raw.fundamentals';

  // Schema query
  const schemaQ = useQuery({
    queryKey: ['table-schema', tableName],
    queryFn: () => endpoints.tableSchema(tableName),
    enabled: !!tableName,
  });
  const schema = schemaQ.data;

  // Build query params
  const queryParams = useMemo(() => {
    const p = {};
    if (symbols.trim()) p.symbols = symbols.trim();
    if (dateStart) p.start = dateStart;
    if (dateEnd) p.end = dateEnd;
    if (source.trim()) p.source = source.trim();
    if (parseInt(limit) > 0) p.limit = parseInt(limit);
    if (extrasAsColumns.length > 0) {
      const valid = extrasAsColumns.filter((r) => r.key.trim());
      if (valid.length > 0) p.extras_as_columns = valid.map((r) => `${r.key}:${r.type}`).join(',');
    }
    if (isFundamentals) p.as_of_date = asOfDate || TODAY;
    if (isBarsDaily) p.adjust = adjust;
    if (isBarsMinute) p.frequency = frequency;
    if (viewMode === 'batch' && batchId.trim()) p.batch_id = batchId.trim();
    return p;
  }, [symbols, dateStart, dateEnd, source, limit, extrasAsColumns, isFundamentals, asOfDate, isBarsDaily, adjust, isBarsMinute, frequency, viewMode, batchId]);

  // Row data query
  const rowsQueryFn = useCallback(() => {
    if (viewMode === 'current') return endpoints.tableRows(tableName, queryParams);
    return endpoints.tableRowsAll(tableName, queryParams);
  }, [tableName, viewMode, queryParams]);

  const rowsQ = useQuery({
    queryKey: ['table-rows', tableName, viewMode, queryParams],
    queryFn: rowsQueryFn,
    enabled: !!tableName && !!schema && (viewMode !== 'batch' || !!batchId.trim()),
  });

  // Build pk from row using schema.primary_key
  const buildPk = useCallback((row) => {
    if (!schema?.primary_key) return {};
    return Object.fromEntries(schema.primary_key.map((k) => [k, row[k]]));
  }, [schema]);

  // Build columns dynamically from response
  const tableColumns = useMemo(() => {
    if (!rowsQ.data?.columns) return [];
    const responseCols = rowsQ.data.columns || [];
    const cols = [];

    responseCols.forEach(({ name }) => {
      if (name === '_extras') {
        // Extras column - show as chip that opens modal
        cols.push({
          key: '_extras',
          label: '◇ _extras',
          headColor: 'var(--warn)',
          headBg: 'rgba(216,153,0,0.06)',
          cellBg: 'rgba(216,153,0,0.04)',
          color: 'var(--warn)',
          fontSize: 10,
          mono: true,
          render: (v, row) => {
            if (!v || typeof v !== 'object') return <span style={{ color: 'var(--fg-4)' }}>·</span>;
            const n = Object.keys(v).length;
            return (
              <span
                className="chip warn"
                style={{ fontSize: 9.5, cursor: 'pointer' }}
                onClick={(e) => { e.stopPropagation(); setExtrasModal(v); }}
              >
                {n} keys ⤵
              </span>
            );
          },
        });
        return;
      }

      if (INTERNAL_COLS.has(name)) {
        if (!showInternal) return;
        // Internal columns - tinted blue
        if (name === '_batch_id') {
          cols.push({
            key: '_batch_id',
            label: '⚙ _batch_id',
            headColor: 'var(--info)',
            headBg: 'rgba(58,128,200,0.06)',
            cellBg: 'rgba(58,128,200,0.04)',
            color: 'var(--info)',
            fontSize: 10,
            mono: true,
            render: (v) => v ? (
              <CopyableId
                value={v}
                length={20}
                style={{ color: 'var(--info)', cursor: 'pointer' }}
              />
            ) : '·',
          });
        } else {
          cols.push({
            key: name,
            label: `⚙ ${name}`,
            headColor: 'var(--info)',
            headBg: 'rgba(58,128,200,0.06)',
            cellBg: 'rgba(58,128,200,0.04)',
            color: 'var(--info)',
            fontSize: 10,
            mono: true,
          });
        }
        return;
      }

      // Business column
      const isPk = schema?.primary_key?.includes(name);
      cols.push({
        key: name,
        label: name,
        mono: true,
        color: isPk ? 'var(--fg)' : 'var(--fg-2)',
        headColor: isPk ? 'var(--fg-2)' : 'var(--fg-3)',
        align: isPk ? 'left' : 'right',
        fontSize: 11,
        cellStyle: isPk ? () => ({ fontWeight: 500 }) : undefined,
      });
    });

    // Actions column
    cols.push({
      key: '__actions',
      label: '',
      align: 'right',
      render: (_v, row) => (
        <span
          style={{ color: 'var(--info)', fontSize: 10, cursor: 'pointer', userSelect: 'none' }}
          onClick={(e) => { e.stopPropagation(); setLineageRow({ pk: buildPk(row) }); }}
        >
          lineage →
        </span>
      ),
    });

    return cols;
  }, [rowsQ.data?.columns, showInternal, schema, buildPk]);

  const rows = rowsQ.data?.items || [];
  const total = rowsQ.data?.total || 0;

  // Row style for 'all' view — invalidated rows get opacity + line-through
  const rowStyle = useCallback((row) => {
    if (viewMode === 'all' && row._status === 'invalidated') {
      return { opacity: 0.55, textDecoration: 'line-through', color: 'var(--fg-4)' };
    }
    return {};
  }, [viewMode]);

  // Badge from schema freshness
  const fm = schema ? freshnessMeta('ok') : null;

  // First symbol for kline link
  const firstSymbol = rows[0]?.symbol || '';

  // path breadcrumb
  const path = ['data_mgmt/', 'tables/', tableName || ''];

  const fkeys = [
    { k: 'F1', l: '当前生效', onClick: () => setViewMode('current'), active: viewMode === 'current' },
    { k: 'F2', l: '全量(含作废)', onClick: () => setViewMode('all'), active: viewMode === 'all' },
    { k: 'F3', l: '单批次', onClick: () => setViewMode('batch'), active: viewMode === 'batch' },
  ];

  const isBarsTable = isBarsDaily || isBarsMinute;

  return (
    <TerminalShell
      module="data"
      current="tables"
      path={path}
      fkeys={fkeys}
      title={tableName || ''}
      subtitle={schema ? `${fmtCompact(schema.columns?.length || 0)} cols · pk: ${(schema.primary_key || []).join(', ')}` : '加载中…'}
      badge={schemaQ.isLoading ? null : <span className="chip ok" style={{ fontSize: 10 }}>FRESH</span>}
      actions={
        <>
          {/* 表格 / 图表 dual tab (doc §4.5.1 / §6.1) */}
          <div style={{ display: 'inline-flex', border: '1px solid var(--border-strong)', borderRadius: 3, overflow: 'hidden', marginRight: 8 }}>
            {[{ id: 'table', l: '表格视图' }, { id: 'chart', l: '图表视图 ↗' }].map((t, i) => (
              <button
                key={t.id}
                data-testid={`tablebrowser-tab-${t.id}`}
                onClick={() => setViewTab(t.id)}
                style={{
                  height: 24, padding: '0 10px',
                  background: viewTab === t.id ? 'var(--fg)' : 'transparent',
                  color: viewTab === t.id ? 'var(--bg)' : 'var(--fg-2)',
                  border: 'none', borderRight: i === 0 ? '1px solid var(--border-strong)' : 'none',
                  fontFamily: 'var(--font-mono)', fontSize: 10.5, cursor: 'pointer',
                }}
              >{t.l}</button>
            ))}
          </div>
          <button className="btn ghost sm" onClick={() => nav(`/data/tables`)}>← 返回</button>
          {isBarsTable && firstSymbol && (
            <button
              className="btn ghost sm"
              onClick={() => nav(`/data/charts/kline?symbol=${encodeURIComponent(firstSymbol)}`)}
            >
              ↗ K 线工作台
            </button>
          )}
        </>
      }
      filterBar={
        <div style={{
          padding: '6px 14px',
          background: 'var(--surface)',
          borderBottom: '1px solid var(--border)',
          display: 'flex', alignItems: 'center', gap: 0,
          flexWrap: 'wrap',
          fontFamily: 'var(--font-mono)', fontSize: 11,
          flexShrink: 0,
          rowGap: 6,
        }}>
          {/* View mode segmented */}
          <span style={{ fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginRight: 10 }}>view</span>
          <div style={{ display: 'inline-flex', border: '1px solid var(--border-strong)', borderRadius: 3, overflow: 'hidden', marginRight: 14 }}>
            {[
              { id: 'current', l: '当前生效', s: '◉' },
              { id: 'all', l: '全量(含作废)', s: '⊕' },
              { id: 'batch', l: '单批次', s: '◐' },
            ].map((m) => (
              <button
                key={m.id}
                onClick={() => setViewMode(m.id)}
                style={{
                  height: 24, padding: '0 10px',
                  background: viewMode === m.id ? 'var(--fg)' : 'transparent',
                  color: viewMode === m.id ? 'var(--bg)' : 'var(--fg-2)',
                  border: 'none', borderRight: '1px solid var(--border-strong)',
                  fontFamily: 'var(--font-mono)', fontSize: 10.5, cursor: 'pointer',
                  display: 'inline-flex', alignItems: 'center', gap: 5,
                }}
              >
                <span>{m.s}</span> {m.l}
              </button>
            ))}
          </div>

          <span style={{ color: 'var(--fg-mute)', marginRight: 14 }}>│</span>

          {/* Table-specific controls */}
          {isFundamentals && (
            <>
              <FInput
                label="as_of_date"
                placeholder="YYYY-MM-DD"
                value={asOfDate}
                onChange={setAsOfDate}
                width={110}
              />
              <span style={{ marginRight: 8 }} />
            </>
          )}

          {isBarsDaily && (
            <>
              <span style={{ fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginRight: 6 }}>adjust</span>
              <div style={{ display: 'inline-flex', border: '1px solid var(--border-strong)', borderRadius: 3, overflow: 'hidden', marginRight: 14 }}>
                {['none', 'pre', 'post'].map((a) => (
                  <button
                    key={a}
                    onClick={() => setAdjust(a)}
                    style={{
                      height: 22, padding: '0 8px',
                      background: adjust === a ? 'var(--fg)' : 'transparent',
                      color: adjust === a ? 'var(--bg)' : 'var(--fg-2)',
                      border: 'none', borderRight: '1px solid var(--border-strong)',
                      fontFamily: 'var(--font-mono)', fontSize: 10, cursor: 'pointer',
                    }}
                  >
                    {a}
                  </button>
                ))}
              </div>
            </>
          )}

          {isBarsMinute && (
            <>
              <FSelect
                label="freq"
                value={frequency}
                options={['1m', '5m', '15m', '30m', '1h']}
                onChange={setFrequency}
                width={60}
              />
              <span style={{ marginRight: 8 }} />
            </>
          )}

          {viewMode === 'batch' && (
            <>
              <FInput
                label="batch_id"
                placeholder="b_2026_…"
                value={batchId}
                onChange={setBatchId}
                width={180}
              />
              <span style={{ marginRight: 8 }} />
            </>
          )}

          {/* WHERE / symbols */}
          <FInput
            label="symbols"
            placeholder="600519.SH,000001.SZ"
            value={symbols}
            onChange={setSymbols}
            width={210}
          />
          <span style={{ marginRight: 8 }} />

          {/* Date range */}
          <FInput label="start" placeholder="YYYY-MM-DD" value={dateStart} onChange={setDateStart} width={100} />
          <span style={{ marginRight: 4 }} />
          <span style={{ color: 'var(--fg-3)', marginRight: 4 }}>→</span>
          <FInput label="end" placeholder="YYYY-MM-DD" value={dateEnd} onChange={setDateEnd} width={100} />
          <span style={{ marginRight: 8 }} />

          {/* limit */}
          <FSelect
            label="limit"
            value={limit}
            options={['50', '100', '200', '500', '1000']}
            onChange={setLimit}
            width={60}
          />

          <span style={{ color: 'var(--fg-mute)', margin: '0 10px' }}>│</span>

          {/* Show internal toggle */}
          <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer', color: 'var(--fg-2)', marginRight: 14 }}>
            <input
              type="checkbox"
              checked={showInternal}
              onChange={(e) => setShowInternal(e.target.checked)}
              style={{ accentColor: 'var(--fg)' }}
            />
            显示内部列
            <span style={{ color: 'var(--fg-3)', fontSize: 10 }}>(_batch_id, _source, …)</span>
          </label>

          {/* Extras as columns — only if schema.has_extras */}
          {schema?.has_extras && (
            <>
              <span style={{ color: 'var(--fg-mute)', marginRight: 10 }}>│</span>
              <ExtrasConfigPanel
                tableName={tableName}
                extrasAsColumns={extrasAsColumns}
                setExtrasAsColumns={setExtrasAsColumns}
              />
            </>
          )}
        </div>
      }
    >
      {/* View banner */}
      <div style={{
        padding: '5px 14px',
        background: viewMode === 'all' ? 'var(--warn-bg)' : viewMode === 'batch' ? 'var(--info-bg)' : 'var(--ok-bg)',
        borderBottom: '1px solid var(--border)',
        fontFamily: 'var(--font-mono)', fontSize: 10.5,
        color: viewMode === 'all' ? 'var(--warn)' : viewMode === 'batch' ? 'var(--info)' : 'var(--ok)',
        display: 'flex', alignItems: 'center', gap: 8,
        flexShrink: 0,
      }}>
        <span>{viewMode === 'all' ? '⚠' : viewMode === 'batch' ? 'ⓘ' : '●'}</span>
        <span>
          {viewMode === 'current' && '当前生效视图 · 已应用作废/替换规则后的最终有效数据'}
          {viewMode === 'all' && '全量视图 · 显示所有历史版本(含已作废),作废行用灰色 + 删除线展示'}
          {viewMode === 'batch' && (batchId ? `单批次视图 · 仅显示 ${batchId} 写入的内容` : '单批次视图 · 请输入 batch_id 进行过滤')}
        </span>
        {isFundamentals && (
          <>
            <span style={{ color: viewMode === 'all' ? 'var(--warn)' : viewMode === 'batch' ? 'var(--info)' : 'var(--ok)' }}>│</span>
            <span>as_of_date: {asOfDate}</span>
          </>
        )}
      </div>

      {/* Main content + potential drawer */}
      <div style={{ flex: 1, display: 'flex', minHeight: 0, position: 'relative', overflow: 'hidden' }}>
        {/* Chart view */}
        {viewTab === 'chart' ? (
          <div style={{ flex: 1, minWidth: 0, minHeight: 0, position: 'relative', background: 'var(--surface)' }}>
            <TableQuickChart table={tableName} rows={rows} filters={queryParams} nav={nav} />
          </div>
        ) : (
        /* Table area */
        <div className="scroll" style={{ flex: 1, minWidth: 0, minHeight: 0 }}>
          {schemaQ.isLoading ? (
            <Loading />
          ) : schemaQ.error ? (
            <ErrorState error={schemaQ.error} onRetry={schemaQ.refetch} />
          ) : viewMode === 'batch' && !batchId.trim() ? (
            <Empty label="请输入 batch_id 以过滤单批次数据" hint="在上方 filter bar 填写 batch_id" />
          ) : rowsQ.isLoading ? (
            <Loading />
          ) : rowsQ.error ? (
            <ErrorState error={rowsQ.error} onRetry={rowsQ.refetch} />
          ) : rows.length === 0 ? (
            <Empty label="暂无数据" hint="调整过滤条件后重试" />
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
              <thead style={{ position: 'sticky', top: 0, background: 'var(--surface)', zIndex: 2 }}>
                <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
                  {tableColumns.map((c, i) => (
                    <th key={c.key || i} style={{
                      textAlign: c.align || 'left',
                      padding: '6px 10px',
                      fontSize: 9.5, fontWeight: 500,
                      color: c.headColor || 'var(--fg-3)',
                      textTransform: 'uppercase', letterSpacing: '0.05em',
                      background: c.headBg || 'var(--surface)',
                      whiteSpace: 'nowrap',
                    }}>
                      {c.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rows.map((row, ri) => {
                  const extra = rowStyle(row, ri);
                  const isInvalidated = viewMode === 'all' && row._status === 'invalidated';
                  const rowPk = buildPk(row);
                  return (
                    <tr
                      key={ri}
                      data-testid={`table-row-${Object.values(rowPk).join('-')}`}
                      onClick={() => setLineageRow({ pk: rowPk })}
                      style={{
                        borderBottom: '1px solid var(--border-faint)',
                        height: 26,
                        background: ri % 2 === 0 ? 'transparent' : 'rgba(0,0,0,0.012)',
                        cursor: 'pointer',
                        ...extra,
                      }}
                      onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--hover)'; }}
                      onMouseLeave={(e) => { e.currentTarget.style.background = isInvalidated ? 'rgba(0,0,0,0.03)' : (ri % 2 === 0 ? 'transparent' : 'rgba(0,0,0,0.012)'); }}
                    >
                      {tableColumns.map((c, ci) => {
                        const v = c.render ? c.render(row[c.key], row, ri) : row[c.key];
                        const cellBgStyle = c.cellBg ? { background: c.cellBg } : {};
                        const cellExtraStyle = c.cellStyle ? c.cellStyle(row) : {};
                        return (
                          <td
                            key={c.key || ci}
                            style={{
                              padding: '0 10px',
                              textAlign: c.align || 'left',
                              fontFamily: c.mono !== false ? 'var(--font-mono)' : 'inherit',
                              fontVariantNumeric: c.align === 'right' ? 'tabular-nums' : 'normal',
                              color: c.color || (isInvalidated ? 'var(--fg-4)' : 'var(--fg-2)'),
                              fontSize: c.fontSize || 11,
                              whiteSpace: 'nowrap',
                              ...cellBgStyle,
                              ...cellExtraStyle,
                            }}
                          >
                            {v == null ? <span style={{ color: 'var(--fg-4)' }}>·</span> : v}
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
        )}

        {/* Row lineage drawer */}
        {lineageRow && (
          <RowLineageDrawer
            tableName={tableName}
            pk={lineageRow.pk}
            onClose={() => setLineageRow(null)}
            nav={nav}
          />
        )}
      </div>

      {/* Footer status line */}
      <div style={{
        height: 26, padding: '0 14px',
        borderTop: '1px solid var(--border)',
        background: 'var(--surface-2)',
        display: 'flex', alignItems: 'center',
        fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)',
        flexShrink: 0, gap: 0,
      }}>
        <span>matched: <span style={{ color: 'var(--fg)' }}>{fmtCompact(rows.length)}</span></span>
        {total > rows.length && (
          <span style={{ marginLeft: 4 }}>/ <span style={{ color: 'var(--fg)' }}>{fmtCompact(total)}</span></span>
        )}
        <span style={{ color: 'var(--fg-mute)', margin: '0 10px' }}>│</span>
        <span>view: {viewMode}</span>
        {isBarsDaily && <><span style={{ color: 'var(--fg-mute)', margin: '0 10px' }}>│</span><span>adjust: {adjust}</span></>}
        {isBarsMinute && <><span style={{ color: 'var(--fg-mute)', margin: '0 10px' }}>│</span><span>freq: {frequency}</span></>}
        {isFundamentals && <><span style={{ color: 'var(--fg-mute)', margin: '0 10px' }}>│</span><span>as_of: {asOfDate}</span></>}
        <div style={{ flex: 1 }} />
        {isBarsTable && firstSymbol && (
          <button
            className="btn ghost sm"
            style={{ height: 20, fontSize: 10 }}
            onClick={() => nav(`/data/charts/kline?symbol=${encodeURIComponent(firstSymbol)}`)}
          >
            ↗ K 线工作台
          </button>
        )}
        <span style={{ color: 'var(--fg-mute)', margin: '0 10px' }}>│</span>
        <span className="kbd">L</span>
        <span style={{ marginLeft: 4 }}>行 lineage</span>
      </div>

      {/* Extras modal */}
      {extrasModal && <ExtrasModal extrasObj={extrasModal} onClose={() => setExtrasModal(null)} />}
    </TerminalShell>
  );
}
