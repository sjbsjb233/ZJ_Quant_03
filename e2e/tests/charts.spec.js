import { test, expect } from '@playwright/test';
import { loginAs } from './helpers';

test.describe('行情图表', () => {
  test.beforeEach(async ({ page, request }) => {
    await loginAs(page, request, 'admin');
  });

  test('图表导航页加载', async ({ page }) => {
    await page.goto('/data/charts');
    await expect(page.getByTestId('status-bar')).toBeVisible({ timeout: 15_000 });
  });

  test('K 线页渲染图表画布', async ({ page }) => {
    await page.goto('/data/charts/kline');
    // klinecharts renders into a canvas once data arrives.
    await expect(page.locator('canvas').first()).toBeVisible({ timeout: 20_000 });
  });

  test('市场全景页加载图表', async ({ page }) => {
    await page.goto('/data/charts/market');
    await expect(page.locator('canvas').first()).toBeVisible({ timeout: 20_000 });
  });

  test('行业视图页加载', async ({ page }) => {
    await page.goto('/data/charts/industry');
    await expect(page.getByTestId('status-bar')).toBeVisible({ timeout: 15_000 });
  });

  test('基本面页加载', async ({ page }) => {
    await page.goto('/data/charts/fundamentals');
    await expect(page.getByTestId('status-bar')).toBeVisible({ timeout: 15_000 });
  });
});
