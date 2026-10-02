import {defineConfig, devices} from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: ["latency.spec.mjs", "accessibility.spec.mjs"],
  outputDir: "../../latency-results/playwright",
  fullyParallel: false,
  workers: 1,
  timeout: 540_000,
  expect: {timeout: 30_000},
  reporter: process.env.ENHANCED_EXECUTION_EVIDENCE === "1"
    ? [["line"], ["../playwright_execution.mjs"]] : "line",
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
