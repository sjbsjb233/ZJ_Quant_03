import React from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
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
import Kline from './pages/charts/Kline';
import Fundamentals from './pages/charts/Fundamentals';
import Explorer from './pages/charts/Explorer';
import Placeholder from './pages/Placeholder';
import { Page403, Page404, Page500 } from './pages/errors';

function Protected({ children }) {
  const { user, loading } = useAuth();
  const loc = useLocation();
  if (loading) {
    return <div style={{ height: '100%', display: 'grid', placeItems: 'center' }}><Loading label="校验登录态…" /></div>;
  }
  if (!user) return <Navigate to="/login" state={{ from: loc.pathname }} replace />;
  return children;
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

      <Route path="/data/charts" element={<Protected><ChartsHub /></Protected>} />
      <Route path="/data/charts/kline" element={<Protected><Kline /></Protected>} />
      <Route path="/data/charts/fundamentals" element={<Protected><Fundamentals /></Protected>} />
      <Route path="/data/charts/explorer" element={<Protected><Explorer /></Protected>} />

      <Route path="/settings" element={<Protected><Settings /></Protected>} />
      <Route path="/factors/*" element={<Protected><Placeholder module="factor" /></Protected>} />
      <Route path="/strategies/*" element={<Protected><Placeholder module="strategy" /></Protected>} />
      <Route path="/monitor/*" element={<Protected><Placeholder module="live" /></Protected>} />

      <Route path="/403" element={<Page403 />} />
      <Route path="/500" element={<Page500 />} />
      <Route path="*" element={<Page404 />} />
    </Routes>
  );
}
