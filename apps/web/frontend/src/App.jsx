import React from 'react';
import { Navigate, Route, Routes, useLocation, useParams } from 'react-router-dom';
import { useAuth } from './lib/auth';
import { Loading } from './components/ui';

import Login from './pages/Login';
import Dashboard from './pages/Dashboard';
import Batches from './pages/Batches';
import BatchDetail from './pages/BatchDetail';
import Sources from './pages/Sources';
import SourceDetail from './pages/SourceDetail';
import SourcesDiff from './pages/SourcesDiff';
import Fields from './pages/Fields';
import Tables from './pages/Tables';
import TableBrowser from './pages/TableBrowser';
import Quality from './pages/Quality';
import Maintenance from './pages/Maintenance';
import Audit from './pages/Audit';
import Settings from './pages/Settings';
import ChartsHub from './pages/charts/ChartsHub';
import Market from './pages/charts/Market';
import Industry from './pages/charts/Industry';
import Kline from './pages/charts/Kline';
import Fundamentals from './pages/charts/Fundamentals';
import Explorer from './pages/charts/Explorer';
import Placeholder from './pages/Placeholder';
import { Page403, Page404, Page500 } from './pages/errors';
// 「可观测」模块 (可观测层重构方案 §7): 合并旧日志中心/因子日志 + 监控/外部观察。
// ClientLogs / LogStream / TraceDetail 沿用 (已切到 module="observ")。
import ClientLogs from './pages/logs/ClientLogs';
import LogStream from './pages/logs/LogStream';
import TraceDetail from './pages/logs/TraceDetail';
import ObsOverview from './pages/observability/ObsOverview';
import Alerts from './pages/observability/Alerts';
import Topology from './pages/observability/Topology';
import ObsLogs from './pages/observability/ObsLogs';
import ObsTasks from './pages/observability/ObsTasks';
import Rules from './pages/observability/Rules';
import FactorLibrary from './pages/factors/FactorLibrary';
import FactorDetail from './pages/factors/FactorDetail';
import FactorTasks from './pages/factors/FactorTasks';
import UniverseManager from './pages/factors/UniverseManager';
import FactorImport from './pages/factors/FactorImport';
import FactorCorrelation from './pages/factors/FactorCorrelation';
// 因子篮子改版 (因子层重构 §2.2)
import BasketList from './pages/factors/baskets/BasketList';
import BasketWizard from './pages/factors/baskets/BasketWizard';
import BasketDetail from './pages/factors/baskets/BasketDetail';
import BasketRunDetail from './pages/factors/baskets/BasketRunDetail';
import EntryDetail from './pages/factors/baskets/EntryDetail';
import Screening from './pages/factors/screening/Screening';
import SelectionList from './pages/factors/screening/SelectionList';
import SelectionDetail from './pages/factors/screening/SelectionDetail';
import StatusRef from './pages/factors/baskets/StatusRef';
import Robustness from './pages/factors/baskets/Robustness';

const ROLE_RANK = { viewer: 0, editor: 1, admin: 2 };

function Protected({ children, role }) {
  const { user, loading } = useAuth();
  const loc = useLocation();
  if (loading) {
    return <div style={{ height: '100%', display: 'grid', placeItems: 'center' }}><Loading label="校验登录态…" /></div>;
  }
  if (!user) return <Navigate to="/login" state={{ from: loc.pathname }} replace />;
  // role gate (doc §8.8.3): e.g. 可观测「规则」页是 admin-only
  if (role && ROLE_RANK[user.role] < ROLE_RANK[role]) return <Navigate to="/403" replace />;
  return children;
}

