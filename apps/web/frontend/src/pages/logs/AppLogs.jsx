// 应用日志 (AppLogs) — /data/logs/app · 中枢全量视图 (admin)。
// V3 §4.4: a thin shell over the reusable <LogPanel>; the per-layer pages
// (FactorLogs) reuse the same panel with a locked source.
import React from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { TerminalShell } from '../../components/shell';
import LogPanel from './LogPanel';

export default function AppLogs() {
  const nav = useNavigate();
  const [sp] = useSearchParams();
  return (
    <TerminalShell
      module="data" current="logs"
      path={['data_mgmt/', 'logs/', 'app']}
      fkeys={[
        { k: 'F1', l: '日志中心', onClick: () => nav('/data/logs') },
        { k: 'F2', l: '前端日志', onClick: () => nav('/data/logs/client') },
        { k: 'F3', l: '实时流', onClick: () => nav('/data/logs/stream') },
        { k: 'F4', l: '因子日志', onClick: () => nav('/factors/logs') },
      ]}
      title="应用日志"
      subtitle="logs/app · backend + data + factor + frontend · 中枢全量 (admin)"
      badge={<span className="chip warn" style={{ fontSize: 10 }}>ADMIN</span>}
    >
      <LogPanel
        lockSource={false}
        source={sp.get('source') || ''}
        defaultLevel={sp.get('level') || ''}
        defaultEvent={sp.get('event') || ''}
        defaultTrace={sp.get('trace_id') || ''}
        onTrace={(t) => nav(`/data/logs/trace/${t}`)}
      />
    </TerminalShell>
  );
}
