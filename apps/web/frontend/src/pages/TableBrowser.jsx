// 单表浏览 · TableBrowser (v1.0 schema-driven)
// Filter bar / lineage entry are now driven by the backend schema "capability
// contract" (schema.filters / schema.lineage_kind) instead of per-table
// hard-coding, so each table renders only the controls that make sense for it
// (方案 §2 / §3). The OOM guardrails (_REQUIRE_FILTER, count+page pushdown) are
// unchanged on the backend; the frontend only adds a pre-flight prompt.
import React, { useState, useMemo, useCallback, useEffect, useRef } from 'react';
import { useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { TerminalShell } from '../components/shell';
import { FInput, FSelect } from '../components/shell';
import {
  Loading, ErrorState, Empty, CopyableId, StatusChip,
  Drawer, Modal, SymbolMultiSelect,
} from '../components/ui';
import { endpoints } from '../lib/api';
import TableQuickChart from '../components/TableQuickChart';
import {
  fmtCompact, relTime, absTime, statusMeta, toneColor,
} from '../lib/format';

// ─── constants ─────────────────────────────────────────────────────────────────
const INTERNAL_COLS = new Set(['_batch_id', '_source', '_ingested_at', '_publish_date', '_status', '_invalidated_at', '_superseded_by', '_updated_at']);
const EXTRAS_TYPES = ['VARCHAR', 'DOUBLE', 'BIGINT', 'INTEGER', 'BOOLEAN', 'DATE', 'TIMESTAMP'];
const TODAY = new Date().toISOString().slice(0, 10);

// Default value per declarative filter kind (mirrors backend defaults so the
// initial query matches the old hard-coded behaviour exactly).
function defaultForFilter(f) {
  if (f.kind === 'adjust') return { adjust: 'none' };
  if (f.kind === 'freq') return { frequency: '1m' };
  if (f.kind === 'as_of') return { as_of_date: TODAY };
  if (f.kind === 'bool') return { [f.key]: false };
  return {};
}

// A filter is "active" (counts toward the badge) only when it diverges from its
// default — adjust=none / freq=1m / as_of=today don't count.
function filterIsActive(f, values) {
  switch (f.kind) {
    case 'symbols': return !!(values.symbols && values.symbols.trim());
    case 'date_range': return !!(values.start || values.end);
    case 'enum': return !!values[f.key];
    case 'bool': return !!values[f.key];
    case 'int': return values[f.key] !== '' && values[f.key] != null;
    case 'as_of': return false; // always sent, never "extra"
    case 'adjust': return (values.adjust || 'none') !== 'none';
    case 'freq': return (values.frequency || '1m') !== '1m';
    default: return false;
  }
}

// Translate the declarative filter values into the backend query params. Only
// keys declared in schema.filters are emitted, so e.g. sectors never sends
// `symbols=` and bars never sends `classification_system=` (方案 §3.1).
function buildFilterParams(filters, values) {
  const p = {};
  for (const f of filters) {
    switch (f.kind) {
      case 'symbols':
        if (values.symbols && values.symbols.trim()) p.symbols = values.symbols.trim();
        break;
      case 'date_range':
        if (values.start) p.start = values.start;
        if (values.end) p.end = values.end;
        break;
      case 'enum':
        if (values[f.key]) p[f.key] = values[f.key];
        break;
      case 'bool':
        if (values[f.key]) p[f.key] = true;
        break;
      case 'int':
        if (values[f.key] !== '' && values[f.key] != null) p[f.key] = values[f.key];
        break;
      case 'as_of':
        p.as_of_date = values.as_of_date || TODAY;
        break;
      case 'adjust':
        p.adjust = values.adjust || 'none';
        break;
      case 'freq':
        p.frequency = values.frequency || '1m';
        break;
      default:
        break;
    }
  }
  return p;
}

// ─── EnumSelect — dropdown fed by the table's enum endpoint ────────────────────
function EnumSelect({ field, tableName, value, onChange }) {
  const enumQ = useQuery({
    queryKey: ['table-enum', tableName, field.key],
    queryFn: () => endpoints.tableEnum(tableName, field.key),
    enabled: !!tableName && !!field.key,
    staleTime: 60_000,
  });
  const opts = [{ value: '', label: '全部' }].concat(
    (enumQ.data?.values || []).map((v) => ({ value: String(v), label: String(v) })),
  );
  return (
    <FSelect
      label={field.label || field.key}
      testId={`filter-enum-${field.key}`}
      value={value ?? ''}
      options={opts}
      onChange={onChange}
      width={90}
    />
  );
}

// ─── FilterBar — generic renderer over schema.filters ──────────────────────────
function FilterBar({ filters, values, setV, tableName }) {
  return filters.map((f) => {
    switch (f.kind) {
      case 'symbols':
        return (
          <React.Fragment key="symbols">
            <SymbolMultiSelect value={values.symbols || ''} onChange={(v) => setV('symbols', v)} width={250} />
            <span style={{ marginRight: 10 }} />
          </React.Fragment>
        );
      case 'date_range':
        return (
          <React.Fragment key={`date-${f.key}`}>
            <FInput label="start" testId="filter-start" placeholder="YYYY-MM-DD" value={values.start || ''} onChange={(v) => setV('start', v)} width={100} />
            <span style={{ marginRight: 4 }} />
            <span style={{ color: 'var(--fg-3)', marginRight: 4 }}>→</span>
            <FInput label="end" testId="filter-end" placeholder="YYYY-MM-DD" value={values.end || ''} onChange={(v) => setV('end', v)} width={100} />
            <span style={{ marginRight: 10 }} />
          </React.Fragment>
        );
      case 'enum':
        return (
          <React.Fragment key={`enum-${f.key}`}>
            <EnumSelect field={f} tableName={tableName} value={values[f.key]} onChange={(v) => setV(f.key, v)} />
            <span style={{ marginRight: 10 }} />
          </React.Fragment>
        );
      case 'bool':
        return (
          <label key={`bool-${f.key}`} data-testid={`filter-bool-${f.key}`} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, cursor: 'pointer', color: 'var(--fg-2)', marginRight: 12 }}>
            <input type="checkbox" checked={!!values[f.key]} onChange={(e) => setV(f.key, e.target.checked)} style={{ accentColor: 'var(--fg)' }} />
            {f.label}
          </label>
        );
      case 'int':
        return (
          <React.Fragment key={`int-${f.key}`}>
            <FInput
              label={f.label || f.key}
              testId={`filter-int-${f.key}`}
              placeholder="—"
              value={values[f.key] ?? ''}
              onChange={(v) => setV(f.key, v.replace(/[^0-9]/g, ''))}
              width={54}
            />
            <span style={{ marginRight: 10 }} />
          </React.Fragment>
        );
      case 'as_of':
        return (
          <React.Fragment key="as_of">
            <FInput label="as_of_date" testId="filter-as-of" placeholder="YYYY-MM-DD" value={values.as_of_date || ''} onChange={(v) => setV('as_of_date', v)} width={110} />
            <span style={{ marginRight: 10 }} />
          </React.Fragment>
        );
      case 'adjust':
        return (
          <React.Fragment key="adjust">
            <span style={{ fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginRight: 6 }}>adjust</span>
            <div style={{ display: 'inline-flex', border: '1px solid var(--border-strong)', borderRadius: 3, overflow: 'hidden', marginRight: 12 }}>
              {['none', 'pre', 'post'].map((a) => (
                <button
                  key={a}
                  data-testid={`filter-adjust-${a}`}
                  onClick={() => setV('adjust', a)}
                  style={{
                    height: 22, padding: '0 8px',
                    background: (values.adjust || 'none') === a ? 'var(--fg)' : 'transparent',
                    color: (values.adjust || 'none') === a ? 'var(--bg)' : 'var(--fg-2)',
                    border: 'none', borderRight: '1px solid var(--border-strong)',
                    fontFamily: 'var(--font-mono)', fontSize: 10, cursor: 'pointer',
                  }}
                >
                  {a}
                </button>
              ))}
            </div>
          </React.Fragment>
        );
      case 'freq':
        return (
          <React.Fragment key="freq">
            <FSelect label="freq" testId="filter-freq" value={values.frequency || '1m'} options={['1m', '5m', '15m', '30m', '1h']} onChange={(v) => setV('frequency', v)} width={60} />
            <span style={{ marginRight: 10 }} />
          </React.Fragment>
        );
      default:
        return null;
    }
  });
}