// 旧 /data/logs/trace/:id → /observ/trace/:id (保留 param, §7.1)
function LegacyTraceRedirect() {
  const { traceId } = useParams();
  return <Navigate to={`/observ/trace/${traceId}`} replace />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/" element={<Navigate to="/data/dashboard" replace />} />

      <Route path="/data/dashboard" element={<Protected><Dashboard /></Protected>} />
      <Route path="/data/batches" element={<Protected><Batches /></Protected>} />
      <Route path="/data/batches/:batchId" element={<Protected><BatchDetail /></Protected>} />
      <Route path="/data/sources" element={<Protected><Sources /></Protected>} />
      <Route path="/data/sources/diff" element={<Protected><SourcesDiff /></Protected>} />
      <Route path="/data/sources/:sourceId" element={<Protected><SourceDetail /></Protected>} />
      <Route path="/data/fields" element={<Protected><Fields /></Protected>} />
      <Route path="/data/tables" element={<Protected><Tables /></Protected>} />
      <Route path="/data/tables/:tableName" element={<Protected><TableBrowser /></Protected>} />
      <Route path="/data/quality" element={<Protected><Quality /></Protected>} />
      <Route path="/data/maintenance" element={<Protected><Maintenance /></Protected>} />
      <Route path="/data/audit" element={<Protected><Audit /></Protected>} />

      {/* ── 可观测 (observ) ──────────────────────────────────────────────────
          模块入口 = 任何非纯无权角色可进 (后端按 source 收窄数据)；规则页单独 admin。
          (可观测层重构方案 §7.1 / §8) */}
      <Route path="/observ" element={<Navigate to="/observ/overview" replace />} />
      <Route path="/observ/overview" element={<Protected><ObsOverview /></Protected>} />
      <Route path="/observ/alerts" element={<Protected><Alerts /></Protected>} />
      <Route path="/observ/topology" element={<Protected><Topology /></Protected>} />
      <Route path="/observ/logs" element={<Protected><ObsLogs /></Protected>} />
      <Route path="/observ/stream" element={<Protected><LogStream /></Protected>} />
      <Route path="/observ/sessions" element={<Protected><ClientLogs /></Protected>} />
      <Route path="/observ/tasks" element={<Protected><ObsTasks /></Protected>} />
      {/* 规则页对 log-access 可见(非 admin 只读, 禁用态); 写阈值由后端 require_admin 把关 (§7.3⑧) */}
      <Route path="/observ/rules" element={<Protected><Rules /></Protected>} />
      <Route path="/observ/trace/:traceId" element={<Protected><TraceDetail /></Protected>} />

      {/* 旧「日志中心」/「因子日志」入口已被「可观测」接管 (§7.1)。保留 301-style
          重定向, 避免书签 / 外链 404。 */}
      <Route path="/data/logs/trace/:traceId" element={<LegacyTraceRedirect />} />
      <Route path="/data/logs/*" element={<Navigate to="/observ/logs" replace />} />
      <Route path="/data/logs" element={<Navigate to="/observ/overview" replace />} />
      <Route path="/factors/logs" element={<Navigate to="/observ/logs?source=factor" replace />} />

      <Route path="/data/charts" element={<Protected><ChartsHub /></Protected>} />
      <Route path="/data/charts/market" element={<Protected><Market /></Protected>} />
      <Route path="/data/charts/industry" element={<Protected><Industry /></Protected>} />
      <Route path="/data/charts/kline" element={<Protected><Kline /></Protected>} />
      <Route path="/data/charts/fundamentals" element={<Protected><Fundamentals /></Protected>} />
      <Route path="/data/charts/explorer" element={<Protected><Explorer /></Protected>} />

      <Route path="/settings" element={<Protected><Settings /></Protected>} />

      {/* Factor management — 6 sub-pages */}
      <Route path="/factors" element={<Navigate to="/factors/baskets" replace />} />
      <Route path="/factors/library" element={<Protected><FactorLibrary /></Protected>} />
      <Route path="/factors/library/:factorId" element={<Protected><FactorDetail /></Protected>} />
      <Route path="/factors/tasks" element={<Protected><FactorTasks /></Protected>} />
      <Route path="/factors/universe" element={<Protected><UniverseManager /></Protected>} />
      <Route path="/factors/import" element={<Protected role="editor"><FactorImport /></Protected>} />
      {/* 因子相关性已并入篮子 — 301 重定向 (因子层重构 §8) */}
      <Route path="/factors/correlation" element={<Navigate to="/factors/baskets" replace />} />
      {/* 因子篮子改版 (因子层重构 §2.2) */}
      <Route path="/factors/baskets" element={<Protected><BasketList /></Protected>} />
      <Route path="/factors/baskets/new" element={<Protected role="editor"><BasketWizard /></Protected>} />
      <Route path="/factors/baskets/:id" element={<Protected><BasketDetail /></Protected>} />
      <Route path="/factors/baskets/:id/runs/:rid" element={<Protected><BasketRunDetail /></Protected>} />
      <Route path="/factors/baskets/:id/items/:itemId" element={<Protected><EntryDetail /></Protected>} />
      <Route path="/factors/screening" element={<Protected><Screening /></Protected>} />
      <Route path="/factors/selections" element={<Protected><SelectionList /></Protected>} />
      <Route path="/factors/selections/:sid" element={<Protected><SelectionDetail /></Protected>} />
      <Route path="/factors/statusref" element={<Protected><StatusRef /></Protected>} />
      <Route path="/factors/:factorId/robustness" element={<Protected><Robustness /></Protected>} />
      {/* 因子日志已并入「可观测」(/observ/logs?source=factor) — 重定向在上方 §7.1 */}

      <Route path="/strategies/*" element={<Protected><Placeholder module="strategy" /></Protected>} />
      {/* 实盘交易 (P3 2027) 占位；旧 /monitor 前缀重定向到 /live (现 /monitor 为监控 API 命名空间) */}
      <Route path="/live/*" element={<Protected><Placeholder module="live" /></Protected>} />
      <Route path="/monitor/*" element={<Navigate to="/live" replace />} />

      <Route path="/403" element={<Page403 />} />
      <Route path="/500" element={<Page500 />} />
      <Route path="*" element={<Page404 />} />
    </Routes>
  );
}
