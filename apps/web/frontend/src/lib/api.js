// Axios client + typed endpoint helpers. The whole app talks to the backend
// through `endpoints`; that keeps URLs/params in one place.
import axios from 'axios';
import { logger, newTraceId, noteTrace } from './logger';

const TOKEN_KEY = 'zjq_token';
const REFRESH_KEY = 'zjq_refresh';
const USER_KEY = 'zjq_user';
const SLOW_MS = 2000;

export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY),
  getRefresh: () => localStorage.getItem(REFRESH_KEY),
  getUser: () => { try { return JSON.parse(localStorage.getItem(USER_KEY)); } catch { return null; } },
  set: (access, refresh, user) => {
    if (access) localStorage.setItem(TOKEN_KEY, access);
    if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
    if (user) localStorage.setItem(USER_KEY, JSON.stringify(user));
  },
  clear: () => { localStorage.removeItem(TOKEN_KEY); localStorage.removeItem(REFRESH_KEY); localStorage.removeItem(USER_KEY); },
};

export const api = axios.create({ baseURL: '/api/v1' });

api.interceptors.request.use((cfg) => {
  const t = tokenStore.get();
  cfg.headers = cfg.headers || {};
  if (t) cfg.headers.Authorization = `Bearer ${t}`;
  // Generate a trace_id per request and propagate it via X-Trace-Id so the
  // backend + data layer stamp the same id (doc §3.2 / §6.2).
  const traceId = newTraceId();
  cfg.headers['X-Trace-Id'] = traceId;
  cfg.metadata = { traceId, startedAt: performance.now() };
  noteTrace(traceId);
  return cfg;
});

api.interceptors.response.use(
  (r) => {
    const ms = performance.now() - (r.config.metadata?.startedAt || 0);
    const traceId = r.headers?.['x-trace-id'] || r.config.metadata?.traceId;
    noteTrace(traceId);
    if (ms > SLOW_MS) {
      logger.apiSlow({ method: r.config.method, url: r.config.url, ms: Math.round(ms) }, traceId);
    }
    return r;
  },
  (err) => {
    const traceId = err.response?.headers?.['x-trace-id'] || err.config?.metadata?.traceId;
    noteTrace(traceId);
    const code = err?.response?.data?.error?.code;
    if (err?.response?.status === 401 && code === 'UNAUTHENTICATED') {
      tokenStore.clear();
      if (!location.pathname.startsWith('/login')) location.assign('/login');
    }
    // normalise error so callers can read err.apiError / err.traceId
    err.apiError = err?.response?.data?.error || { code: 'NETWORK', message: err.message };
    err.traceId = traceId;
    // report to the log centre — but not the failures of the log endpoint itself
    if (!String(err.config?.url || '').startsWith('/logs')) {
      logger.apiFailure({
        method: err.config?.method,
        url: err.config?.url,
        status: err.response?.status,
        error_code: err.apiError.code,
        message: err.apiError.message,
      }, err instanceof Error ? err : undefined, traceId);
    }
    return Promise.reject(err);
  },
);

const get = (url, params) => api.get(url, { params }).then((r) => r.data);
const post = (url, body) => api.post(url, body).then((r) => r.data);
const patch = (url, body) => api.patch(url, body).then((r) => r.data);
const del = (url) => api.delete(url).then((r) => r.data);

