// Frontend structured logger (全链路日志方案 v2.0 §6.1).
//
// Buffers structured records and flushes them in batches to
// POST /api/v1/logs/client (every 5s, when the buffer fills, and on
// beforeunload via sendBeacon). Captures window.onerror, unhandled promise
// rejections, axios failures, React boundary errors and key user actions.
//
// Deliberately uses raw fetch/sendBeacon — NOT the axios `api` instance — so a
// failing log upload can never trip the axios interceptor and recurse, and so
// importing this module never creates a cycle with lib/api.js.

const TOKEN_KEY = 'zjq_token';
const USER_KEY = 'zjq_user';
const ENDPOINT = '/api/v1/logs/client';
const MAX_BUFFER = 50;
const FLUSH_AT = 25;
const FLUSH_INTERVAL_MS = 5000;
const STACK_CAP = 4096;

// hex id helpers (no uuid dependency; doc §14.2)
function hex(bytes) {
  const a = new Uint8Array(bytes);
  (globalThis.crypto || {}).getRandomValues?.(a);
  return Array.from(a, (b) => b.toString(16).padStart(2, '0')).join('');
}
export function newTraceId() { return `t_${hex(8)}`; }    // t_ + 16 hex (doc §9.3)

const SESSION_ID = `s_${hex(8)}`;
let buffer = [];
let lastTraceId = null;
let started = false;

function token() { try { return localStorage.getItem(TOKEN_KEY); } catch { return null; } }
function actor() {
  try { return JSON.parse(localStorage.getItem(USER_KEY))?.username || null; } catch { return null; }
}

// The most recent API trace_id — surfaced in the StatusBar / error pages so a
// user can quote it (doc §8.8.2). Broadcast so components can react.
export function noteTrace(traceId) {
  if (!traceId) return;
  lastTraceId = traceId;
  try { window.dispatchEvent(new CustomEvent('zjq:trace', { detail: traceId })); } catch { /* noop */ }
}
export function getLastTraceId() { return lastTraceId; }

function cap(value, n = STACK_CAP) {
  if (value == null) return undefined;
  const s = typeof value === 'string' ? value : String(value);
  return s.length > n ? `${s.slice(0, n)}…(truncated)` : s;
}

function record(level, event, { loggerName, extra, exc, traceId } = {}) {
  const rec = {
    ts: new Date().toISOString(),
    level,
    logger: loggerName || 'frontend',
    event,
    session_id: SESSION_ID,
    trace_id: traceId || lastTraceId || undefined,
    route: location.pathname + location.search,
    user_agent: navigator.userAgent,
    viewport: `${window.innerWidth}x${window.innerHeight}`,
    actor: actor() || undefined,
    extra: extra || undefined,
  };
  if (exc) {
    rec.exc_type = exc.name || 'Error';
    rec.exc_message = cap(exc.message || String(exc), 1024);
    rec.stack = cap(exc.stack);
  }
  buffer.push(rec);
  if (buffer.length >= FLUSH_AT) flush();
  if (buffer.length > MAX_BUFFER) buffer = buffer.slice(-MAX_BUFFER);
}

export const logger = {
  debug(event, extra) { record('DEBUG', event, { extra }); },
  info(event, extra) { record('INFO', event, { extra }); },
  warn(event, extra) { record('WARN', event, { extra }); },
  error(event, exc, extra) { record('ERROR', event, { exc, extra }); },

  // called by the axios interceptor on a failed request
  apiFailure(detail, exc, traceId) {
    record('ERROR', 'api.failure', { loggerName: 'frontend.api', exc, extra: detail, traceId });
  },
  // a slow but successful request
  apiSlow(detail, traceId) {
    record('WARN', 'api.slow', { loggerName: 'frontend.api', extra: detail, traceId });
  },
  // a deliberate, audited user action (doc §6.4)
  userAction(action, detail) {
    record('INFO', action, { loggerName: 'frontend.action', extra: detail });
  },
  flush,
  sessionId: SESSION_ID,
};

function flush(useBeacon = false) {
  if (buffer.length === 0) return;
  const t = token();
  if (!t) { buffer = []; return; }   // pre-login: nothing to attribute it to
  const payload = JSON.stringify({ records: buffer });
  buffer = [];
  try {
    if (useBeacon && navigator.sendBeacon) {
      // sendBeacon can't set an Authorization header — pass the token as a query
      // param (the client-log route accepts it for the beacon path).
      navigator.sendBeacon(`${ENDPOINT}?token=${encodeURIComponent(t)}`,
        new Blob([payload], { type: 'application/json' }));
    } else {
      fetch(ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${t}` },
        body: payload,
        keepalive: true,
      }).catch(() => { /* drop on failure — never recurse into logging */ });
    }
  } catch { /* swallow */ }
}

export function installGlobalErrorHandlers() {
  if (started) return;
  started = true;
  window.addEventListener('error', (e) => {
    logger.error('window.error', e.error || { name: 'Error', message: e.message },
      { filename: e.filename, lineno: e.lineno, colno: e.colno });
  });
  window.addEventListener('unhandledrejection', (e) => {
    const reason = e.reason;
    const exc = reason instanceof Error ? reason : { name: 'UnhandledRejection', message: String(reason) };
    logger.error('promise.unhandled', exc, {});
  });
  window.addEventListener('beforeunload', () => flush(true));
  setInterval(() => flush(false), FLUSH_INTERVAL_MS);
}
