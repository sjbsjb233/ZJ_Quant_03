// 404 / 403 / 500 terminal error scenes.
import React from 'react';
import { useNavigate } from 'react-router-dom';
import { ModuleStrip, StatusBar } from '../components/shell';
import { getLastTraceId } from '../lib/logger';

function ErrorScene({ code, title, body, actions, extra }) {
  return (
    <div style={{ width: '100%', height: '100%', background: 'var(--bg)', color: 'var(--fg)', display: 'flex', flexDirection: 'column', overflow: 'hidden', fontFamily: 'var(--font-mono)' }}>
      <ModuleStrip current="data" />
      <div style={{ flex: 1, display: 'grid', placeItems: 'center', padding: 24, minHeight: 0 }}>
        <div style={{ width: 560 }}>
          <div style={{ fontSize: 11, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: '0.12em' }}>{code}</div>
          <div style={{ fontSize: 80, lineHeight: 1, color: 'var(--fg)', fontWeight: 500, letterSpacing: '-0.04em', marginTop: 8 }}>{code.split(' ')[0]}</div>
          <div style={{ fontSize: 22, fontWeight: 500, marginTop: 14 }}>{title}</div>
          <p style={{ fontSize: 12.5, color: 'var(--fg-2)', lineHeight: 1.6, marginTop: 8, maxWidth: 460 }}>{body}</p>
          {extra}
          <div style={{ display: 'flex', gap: 8, marginTop: 18 }}>{actions}</div>
        </div>
      </div>
      <StatusBar />
    </div>
  );
}

export function Page404() {
  const nav = useNavigate();
  return <ErrorScene code="404 NOT_FOUND" title="该页面不存在" body="你访问的路径可能已重命名或被删除。"
    actions={<><button className="btn sm primary" onClick={() => nav(-1)}>← 返回上一页</button><button className="btn sm" onClick={() => nav('/data/dashboard')}>返回首页</button></>} />;
}
export function Page403() {
  const nav = useNavigate();
  return <ErrorScene code="403 FORBIDDEN" title="无权访问" body="当前角色不能访问该页面。如认为是误判,请联系管理员申请权限。"
    actions={<><button className="btn sm primary" onClick={() => nav(-1)}>← 返回</button><button className="btn sm" onClick={() => nav('/data/dashboard')}>返回首页</button></>} />;
}
export function Page500({ boundary = false }) {
  const nav = useNavigate();
  const traceId = getLastTraceId();
  const body = boundary
    ? '页面渲染时发生了错误。已自动上报到日志中心,可凭 trace_id 排查。'
    : '后端服务返回了意外错误。如反复出现请联系运维。';
  return <ErrorScene code="500 SERVER_ERROR" title="系统暂时不可用" body={body}
    extra={traceId && (
      <div style={{ marginTop: 14, fontSize: 12, color: 'var(--fg-3)' }}>
        trace_id:{' '}
        <span className="mono" style={{ color: 'var(--fg)', cursor: 'pointer' }}
          title="点击复制" onClick={() => navigator.clipboard?.writeText(traceId)}>{traceId} ⎘</span>
      </div>
    )}
    actions={<>
      <button className="btn sm primary" onClick={() => location.reload()}>↻ 重试</button>
      <button className="btn sm" onClick={() => nav('/data/dashboard')}>返回首页</button>
      {traceId && <button className="btn sm" onClick={() => nav(`/data/logs/trace/${traceId}`)}>在日志中心查看 →</button>}
    </>} />;
}
