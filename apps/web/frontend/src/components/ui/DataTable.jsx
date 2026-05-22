// A dense terminal-style table. columns: [{key,label,align,width,render,mono,headColor}].
import React from 'react';

export function DataTable({ columns, rows, rowKey, rowTestId, onRowClick, rowStyle, height = 28, emptyLabel = '未找到数据' }) {
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
      <thead style={{ position: 'sticky', top: 0, background: 'var(--surface)', zIndex: 1 }}>
        <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
          {columns.map((c, i) => (
            <th key={c.key || i} style={{
              textAlign: c.align || 'left', padding: '6px 10px', fontSize: 9.5, fontWeight: 500,
              color: c.headColor || 'var(--fg-3)', fontFamily: 'var(--font-mono)',
              textTransform: 'uppercase', letterSpacing: '0.05em',
              width: typeof c.width === 'number' ? c.width : undefined,
              background: c.headBg || undefined,
              whiteSpace: 'nowrap',
            }}>{c.label}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.length === 0 && (
          <tr><td colSpan={columns.length} style={{ padding: 30, textAlign: 'center', color: 'var(--fg-3)', fontFamily: 'var(--font-mono)' }}>{emptyLabel}</td></tr>
        )}
        {rows.map((row, ri) => {
          const extra = rowStyle ? rowStyle(row, ri) : {};
          return (
            <tr key={rowKey ? rowKey(row, ri) : ri}
              data-testid={rowTestId ? rowTestId(row, ri) : undefined}
              onClick={onRowClick ? () => onRowClick(row) : undefined}
              style={{
                borderBottom: '1px solid var(--border-faint)', height,
                background: ri % 2 === 0 ? 'transparent' : 'rgba(0,0,0,0.012)',
                cursor: onRowClick ? 'pointer' : 'default', ...extra,
              }}
              onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--hover)'; }}
              onMouseLeave={(e) => { e.currentTarget.style.background = (extra.background) || (ri % 2 === 0 ? 'transparent' : 'rgba(0,0,0,0.012)'); }}>
              {columns.map((c, ci) => {
                const v = c.render ? c.render(row[c.key], row, ri) : row[c.key];
                return (
                  <td key={c.key || ci} style={{
                    padding: '0 10px', textAlign: c.align || 'left',
                    fontFamily: c.mono ? 'var(--font-mono)' : 'inherit',
                    fontVariantNumeric: c.align === 'right' ? 'tabular-nums' : 'normal',
                    color: c.color || 'var(--fg-2)', fontSize: c.fontSize || 11,
                    background: c.cellBg, whiteSpace: c.wrap ? 'normal' : 'nowrap',
                    ...(c.cellStyle ? c.cellStyle(row) : {}),
                  }}>{v == null ? '·' : v}</td>
                );
              })}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
