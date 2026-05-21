// Generic chart explorer: 8-step config panel + ECharts preview + template management
import React, { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { TerminalShell, TPanel, FSelect } from '../../components/shell';
import { EChart, Loading, Empty, ErrorState, Modal } from '../../components/ui';
import { endpoints } from '../../lib/api';

const AGG_OPTIONS = [
  { value: 'none',        label: '无聚合 (none)' },
  { value: 'daily_avg',   label: '日均值' },
  { value: 'weekly_avg',  label: '周均值' },
  { value: 'monthly_avg', label: '月均值' },
  { value: 'sum',         label: '求和 (sum)' },
  { value: 'count',       label: '计数 (count)' },
  { value: 'min',         label: '最小值 (min)' },
  { value: 'max',         label: '最大值 (max)' },
];

const CHART_TYPES = [
  { value: 'line',        label: '折线 line' },
  { value: 'bar',         label: '柱状 bar' },
  { value: 'scatter',     label: '散点 scatter' },
  { value: 'area',        label: '面积 area' },
  { value: 'stacked_bar', label: '堆叠柱 stacked_bar' },
  { value: 'heatmap',     label: '热力图 heatmap' },
  { value: 'pie',         label: '饼图 pie' },
];

// Map explorer chart_type to ECharts series type
function toEChartsType(ct) {
  const m = { line: 'line', bar: 'bar', scatter: 'scatter', area: 'line', stacked_bar: 'bar', heatmap: 'heatmap', pie: 'pie' };
  return m[ct] || 'line';
}

function buildOption(previewData) {
  if (!previewData) return null;
  const { x, series, chart_type } = previewData;
  const eType = toEChartsType(chart_type);

  if (chart_type === 'pie') {
    const s = series[0];
    return {
      tooltip: { trigger: 'item' },
      legend: { orient: 'vertical', left: 'left' },
      series: [{
        type: 'pie',
        data: (x || []).map((name, i) => ({ name, value: s?.data[i] ?? 0 })),
        radius: '60%',
      }],
    };
  }

  return {
    xAxis: { type: 'category', data: x || [], boundaryGap: chart_type === 'bar' || chart_type === 'stacked_bar' },
    yAxis: { type: 'value', scale: true },
    series: (series || []).map((s) => ({
      name: s.name,
      type: eType,
      data: s.data,
      areaStyle: chart_type === 'area' ? { opacity: 0.15 } : undefined,
      stack: chart_type === 'stacked_bar' ? 'total' : undefined,
      smooth: chart_type === 'line' || chart_type === 'area',
    })),
    legend: { show: series?.length > 1, bottom: 0, type: 'scroll', itemWidth: 18, itemHeight: 8 },
    tooltip: { trigger: chart_type === 'scatter' ? 'item' : 'axis', axisPointer: { type: 'shadow' } },
    grid: { left: 48, right: 20, top: 24, bottom: series?.length > 1 ? 52 : 28 },
  };
}

// Step number badge
function StepBadge({ n }) {
  return (
    <span style={{
      width: 18, height: 18, borderRadius: 3,
      background: 'var(--fg)', color: 'var(--bg)',
      display: 'grid', placeItems: 'center',
      fontSize: 10, fontWeight: 600, flexShrink: 0,
    }}>{n}</span>
  );
}

function StepRow({ n, label, children, sub }) {
  return (
    <div style={{
      padding: '10px 14px',
      borderBottom: '1px solid var(--border-faint)',
      fontFamily: 'var(--font-mono)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
        <StepBadge n={n} />
        <span style={{ fontSize: 11, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{label}</span>
      </div>
      <div style={{ paddingLeft: 26 }}>
        {children}
        {sub && <div style={{ fontSize: 9.5, color: 'var(--fg-3)', marginTop: 3 }}>{sub}</div>}
      </div>
    </div>
  );
}

function SelectBox({ value, onChange, options, placeholder }) {
  return (
    <select
      value={value || ''}
      onChange={(e) => onChange && onChange(e.target.value)}
      style={{
        width: '100%', height: 26, padding: '0 8px',
        background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 3,
        fontSize: 11, fontFamily: 'var(--font-mono)', color: value ? 'var(--fg)' : 'var(--fg-3)',
        cursor: 'pointer',
      }}
    >
      {placeholder && <option value="">{placeholder}</option>}
      {options.map((o) => {
        const v = typeof o === 'string' ? o : o.value ?? o.name ?? o;
        const l = typeof o === 'string' ? o : o.label ?? o.name ?? o;
        return <option key={v} value={v}>{l}</option>;
      })}
    </select>
  );
}

export default function ExplorerPage() {
  const qc = useQueryClient();

  // Config state
  const [table, setTable]         = useState('');
  const [filterSymbols, setFilterSymbols] = useState(''); // comma-sep
  const [xAxis, setXAxis]         = useState('');
  const [yAxes, setYAxes]         = useState([]); // multi
  const [groupBy, setGroupBy]     = useState('');
  const [aggregation, setAgg]     = useState('none');
  const [chartType, setChartType] = useState('line');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate]     = useState('');
  const [titleText, setTitleText] = useState('');
  const [saveModalOpen, setSaveModalOpen] = useState(false);
  const [saveName, setSaveName]   = useState('');
  const [showTemplates, setShowTemplates] = useState(false);
  const [previewData, setPreviewData] = useState(null);

  // Available tables
  const tablesQ = useQuery({ queryKey: ['tables'], queryFn: endpoints.tables });
  const tableNames = (tablesQ.data?.items || []).map((t) => t.name);

  // Schema for selected table
  const schemaQ = useQuery({
    queryKey: ['tableSchema', table],
    queryFn: () => endpoints.tableSchema(table),
    enabled: !!table,
  });
  const columns = schemaQ.data?.columns || [];
  const dateOrNumCols = columns.filter((c) => c.type === 'DATE' || c.type === 'TIMESTAMP' || c.type === 'date' || c.type === 'timestamp' || /date|time/i.test(c.type) || /float|int|num|decimal/i.test(c.type));
  const numCols  = columns.filter((c) => /float|int|num|decimal|double|real/i.test(c.type));
  const allCols  = columns.map((c) => c.name);

  // Preview mutation
  const previewMut = useMutation({
    mutationFn: (body) => endpoints.explorerPreview(body),
    onSuccess: (data) => setPreviewData(data),
  });

  // Templates
  const templatesQ = useQuery({ queryKey: ['explorerTemplates'], queryFn: endpoints.explorerTemplates, enabled: showTemplates });

  const saveTemplateMut = useMutation({
    mutationFn: (body) => endpoints.saveTemplate(body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['explorerTemplates'] }); setSaveModalOpen(false); },
  });

  const deleteTemplateMut = useMutation({
    mutationFn: (id) => endpoints.deleteTemplate(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['explorerTemplates'] }),
  });

  function buildConfig() {
    return {
      table,
      x_axis: xAxis,
      y_axes: yAxes,
      ...(groupBy ? { group_by: groupBy } : {}),
      aggregation,
      chart_type: chartType,
      ...(filterSymbols.trim() ? { filters: { symbol: filterSymbols.split(',').map((s) => s.trim()).filter(Boolean) } } : {}),
      ...(startDate ? { filters: { ...((filterSymbols.trim() ? { symbol: filterSymbols.split(',').map((s) => s.trim()).filter(Boolean) } : {})), _start: startDate } } : {}),
      limit: 5000,
    };
  }

  function handleRun() {
    if (!table || !xAxis || yAxes.length === 0) return;
    previewMut.mutate(buildConfig());
  }

  function applyTemplate(t) {
    const cfg = t.config || {};
    if (cfg.table)       setTable(cfg.table);
    if (cfg.x_axis)      setXAxis(cfg.x_axis);
    if (cfg.y_axes)      setYAxes(cfg.y_axes);
    if (cfg.group_by)    setGroupBy(cfg.group_by);
    if (cfg.aggregation) setAgg(cfg.aggregation);
    if (cfg.chart_type)  setChartType(cfg.chart_type);
    setTitleText(t.name || '');
    setShowTemplates(false);
  }

  function toggleY(col) {
    setYAxes((prev) => prev.includes(col) ? prev.filter((c) => c !== col) : [...prev, col]);
  }

  const chartOption = useMemo(() => buildOption(previewData), [previewData]);

  return (
    <TerminalShell
      module="viz"
      current="explorer"
      path={['viz/', 'explorer']}
      fkeys={[
        { k: 'F1', l: '运行', onClick: handleRun },
        { k: 'F2', l: '保存模板', onClick: () => setSaveModalOpen(true) },
        { k: 'F3', l: '加载模板', onClick: () => setShowTemplates((v) => !v) },
      ]}
      title="通用图表浏览器"
      subtitle="explorer · 拖列建图 · 任意聚合 · 模板可保存"
      actions={
        <>
          <button className="btn ghost sm" onClick={() => setShowTemplates((v) => !v)}>加载模板</button>
          <button
            className="btn sm"
            onClick={() => setSaveModalOpen(true)}
            style={{ background: 'var(--fg)', color: 'var(--bg)', borderColor: 'var(--fg)' }}
          >保存为模板</button>
        </>
      }
    >
      <div style={{ flex: 1, display: 'grid', gridTemplateColumns: '320px 1fr', minHeight: 0 }}>
        {/* Left config panel */}
        <div style={{ borderRight: '1px solid var(--border)', overflow: 'auto', background: 'var(--surface-2)' }}>

          {/* Step 1: Table */}
          <StepRow n="1" label="选择表" sub={table ? `已选: ${table}` : '选择一张数据表'}>
            <SelectBox
              value={table}
              onChange={(v) => { setTable(v); setXAxis(''); setYAxes([]); setGroupBy(''); }}
              options={tableNames}
              placeholder="— 选择表 —"
            />
          </StepRow>

          {/* Step 2: Filters */}
          <StepRow n="2" label="行筛选" sub="可选：symbol IN list (逗号分隔)">
            <input
              value={filterSymbols}
              onChange={(e) => setFilterSymbols(e.target.value)}
              placeholder="600519.SH, 000858.SZ …"
              style={{
                width: '100%', height: 26, padding: '0 8px',
                background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 3,
                fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--fg)', boxSizing: 'border-box',
              }}
            />
          </StepRow>

          {/* Step 3: X axis */}
          <StepRow n="3" label="x 轴" sub={xAxis ? `已选: ${xAxis}` : '选择 x 轴列'}>
            <SelectBox
              value={xAxis}
              onChange={setXAxis}
              options={allCols}
              placeholder="— 选择 x 轴 —"
            />
          </StepRow>

          {/* Step 4: Y axes (multi-select) */}
          <StepRow n="4" label="y 轴" sub={yAxes.length ? `已选: ${yAxes.join(', ')}` : '选择一或多个数值列'}>
            <div style={{ maxHeight: 120, overflow: 'auto', display: 'flex', flexDirection: 'column', gap: 2 }}>
              {(numCols.length ? numCols : columns).map((c) => (
                <label key={c.name} style={{
                  display: 'inline-flex', alignItems: 'center', gap: 6,
                  fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-2)', cursor: 'pointer',
                }}>
                  <input
                    type="checkbox"
                    checked={yAxes.includes(c.name)}
                    onChange={() => toggleY(c.name)}
                  />
                  <span style={{ color: 'var(--fg)' }}>{c.name}</span>
                  <span style={{ color: 'var(--fg-4)', fontSize: 9.5 }}>{c.type}</span>
                </label>
              ))}
              {columns.length === 0 && (
                <div style={{ color: 'var(--fg-4)', fontSize: 10.5 }}>先选择表</div>
              )}
            </div>
          </StepRow>

          {/* Step 5: Group by */}
          <StepRow n="5" label="分组" sub={groupBy || '可选'}>
            <SelectBox
              value={groupBy}
              onChange={setGroupBy}
              options={allCols}
              placeholder="— 不分组 —"
            />
          </StepRow>

          {/* Step 6: Aggregation */}
          <StepRow n="6" label="聚合方式" sub="">
            <SelectBox value={aggregation} onChange={setAgg} options={AGG_OPTIONS} />
          </StepRow>

          {/* Step 7: Chart type */}
          <StepRow n="7" label="图表类型" sub="">
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
              {CHART_TYPES.map((ct) => (
                <button
                  key={ct.value}
                  onClick={() => setChartType(ct.value)}
                  style={{
                    height: 24, padding: '0 8px',
                    background: chartType === ct.value ? 'var(--fg)' : 'var(--surface)',
                    color: chartType === ct.value ? 'var(--bg)' : 'var(--fg-2)',
                    border: '1px solid var(--border)', borderRadius: 3,
                    fontFamily: 'var(--font-mono)', fontSize: 10, cursor: 'pointer',
                  }}
                >{ct.label}</button>
              ))}
            </div>
          </StepRow>

          {/* Step 8: Time range */}
          <StepRow n="8" label="时间范围" sub="可选">
            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
              <input
                type="date"
                value={startDate}
                onChange={(e) => setStartDate(e.target.value)}
                style={{
                  flex: 1, height: 24, padding: '0 6px',
                  background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 3,
                  fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg)',
                }}
              />
              <span style={{ color: 'var(--fg-3)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>→</span>
              <input
                type="date"
                value={endDate}
                onChange={(e) => setEndDate(e.target.value)}
                style={{
                  flex: 1, height: 24, padding: '0 6px',
                  background: 'var(--surface)', border: '1px solid var(--border)', borderRadius: 3,
                  fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg)',
                }}
              />
            </div>
          </StepRow>

          {/* Run button */}
          <div style={{ padding: '12px 14px', display: 'flex', gap: 6 }}>
            <button className="btn ghost sm" style={{ flex: 1 }} onClick={handleRun}>预览</button>
            <button
              className="btn sm"
              style={{ flex: 1, background: 'var(--fg)', color: 'var(--bg)', borderColor: 'var(--fg)' }}
              disabled={!table || !xAxis || yAxes.length === 0 || previewMut.isPending}
              onClick={handleRun}
            >
              {previewMut.isPending ? '运行中…' : '运行 ⌘↵'}
            </button>
          </div>

          {/* Template list (collapsible) */}
          {showTemplates && (
            <div style={{ borderTop: '1px solid var(--border)' }}>
              <div style={{
                padding: '6px 14px', background: 'var(--surface)',
                fontFamily: 'var(--font-mono)', fontSize: 9.5, color: 'var(--fg-3)',
                textTransform: 'uppercase', letterSpacing: '0.06em',
                borderBottom: '1px solid var(--border)',
              }}>TEMPLATES</div>
              {templatesQ.isLoading ? (
                <div style={{ padding: 12, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-3)' }}>加载中…</div>
              ) : (templatesQ.data?.items || []).map((t) => (
                <div key={t.id} style={{
                  padding: '8px 14px', borderBottom: '1px solid var(--border-faint)',
                  display: 'flex', alignItems: 'center', gap: 6,
                  fontFamily: 'var(--font-mono)', fontSize: 11,
                }}>
                  <span
                    style={{ flex: 1, color: 'var(--fg)', cursor: 'pointer' }}
                    onClick={() => applyTemplate(t)}
                  >{t.name}</span>
                  <span style={{ fontSize: 9.5, color: 'var(--fg-3)' }}>{t.chart_type}</span>
                  <button
                    className="btn ghost sm"
                    style={{ fontSize: 10, height: 20 }}
                    onClick={() => deleteTemplateMut.mutate(t.id)}
                  >✕</button>
                </div>
              ))}
              {(templatesQ.data?.items || []).length === 0 && !templatesQ.isLoading && (
                <div style={{ padding: 12, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-3)', textAlign: 'center' }}>暂无模板</div>
              )}
            </div>
          )}
        </div>

        {/* Right: chart preview */}
        <div style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          {/* Title bar */}
          <div style={{
            padding: '8px 14px', borderBottom: '1px solid var(--border)',
            display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0,
          }}>
            <input
              value={titleText}
              onChange={(e) => setTitleText(e.target.value)}
              placeholder="图表标题…"
              style={{
                flex: 1, height: 26, padding: '0 10px',
                background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3,
                fontFamily: 'var(--font-mono)', fontSize: 13, color: 'var(--fg)',
              }}
            />
            <button className="btn ghost sm">PNG</button>
            <button className="btn ghost sm">CSV</button>
            <button className="btn ghost sm">⇲</button>
          </div>

          {/* Chart */}
          <div style={{ flex: 1.4, minHeight: 0, padding: 14, background: 'var(--surface)', position: 'relative' }}>
            {previewMut.isPending ? (
              <Loading />
            ) : previewMut.error ? (
              <ErrorState error={previewMut.error} onRetry={handleRun} />
            ) : !chartOption ? (
              <Empty label="配置左侧参数后点击运行" />
            ) : (
              <EChart option={chartOption} height="100%" />
            )}
          </div>

          {/* Underlying data preview */}
          <div style={{ flex: 1, minHeight: 0, borderTop: '1px solid var(--border-strong)', overflow: 'auto' }}>
            <TPanel
              title="UNDERLYING DATA · PREVIEW"
              meta={previewData ? `row_count: ${previewData.row_count}` : '—'}
              last
              noBorderBottom
            >
              {previewData && previewData.x ? (
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
                  <thead style={{ position: 'sticky', top: 0, background: 'var(--surface)' }}>
                    <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
                      <th style={{
                        textAlign: 'left', padding: '6px 10px', fontSize: 9.5,
                        color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase',
                      }}>{xAxis || 'x'}</th>
                      {(previewData.series || []).map((s) => (
                        <th key={s.name} style={{
                          textAlign: 'right', padding: '6px 10px', fontSize: 9.5,
                          color: 'var(--fg-3)', fontWeight: 500, textTransform: 'uppercase', whiteSpace: 'nowrap',
                        }}>{s.name}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {(previewData.x || []).slice(0, 12).map((xVal, i) => (
                      <tr key={i} style={{ borderBottom: '1px solid var(--border-faint)' }}>
                        <td style={{ padding: '5px 10px', color: 'var(--fg-2)' }}>{xVal}</td>
                        {(previewData.series || []).map((s) => (
                          <td key={s.name} style={{
                            padding: '5px 10px', textAlign: 'right', color: 'var(--fg)',
                            fontVariantNumeric: 'tabular-nums',
                          }}>
                            {s.data[i] !== null && s.data[i] !== undefined ? String(s.data[i]) : '—'}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <div style={{ padding: 20, fontFamily: 'var(--font-mono)', fontSize: 11, color: 'var(--fg-3)', textAlign: 'center' }}>
                  运行预览后在此处查看原始数据
                </div>
              )}
            </TPanel>
          </div>
        </div>
      </div>

      {/* Save template modal */}
      {saveModalOpen && (
        <Modal title="保存为模板" onClose={() => setSaveModalOpen(false)}>
          <div style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 14, minWidth: 320 }}>
            <div>
              <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)', marginBottom: 6 }}>模板名称</div>
              <input
                autoFocus
                value={saveName}
                onChange={(e) => setSaveName(e.target.value)}
                placeholder="例：白酒 ROE 季度折线"
                style={{
                  width: '100%', height: 28, padding: '0 8px',
                  background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3,
                  fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--fg)', boxSizing: 'border-box',
                }}
              />
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button className="btn ghost sm" onClick={() => setSaveModalOpen(false)}>取消</button>
              <button
                className="btn sm"
                disabled={!saveName.trim() || saveTemplateMut.isPending}
                onClick={() => saveTemplateMut.mutate({ name: saveName.trim(), chart_type: chartType, config: buildConfig() })}
                style={{ background: 'var(--fg)', color: 'var(--bg)', borderColor: 'var(--fg)' }}
              >
                {saveTemplateMut.isPending ? '保存中…' : '保存'}
              </button>
            </div>
          </div>
        </Modal>
      )}
    </TerminalShell>
  );
}
