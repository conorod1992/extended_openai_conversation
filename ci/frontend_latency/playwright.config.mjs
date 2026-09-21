import {defineConfig, devices} from "@playwright/test";

export default defineConfig({
  testDir: "./ci/frontend_latency",
  testMatch: "latency.spec.mjs",
  outputDir: "latency-results/playwright",
  fullyParallel: false,
  workers: 1,
  timeout: 90_000,
  expect: {timeout: 30_000},
  reporter: "line",
  use: {
    trace: "off",
    screenshot: "off",
  },
  projects: [
    {
      name: "chromium",
      use: {...devices["Desktop Chrome"]},
    },
  ],
});