export const endpoints = {
  // auth
  login: (username, password) => post('/auth/login', { username, password }),
  me: () => get('/auth/me'),
  logout: () => post('/auth/logout'),

  // meta / health (修复 A1 · 五容器拓扑)
  health: () => get('/health'),             // 轻量 liveness (如别处需要)
  healthDeps: () => get('/health/deps'),    // 状态栏聚合：db/warehouse/keeper/factor

  // dashboard
  dashboardSummary: () => get('/data/dashboard/summary'),

  // batches
  batches: (params) => get('/data/batches', params),
  batchStats: (period) => get('/data/batches/stats', { period }),
  batch: (id) => get(`/data/batches/${id}`),
  batchLineage: (id) => get(`/data/batches/${id}/lineage`),
  batchQuality: (id) => get(`/data/batches/${id}/quality`),
  batchRows: (id, limit = 100) => get(`/data/batches/${id}/rows`, { limit }),
  invalidateBatch: (id, reason) => post(`/data/batches/${id}/invalidate`, { reason }),
  supersede: (old_batch_id, new_batch_id) => post('/data/batches/supersede', { old_batch_id, new_batch_id }),

  // sources
  sources: (active_only = false) => get('/data/sources', { active_only }),
  source: (id) => get(`/data/sources/${id}`),
  createSource: (body) => post('/data/sources', body),
  updateSource: (id, body) => patch(`/data/sources/${id}`, body),
  deactivateSource: (id) => del(`/data/sources/${id}`),
  diffSources: (body) => post('/data/sources/diff', body),

  // fields
  fields: (params) => get('/data/fields', params),
  field: (table, field) => get(`/data/fields/${table}/${field}`),
  updateField: (table, field, body) => patch(`/data/fields/${table}/${field}`, body),
  promoteField: (table, field, body) => post(`/data/fields/${table}/${field}/promote`, body),
  deprecateField: (table, field) => post(`/data/fields/${table}/${field}/deprecate`, {}),
  migrateField: (table, field, body) => post(`/data/fields/${table}/${field}/migrate`, body),
  addConsumer: (table, field, consumer) => post(`/data/fields/${table}/${field}/consumers`, { consumer }),
  fieldsStale: (days = 90) => get('/data/fields/stale', { days }),
  fieldsTimeTravel: (as_of, table) => get('/data/fields/time-travel', { as_of, table }),

  // tables
  tables: () => get('/data/tables'),
  tableSchema: (name) => get(`/data/tables/${name}/schema`),
  tableExtrasKeys: (name) => get(`/data/tables/${name}/extras-keys`),
  tableRows: (name, params) => get(`/data/tables/${name}/rows`, params),
  tableRowsAll: (name, params) => get(`/data/tables/${name}/rows/all`, params),
  tableRowLineage: (name, params) => get(`/data/tables/${name}/rows/lineage`, params),

  // charts
  kline: (params) => get('/data/charts/kline', params),
  klineMarks: (params) => get('/data/charts/kline/marks', params),
  klineMulti: (body) => post('/data/charts/kline/multi', body),
  klineDiffSources: (params) => get('/data/charts/kline/diff-sources', params),
  fundamentals: (params) => get('/data/charts/fundamentals', params),
  explorerPreview: (body) => post('/data/charts/explorer/preview', body),
  explorerTemplates: () => get('/data/charts/explorer/templates'),
  saveTemplate: (body) => post('/data/charts/explorer/templates', body),
  deleteTemplate: (id) => del(`/data/charts/explorer/templates/${id}`),
  symbolSearch: (q, limit = 20) => get('/data/charts/symbols/search', { q, limit }),

  // quality
  qualityIssues: (params) => get('/data/quality/issues', params),
  qualityTimeseries: (days, group_by) => get('/data/quality/timeseries', { days, group_by }),
  qualityByTable: () => get('/data/quality/by-table'),
  acknowledgeIssue: (id, body) => patch(`/data/quality/issues/${id}`, body),

  // maintenance
  vacuumPreview: (body) => post('/data/maintenance/vacuum/preview', body),
  vacuumExecute: (body) => post('/data/maintenance/vacuum/execute', body),
  compactPreview: (body) => post('/data/maintenance/compact/preview', body),
  compactExecute: (body) => post('/data/maintenance/compact/execute', body),
  snapshot: () => post('/data/maintenance/snapshot', {}),
  snapshots: () => get('/data/maintenance/snapshots'),
  deleteSnapshot: (id) => del(`/data/maintenance/snapshots/${id}`),
  refreshStats: () => post('/data/maintenance/refresh-stats', {}),

  // jobs
  jobs: (limit = 20) => get('/data/jobs', { limit }),
  job: (id) => get(`/data/jobs/${id}`),
  cancelJob: (id) => post(`/data/jobs/${id}/cancel`, {}),

  // audit
  audit: (params) => get('/data/audit', params),

  // ── factor management (proxied to factor-api) ────────────────────────
  factors: (params) => get('/factors', params),
  factor: (factorId) => get(`/factors/${factorId}`),
  factorVersions: (factorId) => get(`/factors/${factorId}/versions`),
  factorIc: (factorId) => get(`/factors/${factorId}/ic`),
  factorTracking: (factorId) => get(`/factors/${factorId}/tracking`),
  factorDependencies: (factorId) => get(`/factors/${factorId}/dependencies`),
  factorConsumers: (factorId) => get(`/factors/${factorId}/consumers`),
  patchFactor: (factorId, body) => patch(`/factors/${factorId}`, body),
  archiveFactor: (factorId) => del(`/factors/${factorId}`),
  enableVersion: (factorId, version) => post(`/factors/${factorId}/versions/${version}/enable`, {}),
  factorValues: (factorId, body) => post(`/factors/${factorId}/values`, body),

  // runs
  submitRun: (body) => post('/runs', body),
  previewEta: (body) => post('/runs/preview-eta', body),
  runs: (params) => get('/runs', params),
  run: (runId) => get(`/runs/${runId}`),
  cancelRun: (runId) => post(`/runs/${runId}/cancel`, {}),
  retryRun: (runId) => post(`/runs/${runId}/retry`, {}),
  runIcSeries: (runId) => get(`/runs/${runId}/ic-series`),
  runLayers: (runId) => get(`/runs/${runId}/layers`),
  runTurnover: (runId) => get(`/runs/${runId}/turnover`),

  // tasks
  tasks: (params) => get('/tasks', params),
  task: (taskId) => get(`/tasks/${taskId}`),
  taskEta: (taskId) => get(`/tasks/${taskId}/eta`),
  boostTask: (taskId) => post(`/tasks/${taskId}/boost`, {}),
  cancelTask: (taskId) => post(`/tasks/${taskId}/cancel`, {}),
  queueStats: () => get('/queue/stats'),

  // universe (stock pools)
  universes: () => get('/universe'),
  universe: (uid) => get(`/universe/${uid}`),
  createUniverse: (body) => post('/universe', body),
  updateUniverse: (uid, body) => patch(`/universe/${uid}`, body),
  deleteUniverse: (uid) => del(`/universe/${uid}`),
  previewUniverse: (uid, body) => post(`/universe/${uid}/preview`, body),
  symbolSearchUni: (q, limit = 20) => get('/universe/symbols/search', { q, limit }),
  industries: (system, level) => get('/universe/industries', { system, level }),

  // imports
  validateFactor: (body) => post('/imports/validate', body),
  uploadFactor: (form) => api.post('/imports/upload', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  }).then((r) => r.data),
  scanInbox: (body) => post('/imports/scan', body || {}),
  pendingImports: () => get('/imports/pending'),
  importCode: (factorId, version) => get('/imports/code', { factor_id: factorId, version }),

  // correlation
  submitCorrelation: (body) => post('/factors/correlation', body),
  correlationResult: (taskId) => get(`/factors/correlation/${taskId}`),

  // logs (admin) — 全链路日志方案 v2.0 §6.5
  logs: (params) => get('/logs', params),
  logsSummary: () => get('/logs/summary'),
  logsTrace: (traceId) => get(`/logs/trace/${encodeURIComponent(traceId)}`),
  logsExport: (params) => api.get('/logs/export', { params, responseType: 'blob' }).then((r) => r.data),
  archiveLogs: (retention_days) => api.post('/logs/archive', null, { params: { retention_days } }).then((r) => r.data),
};

// SSE stream URL — EventSource can't set headers, so the token rides as a query
// param (the backend validates it as admin). doc §7.4.
export function logsStreamUrl({ level, source } = {}) {
  const qs = new URLSearchParams();
  const t = tokenStore.get();
  if (t) qs.set('token', t);
  if (level) qs.set('level', level);
  if (source) qs.set('source', source);
  return `/api/v1/logs/stream?${qs.toString()}`;
}

// Run-log SSE URL — same token-in-query trick as logsStreamUrl.
export function runLogStreamUrl(runId) {
  const qs = new URLSearchParams();
  const t = tokenStore.get();
  if (t) qs.set('token', t);
  return `/api/v1/runs/${encodeURIComponent(runId)}/logs/stream?${qs.toString()}`;
}
