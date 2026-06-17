// /factors/import — upload + directory scan + pending-version review.
// Editor+ only (gated via App.jsx).
import React from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { endpoints } from '../../lib/api';
import { TerminalShell, TPanel, KpiStrip } from '../../components/shell';
import { Empty, Loading } from '../../components/ui';
import { MiniEmpty, MiniLoading } from './shared';

export default function FactorImport() {
  const qc = useQueryClient();
  const [tab, setTab] = React.useState('upload');
  const pendingQ = useQuery({
    queryKey: ['imports.pending'],
    queryFn: () => endpoints.pendingImports(),
  });
  const pending = pendingQ.data?.items || [];
  const warnCount = pending.filter((v) => Object.values(v.validation_report?.summary || {}).find?.((s) => false) || (v.validation_report?.summary?.warn ?? 0) > 0).length;

  return (
    <TerminalShell
      module="factor" current="import"
      path={['factor_mgmt/', 'import']}
      title="因子导入"
      subtitle={`import · 2 路径 · ${pending.length} 待审核`}
      badge={<span className="chip outline" style={{ fontSize: 10 }}>编辑者及以上</span>}
      actions={
        <button className="btn ghost sm" onClick={() => pendingQ.refetch()}>刷新</button>
      }
    >
      <KpiStrip cols={6} cells={[
        { label: '待审核版本', value: pending.length, sub: '需要 review', tone: pending.length ? 'warn' : 'ok' },
        { label: '今日导入', value: '—', sub: '从日志统计' },
        { label: '校验通过率', value: '—', sub: 'last 30d', tone: 'ok' },
        { label: '上传', value: '—', sub: 'this month' },
        { label: '目录扫描', value: '—', sub: 'this month' },
        { label: '默认未启用', value: 'YES', sub: '需手动确认', tone: 'info' },
      ]} />

      <div style={{ flex: 1, display: 'flex', minHeight: 0 }}>
        <div style={{ flex: 1, minWidth: 0, borderRight: '1px solid var(--border)', display: 'flex', flexDirection: 'column' }}>
          <div style={{ flexShrink: 0 }}>
            <UploadCard />
          </div>
          <div style={{ flex: 1, minHeight: 0 }}>
            <ScanCard />
          </div>
        </div>
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <PendingList items={pending} loading={pendingQ.isLoading}
            onEnable={(fid, ver) => endpoints.enableVersion(fid, ver).then(() => qc.invalidateQueries({ queryKey: ['imports.pending'] }))} />
        </div>
      </div>
    </TerminalShell>
  );
}

// ─── A. Web upload ──────────────────────────────────────────────────────
function UploadCard() {
  const qc = useQueryClient();
  const [pyFile, setPyFile] = React.useState(null);
  const [readmeFile, setReadmeFile] = React.useState(null);
  const [source, setSource] = React.useState('');
  const [report, setReport] = React.useState(null);

  const onPyChange = (f) => {
    setPyFile(f);
    if (f) f.text().then(setSource);
  };

  const validate = useMutation({
    mutationFn: () => endpoints.validateFactor({ source }),
    onSuccess: setReport,
  });
  const upload = useMutation({
    mutationFn: () => {
      const fd = new FormData();
      fd.append('factor_py', pyFile);
      if (readmeFile) fd.append('readme_md', readmeFile);
      return endpoints.uploadFactor(fd);
    },
    onSuccess: () => {
      setPyFile(null); setReadmeFile(null); setSource(''); setReport(null);
      qc.invalidateQueries({ queryKey: ['imports.pending'] });
    },
  });

  return (
    <TPanel title="A · 网页上传" meta="拖拽 .py 与 README · 校验通过后才能上传" last>
      <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div data-testid="import-upload-dropzone" style={{
          border: '1.5px dashed var(--border-strong)', borderRadius: 4, padding: '20px 18px',
          background: 'var(--surface-2)', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12,
        }}>
          <FilePicker label="因子代码 (*.py)" sub=".py 单文件 · class extends Factor"
            file={pyFile} onChange={onPyChange} accept=".py" />
          <FilePicker label="说明文件 (README.md, 可选)" sub="人读说明"
            file={readmeFile} onChange={setReadmeFile} accept=".md,.markdown" />
        </div>

        {source && (
          <button className="btn sm" onClick={() => validate.mutate()} disabled={validate.isPending}
            style={{ alignSelf: 'flex-start' }}>
            {validate.isPending ? '校验中…' : '🔍 校验'}
          </button>
        )}

        {report && <ValidationReport report={report} />}

        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>
            {report?.ok ? '校验通过 · 可上传为待审核版本 (默认未启用)' : '请先通过校验'}
          </span>
          <div style={{ flex: 1 }} />
          <button className="btn sm" disabled={!report?.ok || upload.isPending || !pyFile}
            onClick={() => upload.mutate()}
            style={{ background: 'var(--fg)', color: 'var(--bg)', borderColor: 'var(--fg)' }}>
            {upload.isPending ? '上传中…' : '上传为待审核版本'}
          </button>
        </div>
      </div>
    </TPanel>
  );
}

