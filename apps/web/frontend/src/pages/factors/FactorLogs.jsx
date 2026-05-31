// 因子日志 (FactorLogs) — /factors/logs · 因子层本层视图 (V3 §4.4)。
// Reuses <LogPanel> with a locked source="factor"; the backend narrows ?source=
// by role (editor+ may see the factor layer), so this is the per-layer view the
// factor team gets without ever touching other layers. Row → 看整条 trace 仍跳
// 全链路视图 (/data/logs/trace/{id}) so cross-layer 排障 is one click away.
import React from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { TerminalShell } from '../../components/shell';
import LogPanel from '../logs/LogPanel';

export default function FactorLogs() {
  const nav = useNavigate();
  const [sp] = useSearchParams();
  return (
    <TerminalShell
      module="factor" current="logs"
      path={['factor_mgmt/', 'logs']}
      fkeys={[
        { k: 'F1', l: '因子库', onClick: () => nav('/factors/library') },
        { k: 'F2', l: '任务队列', onClick: () => nav('/factors/tasks') },
        { k: 'F3', l: '全链路 trace', onClick: () => nav('/data/logs') },
      ]}
      title="因子日志"
      subtitle="logs · source=factor · 本层视图 (run.start → stage → done)"
      badge={<span className="chip" style={{ fontSize: 10, color: 'var(--success, var(--accent))' }}>FACTOR</span>}
    >
      <LogPanel
        source="factor"
        lockSource
        defaultLevel={sp.get('level') || ''}
        defaultEvent={sp.get('event') || ''}
        defaultTrace={sp.get('trace_id') || ''}
        onTrace={(t) => nav(`/data/logs/trace/${t}`)}
      />
    </TerminalShell>
  );
}
