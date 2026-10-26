import { defineConfig, devices } from '@playwright/test'

/**
 * Two modes:
 *
 *  - **default**: the suite mocks the API with `page.route()` and runs against a local
 *    preview build. Fast, deterministic, no backend or model needed — this is what CI runs.
 *  - **live**: set `E2E_BASE_URL` to a deployed origin (and `E2E_API_URL` to its API) and
 *    the live-smoke spec runs against the real system instead. Not part of CI, because it
 *    depends on a free-tier host that sleeps and on data that changes.
 *
 * The reason for the split: a mocked run can verify that the UI does the right thing with
 * a given response, but only a live run can catch deployment-level faults like a missing
 * SPA rewrite.
 */
const PORT = 4173

export default defineConfig({
  testDir: './e2e',
  // Fixtures are generated, not committed; see e2e/global-setup.js.
  globalSetup: './e2e/global-setup.js',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 2 : undefined,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  timeout: 30_000,
  expect: { timeout: 7_000 },

  use: {
    baseURL: process.env.E2E_BASE_URL || `http://localhost:${PORT}`,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    // Escape hatch for environments with a browser pre-installed at a fixed path (some
    // CI images and sandboxes). Unset by default, so a normal run uses the browser that
    // `npx playwright install` downloaded.
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_PATH
      ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_PATH }
      : {},
  },

  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    // CropGuard is a field tool: the mobile layout is the one that matters.
    { name: 'mobile', use: { ...devices['Pixel 5'] } },
  ],

  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        command: `npm run preview -- --port ${PORT} --strictPort`,
        url: `http://localhost:${PORT}`,
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
})
