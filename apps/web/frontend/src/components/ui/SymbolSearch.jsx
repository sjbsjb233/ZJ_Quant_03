// Symbol search dropdown shared by every chart (code + name fuzzy match).
import React, { useEffect, useRef, useState } from 'react';
import { endpoints } from '../../lib/api';

export function SymbolSearch({ value, onChange, width = 240, placeholder = '搜代码 / 名称…' }) {
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState([]);
  const boxRef = useRef(null);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    const t = setTimeout(() => {
      endpoints.symbolSearch(q, 20).then((r) => { if (alive) setItems(r.items || []); }).catch(() => {});
    }, 200);
    return () => { alive = false; clearTimeout(t); };
  }, [q, open]);

  useEffect(() => {
    const h = (e) => { if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  return (
    <div ref={boxRef} style={{ position: 'relative', width }}>
      <div onClick={() => setOpen(true)} style={{
        height: 26, padding: '0 10px', background: 'var(--surface-2)', border: '1px solid var(--border-strong)',
        borderRadius: 3, display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer',
        fontFamily: 'var(--font-mono)', fontSize: 11,
      }}>
        <span style={{ color: 'var(--fg-3)', fontSize: 10 }}>SYMBOL</span>
        <span style={{ color: 'var(--fg)', fontWeight: 600 }}>{value || '—'}</span>
        <div style={{ flex: 1 }} />
        <span style={{ color: 'var(--fg-3)' }}>▾</span>
      </div>
      {open && (
        <div style={{
          position: 'absolute', top: 30, left: 0, width: Math.max(width, 280), zIndex: 50,
          background: 'var(--surface)', border: '1px solid var(--border-strong)', borderRadius: 4,
          boxShadow: '0 8px 24px rgba(20,18,14,0.16)', maxHeight: 320, overflow: 'auto',
        }}>
          <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder={placeholder}
            style={{ width: '100%', height: 30, padding: '0 10px', border: 'none', borderBottom: '1px solid var(--border)', background: 'var(--surface-2)', fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--fg)' }} />
          {items.map((s) => (
            <div key={s.symbol} onClick={() => { onChange(s.symbol, s); setOpen(false); setQ(''); }}
              style={{ padding: '6px 10px', display: 'flex', gap: 8, cursor: 'pointer', fontFamily: 'var(--font-mono)', fontSize: 11, borderBottom: '1px solid var(--border-faint)' }}
              onMouseEnter={(e) => (e.currentTarget.style.background = 'var(--hover)')}
              onMouseLeave={(e) => (e.currentTarget.style.background = '')}>
              <span style={{ color: 'var(--fg)', fontWeight: 500, minWidth: 84 }}>{s.symbol}</span>
              <span style={{ color: 'var(--fg-2)' }}>{s.name}</span>
              <div style={{ flex: 1 }} />
              <span style={{ color: 'var(--fg-4)', fontSize: 10 }}>{s.exchange}</span>
            </div>
          ))}
          {items.length === 0 && <div style={{ padding: 12, color: 'var(--fg-3)', fontSize: 11, textAlign: 'center' }}>无匹配</div>}
        </div>
      )}
    </div>
  );
}

// Multi-select variant for the table browser's symbols filter (方案 §3.2).
// Reuses the same typeahead (reader.read_symbols single-file dictionary, never
// the bars tables). Holds the selection as string[] internally; `value` /
// `onChange` speak the comma-separated wire format the backend already accepts,
// so no backend param changes are needed.
export function SymbolMultiSelect({ value = '', onChange, width = 260, placeholder = '搜代码 / 名称…' }) {
  const selected = (value || '').split(',').map((s) => s.trim()).filter(Boolean);
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState([]);
  const boxRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    let alive = true;
    const t = setTimeout(() => {
      endpoints.symbolSearch(q, 20).then((r) => { if (alive) setItems(r.items || []); }).catch(() => {});
    }, 200);
    return () => { alive = false; clearTimeout(t); };
  }, [q, open]);

  useEffect(() => {
    const h = (e) => { if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false); };
    document.addEventListener('mousedown', h);
    return () => document.removeEventListener('mousedown', h);
  }, []);

  const emit = (arr) => onChange(arr.join(','));
  const add = (sym) => { if (!selected.includes(sym)) emit([...selected, sym]); };
  const remove = (sym) => emit(selected.filter((s) => s !== sym));

  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}>
      <span style={{ color: 'var(--fg-3)', fontSize: 10 }}>symbols:</span>
      <div ref={boxRef} data-testid="symbol-multiselect" style={{ position: 'relative', width }}>
        <div onClick={() => setOpen(true)} style={{
          minHeight: 22, padding: '0 6px', background: 'var(--surface-2)', border: '1px solid var(--border)',
          borderRadius: 3, display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 4, cursor: 'text',
          fontFamily: 'var(--font-mono)', fontSize: 11,
        }}>
          {selected.map((sym) => (
            <span key={sym} className="chip" style={{ fontSize: 10, height: 16, display: 'inline-flex', alignItems: 'center', gap: 4, background: 'var(--surface)', border: '1px solid var(--border)' }}>
              {sym}
              <span data-testid={`symbol-chip-remove-${sym}`} onClick={(e) => { e.stopPropagation(); remove(sym); }} style={{ cursor: 'pointer', color: 'var(--fg-3)' }}>✕</span>
            </span>
          ))}
          <span style={{ flex: 1, minWidth: 56, color: 'var(--fg-4)', fontSize: 10, lineHeight: '20px' }}>
            {selected.length === 0 ? placeholder : ''}
          </span>
          <span style={{ color: 'var(--fg-3)' }}>▾</span>
        </div>
        {open && (
          <div style={{
            position: 'absolute', top: 24, left: 0, width: Math.max(width, 280), zIndex: 50,
            background: 'var(--surface)', border: '1px solid var(--border-strong)', borderRadius: 4,
            boxShadow: '0 8px 24px rgba(20,18,14,0.16)', maxHeight: 320, overflow: 'auto',
          }}>
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder={placeholder}
              style={{ width: '100%', height: 30, padding: '0 10px', border: 'none', borderBottom: '1px solid var(--border)', background: 'var(--surface-2)', fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--fg)' }} />
            {items.map((s) => {
              const on = selected.includes(s.symbol);
              return (
                <div key={s.symbol} data-testid={`symbol-option-${s.symbol}`} onClick={() => { add(s.symbol); setQ(''); }}
                  style={{ padding: '6px 10px', display: 'flex', gap: 8, cursor: 'pointer', fontFamily: 'var(--font-mono)', fontSize: 11, borderBottom: '1px solid var(--border-faint)', background: on ? 'var(--ok-bg)' : '' }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = on ? 'var(--ok-bg)' : 'var(--hover)')}
                  onMouseLeave={(e) => (e.currentTarget.style.background = on ? 'var(--ok-bg)' : '')}>
                  <span style={{ color: on ? 'var(--ok)' : 'var(--fg-3)', minWidth: 12 }}>{on ? '✓' : ''}</span>
                  <span style={{ color: 'var(--fg)', fontWeight: 500, minWidth: 84 }}>{s.symbol}</span>
                  <span style={{ color: 'var(--fg-2)' }}>{s.name}</span>
                  <div style={{ flex: 1 }} />
                  <span style={{ color: 'var(--fg-4)', fontSize: 10 }}>{s.exchange}</span>
                </div>
              );
            })}
            {items.length === 0 && <div style={{ padding: 12, color: 'var(--fg-3)', fontSize: 11, textAlign: 'center' }}>无匹配</div>}
          </div>
        )}
      </div>
    </span>
  );
}