function FilePicker({ label, sub, file, onChange, accept }) {
  const ref = React.useRef();
  return (
    <div onClick={() => ref.current?.click()} style={{
      padding: '12px 14px', background: 'var(--surface)', border: '1px solid var(--border)',
      borderRadius: 4, display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer',
    }}>
      <div style={{
        width: 28, height: 32, background: 'var(--surface-3)', border: '1px solid var(--border)',
        borderRadius: 2, display: 'grid', placeItems: 'center',
        fontFamily: 'var(--font-mono)', fontSize: 9, color: 'var(--fg-3)',
      }}>{accept.includes('.py') ? '.py' : 'MD'}</div>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, fontWeight: 500, color: 'var(--fg)' }}>{file?.name || label}</div>
        <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>{file ? `${(file.size / 1024).toFixed(1)} KB` : sub}</div>
      </div>
      {file && <span style={{ color: 'var(--ok)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>✓</span>}
      <input ref={ref} type="file" accept={accept} hidden onChange={(e) => onChange(e.target.files?.[0] || null)} />
    </div>
  );
}

function ValidationReport({ report }) {
  if (!report) return null;
  const { checks = [], summary = {} } = report;
  const hasWarn = summary.warn > 0;
  const hasFail = summary.fail > 0;
  const tone = hasFail ? 'err' : hasWarn ? 'warn' : 'ok';
  return (
    <div data-testid="import-validate-report" style={{ border: '1px solid var(--border)', borderRadius: 4 }}>
      <div style={{
        padding: '8px 14px', background: 'var(--surface-2)', borderBottom: '1px solid var(--border)',
        display: 'flex', alignItems: 'center', gap: 10,
      }}>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg)', fontWeight: 500, textTransform: 'uppercase', letterSpacing: '0.06em' }}>校验报告</span>
        <span className={`chip ${tone}`} style={{ height: 16, fontSize: 9.5 }}>
          {hasFail ? `${summary.fail} 失败` : hasWarn ? `${summary.warn} 警告` : '全部通过'}
        </span>
        <span style={{ fontFamily: 'var(--font-mono)', fontSize: 10, color: 'var(--fg-3)' }}>
          · 类 = {report.class_name || '?'} · 参数 {Object.keys(report.param_schema || {}).length} · 依赖 {report.dependencies?.length || 0}
        </span>
      </div>
      <div>
        {checks.map((r, i) => (
          <div key={i} data-testid={`import-check-${r.id}`} style={{
            padding: '7px 14px', borderBottom: '1px solid var(--border-faint)',
            display: 'flex', alignItems: 'center', gap: 12,
            fontFamily: 'var(--font-mono)', fontSize: 11,
            background: r.status === 'warn' ? 'var(--warn-bg)' : r.status === 'fail' ? 'var(--err-bg)' : 'transparent',
          }}>
            <span style={{ color: r.status === 'pass' ? 'var(--ok)' : r.status === 'warn' ? 'var(--warn)' : 'var(--err)', width: 14 }}>
              {r.status === 'pass' ? '✓' : r.status === 'warn' ? '⚠' : '✕'}
            </span>
            <span style={{ flex: 1, color: 'var(--fg)' }}>{r.label}</span>
            <span style={{ color: 'var(--fg-3)', fontSize: 10.5 }}>{r.detail}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── B. Directory scan ─────────────────────────────────────────────────
function ScanCard() {
  const [path, setPath] = React.useState('');
  const [scanResult, setScanResult] = React.useState(null);
  const scan = useMutation({
    mutationFn: () => endpoints.scanInbox(path ? { path } : {}),
    onSuccess: setScanResult,
  });
  return (
    <TPanel title="B · 目录导入" meta="factor-store/inbox · 扫描后批量自动导入" last
      action={
        <>
          <input value={path} onChange={(e) => setPath(e.target.value)} placeholder="路径 (留空 = 默认)"
            style={{ width: 220, height: 22, padding: '0 7px', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3, fontFamily: 'var(--font-mono)', fontSize: 11, marginRight: 8 }} />
          <button className="btn ghost sm" onClick={() => scan.mutate()} style={{ fontSize: 10 }}>{scan.isPending ? '扫描中…' : '⟳ 扫描'}</button>
        </>
      }>
      {!scanResult && <MiniEmpty label="点「⟳ 扫描」开始" />}
      {scanResult && (
        <div>
          <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--border)', fontFamily: 'var(--font-mono)', fontSize: 10.5 }}>
            <span style={{ color: 'var(--fg-3)' }}>路径:</span> <span style={{ color: 'var(--fg)' }}>{scanResult.path}</span>
            <span style={{ color: 'var(--fg-mute)', margin: '0 8px' }}>│</span>
            <span style={{ color: 'var(--ok)' }}>已注册 {scanResult.registered}</span>
            <span style={{ color: 'var(--fg-mute)', margin: '0 8px' }}>│</span>
            <span>共发现 {scanResult.items?.length || 0}</span>
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border-strong)' }}>
                {['file', 'class', 'deps', 'check'].map((c) => (
                  <th key={c} style={{ textAlign: 'left', padding: '6px 12px', fontSize: 9.5, color: 'var(--fg-3)', textTransform: 'uppercase' }}>{c}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {scanResult.items?.map((it, i) => (
                <tr key={i} style={{
                  borderBottom: '1px solid var(--border-faint)', height: 26,
                  background: it.summary?.fail ? 'var(--err-bg)' : it.summary?.warn ? 'var(--warn-bg)' : 'transparent',
                }}>
                  <td style={{ padding: '0 12px', color: 'var(--fg)' }}>{it.file}</td>
                  <td style={{ padding: '0 12px', color: 'var(--fg-2)' }}>{it.class_name || '—'}</td>
                  <td style={{ padding: '0 12px', color: 'var(--fg-3)' }}>{(it.dependencies || []).join(', ')}</td>
                  <td style={{ padding: '0 12px' }}>
                    <span style={{ color: it.ok ? 'var(--ok)' : 'var(--err)' }}>
                      {it.ok ? '✓ 通过' : it.summary?.fail ? '✕ 失败' : '⚠ 警告'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </TPanel>
  );
}

// ─── C. Pending versions ────────────────────────────────────────────────
function PendingList({ items, loading, onEnable }) {
  const [openId, setOpenId] = React.useState(null);
  return (
    <TPanel title="C · 待审核版本" meta={`${items.length} 个 · 上传 / 扫描成功 · 需 review + 启用`} last>
      {loading && <MiniLoading />}
      {!loading && items.length === 0 && (
        <div style={{ padding: '14px 16px', color: 'var(--fg-3)', fontFamily: 'var(--font-mono)', fontSize: 10.5 }}>
          暂无待审核版本
        </div>
      )}
      {items.map((v) => {
        const open = `${v.factor_id}@${v.version}` === openId;
        const hasWarn = v.validation_report?.summary?.warn > 0;
        return (
          <div key={`${v.factor_id}@${v.version}`} data-testid={`import-pending-${v.factor_id}-${v.version}`} style={{ borderBottom: '1px solid var(--border)' }}>
            <div onClick={() => setOpenId(open ? null : `${v.factor_id}@${v.version}`)} style={{
              padding: '12px 16px', cursor: 'pointer', background: open ? 'var(--surface-2)' : 'transparent',
              display: 'grid', gridTemplateColumns: '1fr auto', gap: 10,
            }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                  <span style={{ color: 'var(--fg-3)', fontFamily: 'var(--font-mono)', fontSize: 10 }}>{open ? '▾' : '▸'}</span>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 500, color: 'var(--fg)' }}>{v.factor_id}@{v.version}</span>
                  <span style={{ color: 'var(--fg-3)', fontFamily: 'var(--font-mono)', fontSize: 10 }}>· {v.class_name}</span>
                  <span className="chip outline" style={{ height: 16, fontSize: 9.5 }}>{v.source}</span>
                  {hasWarn && <span className="chip warn" style={{ height: 16, fontSize: 9.5 }}>⚠ 警告</span>}
                  {!hasWarn && <span className="chip ok" style={{ height: 16, fontSize: 9.5 }}>✓ 校验通过</span>}
                  <span className="chip neutral" style={{ height: 16, fontSize: 9.5 }}>未启用</span>
                </div>
                <div style={{ fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-3)' }}>
                  {v.owner || '—'} · {v.uploaded_at?.slice(0, 16)?.replace('T', ' ')} · 依赖 {v.dependencies?.join(', ') || '—'}
                  {v.requires_pit && <span style={{ color: 'var(--warn)' }}> · 需要财报</span>}
                </div>
              </div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <button className="btn sm" data-testid={`import-pending-${v.factor_id}-${v.version}-enable`}
                  onClick={(e) => { e.stopPropagation(); onEnable(v.factor_id, v.version); }}
                  style={{ fontSize: 10, background: 'var(--ok)', color: '#fff', borderColor: 'var(--ok)' }}>✓ 启用</button>
              </div>
            </div>
            {open && (
              <div style={{ borderTop: '1px solid var(--border-faint)', background: 'var(--surface-2)' }}>
                {v.validation_report?.checks && (
                  <div style={{ padding: '8px 16px', display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 8, borderBottom: '1px solid var(--border-faint)' }}>
                    {v.validation_report.checks.map((c, i) => (
                      <div key={i} style={{ fontFamily: 'var(--font-mono)', fontSize: 10 }}>
                        <div style={{ color: c.status === 'pass' ? 'var(--ok)' : c.status === 'warn' ? 'var(--warn)' : 'var(--err)' }}>
                          {c.status === 'pass' ? '✓' : c.status === 'warn' ? '⚠' : '✕'} {c.status}
                        </div>
                        <div style={{ color: 'var(--fg-3)', fontSize: 9.5 }}>{c.label}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
      <div style={{
        padding: '10px 16px', borderTop: '1px solid var(--border)', background: 'var(--warn-bg)',
        fontFamily: 'var(--font-mono)', fontSize: 10.5, color: 'var(--fg-2)', lineHeight: 1.55,
      }}>
        <div style={{ color: 'var(--warn)', fontWeight: 500, marginBottom: 4 }}>⚠ 安全提示</div>
        上传的因子代码默认 <span style={{ color: 'var(--fg)' }}>不启用</span> · 必须由编辑者审查代码后手动「启用」才会生效。这是有意的人工把关。
      </div>
    </TPanel>
  );
}
