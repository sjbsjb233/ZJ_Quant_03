// Small shared UI atoms: status chip, copyable id, relative time, empty/loading/error,
// drawer, modal, and the destructive-confirm dialog.
import React, { useState } from 'react';
import { absTime, relTime, statusMeta } from '../../lib/format';

export function StatusChip({ status, size = 11 }) {
  const m = statusMeta(status);
  return <span className={`chip ${m.tone}`} style={{ fontSize: size, height: size + 5 }}>{m.label}</span>;
}

export function StatusGlyph({ status, withLabel = false }) {
  const m = statusMeta(status);
  const col = { ok: 'var(--ok)', err: 'var(--err)', warn: 'var(--warn)', info: 'var(--info)', neutral: 'var(--fg-3)' }[m.tone];
  return (
    <span style={{ color: col, fontFamily: 'var(--font-mono)' }}>
      {m.sym}{withLabel && <span style={{ marginLeft: 4, fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.04em' }}>{m.label}</span>}
    </span>
  );
}

export function CopyableId({ value, length = 8, style }) {
  const [copied, setCopied] = useState(false);
  const short = value && value.length > length ? value.slice(0, length) + '…' : value;
  return (
    <span title={value} onClick={(e) => { e.stopPropagation(); navigator.clipboard?.writeText(value); setCopied(true); setTimeout(() => setCopied(false), 1000); }}
      style={{ fontFamily: 'var(--font-mono)', cursor: 'pointer', ...style }}>
      {short}{copied && <span style={{ color: 'var(--ok)', marginLeft: 4, fontSize: 10 }}>✓</span>}
    </span>
  );
}

export function RelTime({ value }) {
  return <span title={absTime(value)}>{relTime(value)}</span>;
}

export function Loading({ label = '加载中…' }) {
  return <div style={{ padding: 40, textAlign: 'center', color: 'var(--fg-3)', fontFamily: 'var(--font-mono)', fontSize: 12 }}>
    <span className="dot running" style={{ width: 8, height: 8 }} /> {label}
  </div>;
}

export function Empty({ label = '暂无数据', hint }) {
  return <div style={{ padding: 40, textAlign: 'center', color: 'var(--fg-3)', fontSize: 12 }}>
    <div style={{ fontSize: 24, color: 'var(--fg-4)', marginBottom: 8 }}>∅</div>
    <div>{label}</div>
    {hint && <div style={{ fontSize: 11, color: 'var(--fg-4)', marginTop: 6 }}>{hint}</div>}
  </div>;
}

export function ErrorState({ error, onRetry }) {
  const e = error?.apiError || {};
  return (
    <div style={{ padding: 40, textAlign: 'center', color: 'var(--err)', fontSize: 12 }}>
      <div style={{ fontSize: 24, marginBottom: 8 }}>✕</div>
      <div style={{ fontFamily: 'var(--font-mono)' }}>{e.code || 'ERROR'}</div>
      <div style={{ color: 'var(--fg-2)', marginTop: 6 }}>{e.message || String(error?.message || error)}</div>
      {onRetry && <button className="btn sm" style={{ marginTop: 12 }} onClick={onRetry}>重试</button>}
    </div>
  );
}

export function Drawer({ width = 700, onClose, children }) {
  return (
    <>
      <div onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(20,18,14,0.28)', zIndex: 80 }} />
      <aside style={{
        position: 'absolute', top: 0, right: 0, bottom: 0, width, background: 'var(--surface)',
        borderLeft: '1px solid var(--border-strong)', boxShadow: '-12px 0 40px rgba(20,18,14,0.12)',
        zIndex: 81, display: 'flex', flexDirection: 'column', animation: 'slideIn 0.18s ease-out',
      }}>{children}</aside>
    </>
  );
}

export function Modal({ width = 460, onClose, title, children, footer }) {
  return (
    <>
      <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(20,18,14,0.4)', zIndex: 200 }} />
      <div style={{
        position: 'fixed', top: '50%', left: '50%', transform: 'translate(-50%,-50%)', width,
        maxHeight: '86vh', background: 'var(--surface)', border: '1px solid var(--border-strong)',
        borderRadius: 6, zIndex: 201, display: 'flex', flexDirection: 'column',
        boxShadow: '0 20px 60px rgba(20,18,14,0.3)', animation: 'fadeIn 0.15s ease-out',
      }}>
        {title && <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 500 }}>{title}</div>}
        <div className="scroll" style={{ padding: 16, flex: 1, minHeight: 0 }}>{children}</div>
        {footer && <div style={{ padding: '10px 16px', borderTop: '1px solid var(--border)', display: 'flex', gap: 8, justifyContent: 'flex-end', background: 'var(--surface-2)' }}>{footer}</div>}
      </div>
    </>
  );
}

// Destructive confirm requiring the user to type a phrase (vacuum / invalidate style).
export function DangerConfirm({ phrase, title, onCancel, onConfirm, children, confirmLabel = '确认执行', busy }) {
  const [typed, setTyped] = useState('');
  const [checked, setChecked] = useState(!phrase);
  const ok = (!phrase || typed === phrase) && checked && !busy;
  return (
    <Modal width={520} onClose={onCancel} title={title}
      footer={<>
        <button className="btn sm" onClick={onCancel}>取消</button>
        <button className="btn sm danger" disabled={!ok} onClick={onConfirm}>{busy ? '执行中…' : confirmLabel}</button>
      </>}>
      {children}
      <label style={{ display: 'flex', alignItems: 'center', gap: 8, margin: '12px 0', fontSize: 12, color: 'var(--fg-2)' }}>
        <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} style={{ accentColor: 'var(--fg)' }} />
        我已确认这是经过审慎判断的操作
      </label>
      {phrase && (
        <>
          <div style={{ fontSize: 11, color: 'var(--fg-3)', marginBottom: 4 }}>请输入 <b style={{ color: 'var(--err)' }}>{phrase}</b> 以确认:</div>
          <input value={typed} onChange={(e) => setTyped(e.target.value)} placeholder={phrase}
            style={{ width: '100%', height: 30, padding: '0 10px', background: 'var(--surface-2)', border: `1px solid ${typed === phrase ? 'var(--ok)' : 'var(--err)'}`, borderRadius: 3, fontFamily: 'var(--font-mono)', color: 'var(--fg)' }} />
        </>
      )}
    </Modal>
  );
}

export function TableNameCode({ value, onClick }) {
  return <span className="mono" onClick={onClick} style={{ color: onClick ? 'var(--info)' : 'var(--fg)', cursor: onClick ? 'pointer' : 'default', textDecoration: onClick ? 'underline dotted' : 'none' }}>{value}</span>;
}
