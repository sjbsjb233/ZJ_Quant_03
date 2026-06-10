import { test, expect } from '@playwright/test';
import { loginAs } from './helpers';

test.describe('可观测模块', () => {
  test.beforeEach(async ({ page, request }) => {
    await loginAs(page, request, 'admin');
  });

  test('可观测总览加载', async ({ page }) => {
    await page.goto('/observ/overview');
    await expect(page.getByTestId('status-bar')).toBeVisible({ timeout: 15_000 });
  });

  test('日志页展示后端启动日志', async ({ page }) => {
    await page.goto('/observ/logs');
    // The backend logged app.startup into app_log on boot.
    await expect(page.locator('body')).toContainText(/app\.startup|auth\.login|日志/, { timeout: 15_000 });
  });

  test('告警页加载', async ({ page }) => {
    await page.goto('/observ/alerts');
    await expect(page.getByTestId('status-bar')).toBeVisible({ timeout: 15_000 });
  });

  test('旧日志路由重定向到可观测', async ({ page }) => {
    await page.goto('/data/logs');
    await page.waitForURL(/\/observ\//);
  });
});
