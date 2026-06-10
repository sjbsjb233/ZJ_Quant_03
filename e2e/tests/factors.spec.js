import { test, expect } from '@playwright/test';
import { loginAs } from './helpers';

// Factor pages ride the web-backend → factor-api proxy, so these verify the
// full chain: SPA → :8000 /api/v1/factors → :8100.
test.describe('因子管理', () => {
  test.beforeEach(async ({ page, request }) => {
    await loginAs(page, request, 'admin');
  });

  test('因子库列出内置因子', async ({ page }) => {
    await page.goto('/factors/library');
    await expect(page.getByTestId('factor-lib-table')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('factor-lib-row-mom')).toBeVisible();
  });

  test('因子详情页加载动量因子', async ({ page }) => {
    await page.goto('/factors/library/mom');
    await expect(page.locator('body')).toContainText(/动量|mom/, { timeout: 15_000 });
  });

  test('篮子列表页加载', async ({ page }) => {
    await page.goto('/factors/baskets');
    await expect(page.getByTestId('status-bar')).toBeVisible({ timeout: 15_000 });
  });

  test('股票池管理页加载', async ({ page }) => {
    await page.goto('/factors/universe');
    await expect(page.getByTestId('status-bar')).toBeVisible({ timeout: 15_000 });
  });

  test('任务页加载', async ({ page }) => {
    await page.goto('/factors/tasks');
    await expect(page.getByTestId('status-bar')).toBeVisible({ timeout: 15_000 });
  });
});
