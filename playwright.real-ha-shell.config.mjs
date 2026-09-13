import {defineConfig, devices} from "@playwright/test";

export default defineConfig({
  testDir: "./tests_browser",
  testMatch: "real-ha-shell.spec.mjs",
  fullyParallel: false,
  workers: 1,
  timeout: 45_000,
  expect: {
    timeout: 10_000,
  },
  reporter: process.env.CI
    ? [["line"], ["html", {outputFolder: "playwright-report", open: "never"}]]
    : "list",
  use: {
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {...devices["Desktop Chrome"]},
    },
  ],
});
