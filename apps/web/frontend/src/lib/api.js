// Axios client + typed endpoint helpers. The whole app talks to the backend
// through `endpoints`; that keeps URLs/params in one place.
import axios from 'axios';

const TOKEN_KEY = 'zjq_token';
const REFRESH_KEY = 'zjq_refresh';
const USER_KEY = 'zjq_user';

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
  if (t) cfg.headers.Authorization = `Bearer ${t}`;
  return cfg;
});

api.interceptors.response.use(
  (r) => r,
  (err) => {
    const code = err?.response?.data?.error?.code;
    if (err?.response?.status === 401 && code === 'UNAUTHENTICATED') {
      tokenStore.clear();
      if (!location.pathname.startsWith('/login')) location.assign('/login');
    }
    // normalise error so callers can read err.apiError
    err.apiError = err?.response?.data?.error || { code: 'NETWORK', message: err.message };
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
};
