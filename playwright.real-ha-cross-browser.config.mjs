import {defineConfig} from "@playwright/test";

export default defineConfig({
  testDir: "./tests_browser",
  testMatch: ["real-ha-cross-browser-handoff.spec.mjs"],
  fullyParallel: false,
  workers: 1,
  timeout: 180_000,
  expect: {timeout: 15_000},
  reporter: process.env.CI ? [["line"]] : "list",
  use: {
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
});
