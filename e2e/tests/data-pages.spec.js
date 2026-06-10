import { test, expect } from '@playwright/test';
import { loginAs } from './helpers';

// Data-governance pages against the seeded demo warehouse (8 A-share symbols,
// price/fundamental batches, quality issues, corporate actions).
test.describe('数据治理页面', () => {
  test.beforeEach(async ({ page, request }) => {
    await loginAs(page, request, 'admin');
  });

  test('数据总览展示 KPI 与内容', async ({ page }) => {
    await page.goto('/data/dashboard');
    // The seeded world has batches today → the KPI strip renders numbers.
    await expect(page.getByTestId('status-bar')).toBeVisible();
    await expect(page.locator('body')).toContainText(/批次|成功率|数据/, { timeout: 15_000 });
  });

  test('批次列表展示种子批次并可进入详情', async ({ page }) => {
    await page.goto('/data/batches');
    // Seeded batches include raw.bars_daily ingests.
    const row = page.locator('table tbody tr').first();
    await expect(row).toBeVisible({ timeout: 15_000 });
    await row.click();
    await page.waitForURL(/\/data\/batches\/.+/);
  });

  test('数据源列表展示种子数据源卡片', async ({ page }) => {
    await page.goto('/data/sources');
    await expect(page.getByTestId('source-card-akshare')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('source-card-tushare_pro')).toBeVisible();
  });

  test('表清单包含 raw.bars_daily 并可打开表浏览器', async ({ page }) => {
    await page.goto('/data/tables');
    await expect(page.locator('body')).toContainText('raw.bars_daily', { timeout: 15_000 });
    // 大表浏览强制先选过滤条件 (防全表扫描) — 页面应正常加载并提示/等待过滤。
    await page.goto('/data/tables/raw.bars_daily');
    await expect(page.getByTestId('status-bar')).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('body')).toContainText(/raw\.bars_daily/);
  });

  test('字段注册表加载', async ({ page }) => {
    await page.goto('/data/fields');
    await expect(page.locator('table tbody tr').first()).toBeVisible({ timeout: 15_000 });
  });

  test('质量页展示种子质量问题', async ({ page }) => {
    await page.goto('/data/quality');
    await expect(page.locator('body')).toContainText(/质量|异常|issues|规则/, { timeout: 15_000 });
  });

  test('审计页加载', async ({ page }) => {
    await page.goto('/data/audit');
    await expect(page.getByTestId('status-bar')).toBeVisible({ timeout: 15_000 });
  });
});
