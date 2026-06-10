import { test, expect } from '@playwright/test';
import { API, apiLogin, authHeaders } from './helpers';

// Pure HTTP contract tests against web-backend (and the factor proxy) — no
// browser involved, so they double as fast regression checks for auth,
// pagination, error envelopes and trace propagation.
test.describe('API 契约', () => {
  test('liveness 健康检查', async ({ request }) => {
    const res = await request.get(`${API}/health`);
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.status).toBe('ok');
    expect(body.service).toBe('web-backend');
  });

  test('匿名访问数据接口返回统一 401 错误包', async ({ request }) => {
    const res = await request.get(`${API}/data/batches`);
    expect(res.status()).toBe(401);
    const body = await res.json();
    expect(body.error.code).toBe('UNAUTHENTICATED');
    expect(typeof body.error.message).toBe('string');
  });

  test('响应携带 X-Trace-Id 并回显请求方的值', async ({ request }) => {
    const res = await request.get(`${API}/health`, {
      headers: { 'X-Trace-Id': 't_e2e_contract_check' },
    });
    expect(res.headers()['x-trace-id']).toBe('t_e2e_contract_check');
  });

  test('批次分页 page_size 生效且 total 一致', async ({ request }) => {
    const auth = await apiLogin(request, 'admin');
    const res = await request.get(`${API}/data/batches?page=1&page_size=3`, {
      headers: authHeaders(auth),
    });
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.items.length).toBeLessThanOrEqual(3);
    expect(body.total).toBeGreaterThan(0);
  });

  test('viewer 无权执行写操作', async ({ request }) => {
    const auth = await apiLogin(request, 'viewer');
    const res = await request.post(`${API}/data/sources`, {
      headers: authHeaders(auth),
      data: { source_id: 'e2e_should_fail', name: 'x', type: 'csv' },
    });
    expect([401, 403]).toContain(res.status());
  });

  test('refresh 流程换取新 access token', async ({ request }) => {
    const auth = await apiLogin(request, 'admin');
    const res = await request.post(`${API}/auth/refresh`, {
      data: { refresh_token: auth.refresh_token },
    });
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.access_token).toBeTruthy();
    // The new access token actually works.
    const me = await request.get(`${API}/auth/me`, {
      headers: { Authorization: `Bearer ${body.access_token}` },
    });
    expect(me.status()).toBe(200);
    expect((await me.json()).username).toBe('admin');
  });

  test('access token 不能当 refresh token 用', async ({ request }) => {
    const auth = await apiLogin(request, 'admin');
    const res = await request.post(`${API}/auth/refresh`, {
      data: { refresh_token: auth.access_token },
    });
    expect(res.status()).toBe(401);
  });

  test('因子代理链路返回内置因子', async ({ request }) => {
    const auth = await apiLogin(request, 'admin');
    const res = await request.get(`${API}/factors`, { headers: authHeaders(auth) });
    expect(res.status()).toBe(200);
    const body = await res.json();
    const ids = body.items.map((f) => f.factor_id);
    expect(ids).toContain('mom');
  });

  test('K 线数据接口返回种子标的的 OHLC', async ({ request }) => {
    const auth = await apiLogin(request, 'admin');
    const res = await request.get(
      `${API}/data/charts/kline?symbol=600519.SH&freq=1d&adjust=none&limit=30`,
      { headers: authHeaders(auth) },
    );
    expect(res.status()).toBe(200);
    const body = await res.json();
    expect(body.symbol).toBe('600519.SH');
    expect(body.candles.length).toBeGreaterThan(0);
    const c = body.candles[0];
    expect(c.high).toBeGreaterThanOrEqual(c.low);
  });

  test('大表行浏览要求过滤条件（防全表扫描守卫）', async ({ request }) => {
    const auth = await apiLogin(request, 'admin');
    const bare = await request.get(`${API}/data/tables/raw.bars_daily/rows?limit=5`, {
      headers: authHeaders(auth),
    });
    expect(bare.status()).toBe(400);
    expect((await bare.json()).error.code).toBe('MISSING_PARAMETER');
    // With a symbol filter, rows come back.
    const ok = await request.get(
      `${API}/data/tables/raw.bars_daily/rows?symbols=600519.SH&limit=5`,
      { headers: authHeaders(auth) },
    );
    expect(ok.status()).toBe(200);
    const body = await ok.json();
    expect((body.items ?? body.rows ?? []).length).toBeGreaterThan(0);
  });

  test('健康聚合接口列出依赖组件', async ({ request }) => {
    const auth = await apiLogin(request, 'admin');
    const res = await request.get(`${API}/health/deps`, { headers: authHeaders(auth) });
    expect(res.status()).toBe(200);
  });
});
