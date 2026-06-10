import { test, expect } from '@playwright/test';
import { loginAs } from './helpers';

test.describe('认证与权限', () => {
  test('登录页渲染表单', async ({ page }) => {
    await page.goto('/login');
    await expect(page.getByTestId('login-username')).toBeVisible();
    await expect(page.getByTestId('login-password')).toBeVisible();
    await expect(page.getByTestId('login-submit')).toBeVisible();
  });

  test('错误密码提示失败且停留在登录页', async ({ page }) => {
    await page.goto('/login');
    await page.getByTestId('login-username').fill('admin');
    await page.getByTestId('login-password').fill('wrong-password');
    await page.getByTestId('login-submit').click();
    // Stays on /login; the API answered 401 so no token is stored.
    await page.waitForTimeout(1000);
    expect(page.url()).toContain('/login');
    const token = await page.evaluate(() => localStorage.getItem('zjq_token'));
    expect(token).toBeNull();
  });

  test('正确凭据通过 UI 登录进入数据总览', async ({ page }) => {
    await page.goto('/login');
    await page.getByTestId('login-username').fill('admin');
    await page.getByTestId('login-password').fill('admin123');
    await page.getByTestId('login-submit').click();
    await page.waitForURL(/\/data\/dashboard/, { timeout: 15_000 });
    const token = await page.evaluate(() => localStorage.getItem('zjq_token'));
    expect(token).toBeTruthy();
  });

  test('匿名访问受保护路由跳转登录页', async ({ page }) => {
    await page.goto('/data/batches');
    await page.waitForURL(/\/login/);
  });

  test('viewer 访问 editor 专属页面被拦截到 403', async ({ page, request }) => {
    await loginAs(page, request, 'viewer');
    await page.goto('/factors/import');
    await page.waitForURL(/\/403/);
  });

  test('admin 可以打开 editor 专属页面', async ({ page, request }) => {
    await loginAs(page, request, 'admin');
    await page.goto('/factors/import');
    await expect(page).toHaveURL(/\/factors\/import/);
  });

  test('登出清空令牌并回到登录页', async ({ page }) => {
    // UI 登录（不用 addInitScript 注入 — 它会在每次导航时重新写入 token，
    // 干扰「登出后 token 应被清空」的断言）。
    await page.goto('/login');
    await page.getByTestId('login-username').fill('admin');
    await page.getByTestId('login-password').fill('admin123');
    await page.getByTestId('login-submit').click();
    await page.waitForURL(/\/data\/dashboard/, { timeout: 15_000 });
    await page.getByTestId('topbar-logout').click();
    await page.waitForURL(/\/login/);
    const token = await page.evaluate(() => localStorage.getItem('zjq_token'));
    expect(token).toBeNull();
  });
});
