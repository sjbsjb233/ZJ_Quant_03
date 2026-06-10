// Playwright config — boots the real three-service dev stack:
//   web-backend (uvicorn :8000) ← factor-api (uvicorn :8100), SPA (vite :5173).
// The backend scripts seed a demo warehouse + demo users into e2e/.stack on
// first run, so the suite exercises real data paths, not mocks.
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests',
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : 4,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: 'http://localhost:5173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  webServer: [
    {
      command: 'bash scripts/run-backend.sh',
      url: 'http://localhost:8000/api/v1/health',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: 'bash scripts/run-factor.sh',
      url: 'http://localhost:8100/health',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
    {
      command: 'npm run dev --prefix ../apps/web/frontend',
      url: 'http://localhost:5173',
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