// ─── RowLineageDrawer ───────────────────────────────────────────────────────────
function RowLineageDrawer({ tableName, pk, onClose, nav }) {
  const lineageQ = useQuery({
    queryKey: ['row-lineage', tableName, pk],
    queryFn: () => endpoints.tableRowLineage(tableName, pk),
    enabled: !!tableName && !!pk,
  });

  const pkDisplay = Object.entries(pk).map(([k, v]) => `${k}=${v}`).join(', ');
  const isReference = lineageQ.data?.lineage_kind === 'reference';

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
          }}>{isReference ? '字段来源 / PROVENANCE' : 'ROW LINEAGE'}</span>
          <div style={{ flex: 1 }} />
          <button data-testid="lineage-close" className="btn ghost sm" style={{ height: 20 }} onClick={onClose}>✕</button>
        </div>
        <div data-testid="lineage-pk" style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--fg)', marginTop: 4 }}>
          {pkDisplay}
        </div>
        {lineageQ.data && !isReference && (
          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)', marginTop: 2 }}>
            {lineageQ.data.history?.length || 0} 次写入 · {lineageQ.data.current ? '当前生效' : '无生效数据'}
          </div>
        )}
      </div>

      <div className="scroll" style={{ flex: 1, minHeight: 0 }}>
        {lineageQ.isLoading && <Loading />}
        {lineageQ.error && <ErrorState error={lineageQ.error} onRetry={lineageQ.refetch} />}
        {lineageQ.data && isReference && (() => {
          const { current } = lineageQ.data;
          return (
            <>
              <div data-testid="provenance-note" style={{
                margin: 12, padding: '8px 10px',
                border: '1px solid var(--info)', borderRadius: 4, background: 'var(--info-bg)',
                fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--info)',
                display: 'flex', gap: 8, alignItems: 'flex-start',
              }}>
                <span>ⓘ</span>
                <span>此表为可变参考字典（单文件原地 upsert），无批次历史 / 作废链。下方为当前生效值与来源。</span>
              </div>
              {current ? (
                <div data-testid="provenance-current" style={{
                  margin: 12, padding: 12,
                  border: '1px solid var(--fg)', borderRadius: 4, background: 'var(--ok-bg)',
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                    <span style={{ color: 'var(--ok)' }}>●</span>
                    <span style={{ fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--ok)', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 600 }}>当前生效值</span>
                  </div>
                  {current.values && (
                    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 8, fontFamily: 'var(--font-mono)', fontSize: 11 }}>
                      {Object.entries(current.values).map(([k, v]) => (
                        <div key={k}>
                          <div style={{ color: 'var(--fg-3)', fontSize: 9.5, textTransform: 'uppercase', letterSpacing: '0.04em' }}>{k}</div>
                          <div style={{ color: 'var(--fg)', fontWeight: 500, fontSize: 12 }}>{v ?? '·'}</div>
                        </div>
                      ))}
                    </div>
                  )}
                  <div style={{
                    marginTop: 10, padding: '6px 8px', background: 'var(--surface)', borderRadius: 3,
                    fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-2)',
                    display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap',
                  }}>
                    <span><span style={{ color: 'var(--fg-3)' }}>更新时间</span> {absTime(current.updated_at) || '·'}</span>
                    <span style={{ color: 'var(--fg-mute)' }}>│</span>
                    <span><span style={{ color: 'var(--fg-3)' }}>来源</span> {current.source || '·'}</span>
                  </div>
                  {current.extras && Object.keys(current.extras).length > 0 && (
                    <div style={{ marginTop: 8 }}>
                      <div style={{ color: 'var(--warn)', fontSize: 9.5, fontFamily: 'var(--font-mono)', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: 4 }}>◇ _extras</div>
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 5 }}>
                        {Object.entries(current.extras).map(([k, v]) => (
                          <span key={k} className="chip warn" style={{ fontSize: 9.5 }}>{k}={String(v)}</span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <Empty label="未找到该主键对应的记录" />
              )}
            </>
          );
        })()}
        {lineageQ.data && !isReference && (() => {
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
        {!isReference && lineageQ.data?.current?.batch_id && (
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

// per-table empty-state hint (方案 §3.4)
function emptyHintFor(schema) {
  if (!schema) return '调整过滤条件后重试';
  if (schema.name === 'reference.sectors') return '本表无 symbol 维度,请用分类体系 / 层级筛选';
  if (!schema.has_symbol) return '调整筛选条件后重试';
  return '调整过滤条件后重试';
}

// ─── Main TableBrowser ────────────────────────────────────────────────────────
export default function TableBrowser() {
  const { tableName } = useParams();
  const nav = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();

  // View state
  const [viewMode, setViewMode] = useState('current'); // 'current' | 'all' | 'batch'
  const [viewTab, setViewTab] = useState('table');      // 'table' | 'chart'
  const [showInternal, setShowInternal] = useState(true);

  // Schema-driven filter values (keyed by filter key / start / end / adjust …)
  const [values, setValues] = useState({});
  const [initialized, setInitialized] = useState(false);

  // Global (non-table-specific) controls
  const [batchId, setBatchId] = useState('');
  const [source, setSource] = useState('');
  const [limit, setLimit] = useState('200');
  const [extrasAsColumns, setExtrasAsColumns] = useState([]);
  const [hiddenCols, setHiddenCols] = useState(() => new Set());
  const [showColsPanel, setShowColsPanel] = useState(false);

  // Lineage drawer + extras modal
  const [lineageRow, setLineageRow] = useState(null);
  const [extrasModal, setExtrasModal] = useState(null);

  // Schema query
  const schemaQ = useQuery({
    queryKey: ['table-schema', tableName],
    queryFn: () => endpoints.tableSchema(tableName),
    enabled: !!tableName,
  });
  const schema = schemaQ.data;
  const filters = schema?.filters || [];

  // Keep the latest URL params available without re-triggering the init effect.
  const spRef = useRef(searchParams);
  spRef.current = searchParams;

  // (Re)initialise filter values when the table changes: defaults overlaid with
  // any values restored from the URL query (方案 §3.4 持久化).
  useEffect(() => {
    if (!schema) return;
    setInitialized(false);
    const base = {};
    for (const f of schema.filters) Object.assign(base, defaultForFilter(f));
    const sp = spRef.current;
    const restore = (k) => { const v = sp.get(k); if (v != null) base[k] = v; };
    for (const f of schema.filters) {
      if (f.kind === 'symbols') restore('symbols');
      else if (f.kind === 'date_range') { restore('start'); restore('end'); }
      else if (f.kind === 'bool') { const v = sp.get(f.key); if (v != null) base[f.key] = v === 'true'; }
      else restore(f.key);
    }
    setValues(base);
    const vm = sp.get('view');
    setViewMode(schema.mutable ? 'current' : (vm || 'current'));
    setBatchId(sp.get('batch_id') || '');
    setInitialized(true);
    // re-run only when the table identity changes
  }, [schema?.name]); // eslint-disable-line react-hooks/exhaustive-deps

  const setV = useCallback((k, val) => setValues((p) => ({ ...p, [k]: val })), []);

  const isFundamentals = tableName === 'raw.fundamentals';
  const isBarsTable = tableName === 'raw.bars_daily' || tableName === 'raw.bars_minute';

  // Pre-flight: large tables require symbols or a full date window (mirrors the
  // backend _REQUIRE_FILTER guard) — block the request client-side so the user
  // gets a hint instead of a 400 round-trip (方案 §3.4 / C1).
  const hasSymbols = !!(values.symbols && values.symbols.trim());
  const hasWindow = !!(values.start && values.end);
  const filterGate = !!schema?.require_filter && !(hasSymbols || hasWindow);

  // Build query params: only declared filter keys + global controls (方案 §3.1).
  const queryParams = useMemo(() => {
    if (!schema) return {};
    const p = buildFilterParams(filters, values);
    if (source.trim()) p.source = source.trim();
    if (parseInt(limit, 10) > 0) p.limit = parseInt(limit, 10);
    if (extrasAsColumns.length > 0) {
      const valid = extrasAsColumns.filter((r) => r.key.trim());
      if (valid.length > 0) p.extras_as_columns = valid.map((r) => `${r.key}:${r.type}`).join(',');
    }
    if (viewMode === 'batch' && batchId.trim()) p.batch_id = batchId.trim();
    return p;
  }, [schema, filters, values, source, limit, extrasAsColumns, viewMode, batchId]);

  // Sync filter state into the URL so the view can be shared / survives reload.
  useEffect(() => {
    if (!initialized || !schema) return;
    const sp = new URLSearchParams();
    for (const f of filters) {
      if (f.kind === 'date_range') {
        if (values.start) sp.set('start', values.start);
        if (values.end) sp.set('end', values.end);
      } else if (f.kind === 'symbols') {
        if (values.symbols && values.symbols.trim()) sp.set('symbols', values.symbols.trim());
      } else {
        const v = values[f.key];
        if (v !== '' && v != null && v !== false) sp.set(f.key, String(v));
      }
    }
    if (viewMode !== 'current') sp.set('view', viewMode);
    if (viewMode === 'batch' && batchId.trim()) sp.set('batch_id', batchId.trim());
    setSearchParams(sp, { replace: true });
  }, [values, viewMode, batchId, initialized, schema, filters, setSearchParams]);

  // Row data query
  const rowsQueryFn = useCallback(() => {
    if (viewMode === 'current') return endpoints.tableRows(tableName, queryParams);
    return endpoints.tableRowsAll(tableName, queryParams);
  }, [tableName, viewMode, queryParams]);

  const rowsQ = useQuery({
    queryKey: ['table-rows', tableName, viewMode, queryParams],
    queryFn: rowsQueryFn,
    enabled: !!tableName && !!schema && !filterGate && (viewMode !== 'batch' || !!batchId.trim()),
  });

  const buildPk = useCallback((row) => {
    if (!schema?.primary_key) return {};
    return Object.fromEntries(schema.primary_key.map((k) => [k, row[k]]));
  }, [schema]);

  // Business columns (for the column show/hide control)
  const businessCols = useMemo(
    () => (rowsQ.data?.columns || [])
      .map((c) => c.name)
      .filter((n) => n !== '_extras' && !INTERNAL_COLS.has(n)),
    [rowsQ.data?.columns],
  );

  // Build columns dynamically from response
  const tableColumns = useMemo(() => {
    if (!rowsQ.data?.columns) return [];
    const responseCols = rowsQ.data.columns || [];
    const cols = [];
    const pks = schema?.primary_key || [];

    responseCols.forEach(({ name }) => {
      if (name === '_extras') {
        cols.push({
          key: '_extras',
          label: '◇ _extras',
          headColor: 'var(--warn)',
          headBg: 'rgba(216,153,0,0.06)',
          cellBg: 'rgba(216,153,0,0.04)',
          color: 'var(--warn)',
          fontSize: 10,
          mono: true,
          render: (v) => {
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

      // Business column (hidden via the column toggle?)
      if (hiddenCols.has(name)) return;
      const pkIndex = pks.indexOf(name);
      const isPk = pkIndex >= 0;
      cols.push({
        key: name,
        label: name,
        mono: true,
        color: isPk ? 'var(--fg)' : 'var(--fg-2)',
        headColor: isPk ? 'var(--fg-2)' : 'var(--fg-3)',
        align: isPk ? 'left' : 'right',
        fontSize: 11,
        // Pin PK columns to the left so they stay visible while a wide table
        // scrolls horizontally (方案 §3.4 宽表). Offsets stack per PK column.
        sticky: isPk ? { left: pkIndex * 130, width: 130 } : null,
        cellStyle: isPk ? () => ({ fontWeight: 500 }) : undefined,
      });
    });

    cols.push({
      key: '__actions',
      label: '',
      align: 'right',
      render: (_v, row) => (
        <span
          data-testid="row-lineage-link"
          style={{ color: 'var(--info)', fontSize: 10, cursor: 'pointer', userSelect: 'none' }}
          onClick={(e) => { e.stopPropagation(); setLineageRow({ pk: buildPk(row) }); }}
        >
          {schema?.supports_lineage === false ? '来源 →' : 'lineage →'}
        </span>
      ),
    });

    return cols;
  }, [rowsQ.data?.columns, showInternal, schema, buildPk, hiddenCols]);

  const rows = rowsQ.data?.items || [];
  const total = rowsQ.data?.total || 0;

  const rowStyle = useCallback((row) => {
    if (viewMode === 'all' && row._status === 'invalidated') {
      return { opacity: 0.55, textDecoration: 'line-through', color: 'var(--fg-4)' };
    }
    return {};
  }, [viewMode]);

  const firstSymbol = rows[0]?.symbol || '';
  const path = ['data_mgmt/', 'tables/', tableName || ''];

  const isMutable = !!schema?.mutable;
  const activeFilterCount = filters.filter((f) => filterIsActive(f, values)).length;

  const resetFilters = () => {
    const base = {};
    for (const f of filters) Object.assign(base, defaultForFilter(f));
    setValues(base);
    setSource('');
  };

  const fkeys = isMutable
    ? [{ k: 'F1', l: '当前 (参考字典)', onClick: () => setViewMode('current'), active: true }]
    : [
      { k: 'F1', l: '当前生效', onClick: () => setViewMode('current'), active: viewMode === 'current' },
      { k: 'F2', l: '全量(含作废)', onClick: () => setViewMode('all'), active: viewMode === 'all' },
      { k: 'F3', l: '单批次', onClick: () => setViewMode('batch'), active: viewMode === 'batch' },
    ];

  const adjustVal = values.adjust || 'none';
  const freqVal = values.frequency || '1m';

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
          {/* View mode segmented — only for tables with batch lineage */}
          <span style={{ fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em', marginRight: 10 }}>view</span>
          {isMutable ? (
            <div style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginRight: 14 }} title="可变参考字典:单文件原地更新,无批次 / 作废视图">
              <span className="chip info" style={{ fontSize: 9.5, height: 18 }}>reference · 单文件原地更新</span>
            </div>
          ) : (
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
          )}

          <span style={{ color: 'var(--fg-mute)', marginRight: 14 }}>│</span>

          {/* batch_id only in single-batch view */}
          {!isMutable && viewMode === 'batch' && (
            <>
              <FInput
                label="batch_id"
                placeholder="b_2026_…"
                value={batchId}
                onChange={setBatchId}
                width={180}
              />
              <span style={{ marginRight: 10 }} />
            </>
          )}

          {/* Schema-driven, per-table filter controls */}
          {schema && <FilterBar filters={filters} values={values} setV={setV} tableName={tableName} />}

          {/* limit */}
          <FSelect
            label="limit"
            value={limit}
            options={['50', '100', '200', '500', '1000']}
            onChange={setLimit}
            width={60}
          />

          {/* clear filters + active count */}
          <span style={{ marginLeft: 10 }} />
          <button
            className="btn ghost sm"
            data-testid="filter-reset"
            style={{ height: 22, fontSize: 10 }}
            onClick={resetFilters}
          >
            清空筛选{activeFilterCount > 0 ? ` (${activeFilterCount})` : ''}
          </button>

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
            <span style={{ color: 'var(--fg-3)', fontSize: 10 }}>
              {isMutable ? '(_updated_at, _source, …)' : '(_batch_id, _source, …)'}
            </span>
          </label>

          {/* Column show/hide */}
          {businessCols.length > 0 && (
            <div style={{ position: 'relative', display: 'inline-block', marginRight: 10 }}>
              <button
                className="btn ghost sm"
                data-testid="cols-toggle"
                style={{ height: 22, fontSize: 10 }}
                onClick={() => setShowColsPanel((s) => !s)}
              >
                列 {hiddenCols.size > 0 ? `(隐藏 ${hiddenCols.size})` : ''}
              </button>
              {showColsPanel && (
                <div style={{
                  position: 'absolute', top: 26, left: 0, zIndex: 40,
                  background: 'var(--surface)', border: '1px solid var(--border-strong)', borderRadius: 4,
                  boxShadow: '0 8px 24px rgba(20,18,14,0.16)', padding: 8, maxHeight: 280, overflow: 'auto',
                  display: 'flex', flexDirection: 'column', gap: 4, minWidth: 160,
                }}>
                  {businessCols.map((c) => (
                    <label key={c} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 10.5, color: 'var(--fg-2)', cursor: 'pointer' }}>
                      <input
                        type="checkbox"
                        checked={!hiddenCols.has(c)}
                        onChange={(e) => setHiddenCols((prev) => {
                          const next = new Set(prev);
                          if (e.target.checked) next.delete(c); else next.add(c);
                          return next;
                        })}
                        style={{ accentColor: 'var(--fg)' }}
                      />
                      {c}
                    </label>
                  ))}
                </div>
              )}
            </div>
          )}

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
        background: isMutable ? 'var(--info-bg)' : viewMode === 'all' ? 'var(--warn-bg)' : viewMode === 'batch' ? 'var(--info-bg)' : 'var(--ok-bg)',
        borderBottom: '1px solid var(--border)',
        fontFamily: 'var(--font-mono)', fontSize: 10.5,
        color: isMutable ? 'var(--info)' : viewMode === 'all' ? 'var(--warn)' : viewMode === 'batch' ? 'var(--info)' : 'var(--ok)',
        display: 'flex', alignItems: 'center', gap: 8,
        flexShrink: 0,
      }}>
        <span>{isMutable ? 'ⓘ' : viewMode === 'all' ? '⚠' : viewMode === 'batch' ? 'ⓘ' : '●'}</span>
        <span>
          {isMutable && '可变参考字典 · 单文件原地 upsert,无批次历史 / 作废链;行末「来源 →」查看字段来源'}
          {!isMutable && viewMode === 'current' && '当前生效视图 · 已应用作废/替换规则后的最终有效数据'}
          {!isMutable && viewMode === 'all' && '全量视图 · 显示所有历史版本(含已作废),作废行用灰色 + 删除线展示'}
          {!isMutable && viewMode === 'batch' && (batchId ? `单批次视图 · 仅显示 ${batchId} 写入的内容` : '单批次视图 · 请输入 batch_id 进行过滤')}
        </span>
        {isFundamentals && (
          <>
            <span style={{ color: 'var(--ok)' }}>│</span>
            <span>as_of_date: {values.as_of_date || TODAY}</span>
          </>
        )}
      </div>

      {/* Main content + potential drawer */}
      <div style={{ flex: 1, display: 'flex', minHeight: 0, position: 'relative', overflow: 'hidden' }}>
        {viewTab === 'chart' ? (
          <div style={{ flex: 1, minWidth: 0, minHeight: 0, position: 'relative', background: 'var(--surface)' }}>
            <TableQuickChart table={tableName} rows={rows} filters={queryParams} nav={nav} />
          </div>
        ) : (
        <div className="scroll" style={{ flex: 1, minWidth: 0, minHeight: 0 }}>
          {schemaQ.isLoading ? (
            <Loading />
          ) : schemaQ.error ? (
            <ErrorState error={schemaQ.error} onRetry={schemaQ.refetch} />
          ) : filterGate ? (
            <Empty
              label="请先选择 symbols 或日期范围"
              hint={`大表 ${tableName} 需要先缩小范围再浏览,避免全表扫描`}
            />
          ) : !isMutable && viewMode === 'batch' && !batchId.trim() ? (
            <Empty label="请输入 batch_id 以过滤单批次数据" hint="在上方 filter bar 填写 batch_id" />
          ) : rowsQ.isLoading ? (
            <Loading />
          ) : rowsQ.error ? (
            <ErrorState error={rowsQ.error} onRetry={rowsQ.refetch} />
          ) : rows.length === 0 ? (
            <Empty label="暂无数据" hint={emptyHintFor(schema)} />
          ) : (
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
              <thead style={{ position: 'sticky', top: 0, background: 'var(--surface)', zIndex: 3 }}>
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
                      ...(c.sticky ? { position: 'sticky', left: c.sticky.left, zIndex: 4, minWidth: c.sticky.width } : {}),
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
                  // 稳定身份做 key (而非数组下标): 全量/批次视图会按 filters/viewMode/排序
                  // 重排, 下标 key 会让 React 跨不同记录复用 DOM, 命中逐行 hover 样式错位。
                  // 全量视图同一 PK 可有多条物理行 → 追加 _batch_id 保证唯一。
                  const rowKey = row._batch_id != null
                    ? Object.values(rowPk).join('|') + '|' + row._batch_id
                    : Object.values(rowPk).join('|');
                  const rowBg = ri % 2 === 0 ? 'var(--surface)' : 'var(--surface-2)';
                  return (
                    <tr
                      key={rowKey}
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
                        // Sticky PK cells need an opaque background to cover
                        // columns scrolling underneath; match the row's stripe.
                        const stickyStyle = c.sticky
                          ? { position: 'sticky', left: c.sticky.left, zIndex: 1, minWidth: c.sticky.width, background: c.cellBg || rowBg }
                          : {};
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
                              ...stickyStyle,
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
        <span>view: {isMutable ? 'reference (单文件原地更新)' : viewMode}</span>
        {tableName === 'raw.bars_daily' && <><span style={{ color: 'var(--fg-mute)', margin: '0 10px' }}>│</span><span>adjust: {adjustVal}</span></>}
        {tableName === 'raw.bars_minute' && <><span style={{ color: 'var(--fg-mute)', margin: '0 10px' }}>│</span><span>freq: {freqVal}</span></>}
        {isFundamentals && <><span style={{ color: 'var(--fg-mute)', margin: '0 10px' }}>│</span><span>as_of: {values.as_of_date || TODAY}</span></>}
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
        <span style={{ marginLeft: 4 }}>{schema?.supports_lineage === false ? '字段来源' : '行 lineage'}</span>
      </div>

      {/* Extras modal */}
      {extrasModal && <ExtrasModal extrasObj={extrasModal} onClose={() => setExtrasModal(null)} />}
    </TerminalShell>
  );
}
