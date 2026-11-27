import { defineConfig, devices } from '@playwright/test';

/**
 * Post-deploy browser suite for the staging UI (T54).
 *
 * Auth is keyless. The workflow authenticates to GCP with Workload Identity
 * Federation, signs a short-lived service-account JWT for the dedicated
 * `agentic-kg-e2e-<env>` account, and passes it as `E2E_IAP_JWT`. IAP validates
 * that JWT at the edge; the UI's server-side proxy forwards the resulting IAP
 * assertion to the private API. Every request below therefore carries the same
 * Bearer token, and no secret is stored anywhere.
 *
 * `E2E_UI_URL` is the staging UI's `run.app` URL. Both are required and are
 * asserted in `tests/global-setup.ts`.
 */
const baseURL = process.env.E2E_UI_URL?.trim();
const iapToken = process.env.E2E_IAP_JWT?.trim();

export default defineConfig({
  testDir: './tests',
  globalSetup: './tests/global-setup.ts',
  // A workflow can take a couple of minutes to rank its first candidate set.
  timeout: 240_000,
  expect: { timeout: 20_000 },
  // The suite mutates staging (one workflow, one extract); keep it serial.
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  forbidOnly: Boolean(process.env.CI),
  reporter: [
    ['list'],
    ['html', { outputFolder: 'playwright-report', open: 'never' }],
  ],
  outputDir: 'test-results',
  use: {
    baseURL,
    extraHTTPHeaders: iapToken
      ? { Authorization: `Bearer ${iapToken}` }
      : {},
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'off',
    actionTimeout: 30_000,
    navigationTimeout: 60_000,
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
