// Shared helpers: API login + injecting the token into localStorage so page
// tests start authenticated (mirrors lib/api.js tokenStore keys).
import { expect } from '@playwright/test';

export const API = 'http://localhost:8000/api/v1';

export const USERS = {
  admin: { username: 'admin', password: 'admin123' },
  editor: { username: 'editor', password: 'editor123' },
  viewer: { username: 'viewer', password: 'viewer123' },
};

export async function apiLogin(request, role = 'admin') {
  const res = await request.post(`${API}/auth/login`, { data: USERS[role] });
  expect(res.ok(), `login as ${role} should succeed`).toBeTruthy();
  return res.json();
}

export async function loginAs(page, request, role = 'admin') {
  const auth = await apiLogin(request, role);
  await page.addInitScript(({ access, refresh, user }) => {
    localStorage.setItem('zjq_token', access);
    localStorage.setItem('zjq_refresh', refresh);
    localStorage.setItem('zjq_user', JSON.stringify(user));
  }, { access: auth.access_token, refresh: auth.refresh_token, user: auth.user });
  return auth;
}

export function authHeaders(auth) {
  return { Authorization: `Bearer ${auth.access_token}` };
}
