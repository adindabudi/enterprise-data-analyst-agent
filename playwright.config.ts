import { defineConfig } from "@playwright/test";

const baseUse = {
  baseURL: "http://127.0.0.1:4173",
  locale: "en-US",
  timezoneId: "UTC",
  colorScheme: "light" as const,
  contextOptions: { reducedMotion: "reduce" as const },
  trace: "on-first-retry" as const,
};

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  workers: 1,
  retries: 1,
  timeout: 30_000,
  expect: {
    timeout: 8_000,
    toHaveScreenshot: { animations: "disabled", caret: "hide" },
  },
  outputDir: "test-results/playwright",
  snapshotPathTemplate:
    "{testDir}/screenshots/{projectName}/{testFilePath}/{arg}{ext}",
  webServer: {
    command:
      "npm run build --workspace=@eda/web && uv run python scripts/run-e2e-server.py",
    url: "http://127.0.0.1:4173/health/live",
    reuseExistingServer: false,
    timeout: 30_000,
  },
  projects: [
    {
      name: "desktop",
      use: { ...baseUse, viewport: { width: 1440, height: 900 } },
    },
    {
      name: "minimum-desktop",
      use: { ...baseUse, viewport: { width: 1024, height: 768 } },
    },
    {
      name: "wide",
      use: { ...baseUse, viewport: { width: 1920, height: 1080 } },
    },
    {
      name: "tablet-mobile",
      use: {
        ...baseUse,
        viewport: { width: 768, height: 1024 },
        hasTouch: true,
        isMobile: true,
      },
    },
    {
      name: "mobile",
      use: {
        ...baseUse,
        viewport: { width: 390, height: 844 },
        hasTouch: true,
        isMobile: true,
      },
    },
    {
      name: "narrow-mobile",
      use: {
        ...baseUse,
        viewport: { width: 320, height: 568 },
        hasTouch: true,
        isMobile: true,
      },
    },
  ],
});
