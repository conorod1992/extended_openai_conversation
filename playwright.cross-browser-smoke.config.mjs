import {defineConfig, devices} from "@playwright/test";

export default defineConfig({
  testDir: "./tests_browser",
  testMatch: ["management-panel.spec.mjs", "management-crud.spec.mjs"],
  grep: /renders the shipped Guide and responds to real browser interactions|general configuration survives a fresh panel load and a rejected save can be retried|persistent memories support create, reload, edit, and delete/,
  fullyParallel: false,
  workers: 1,
  timeout: 30_000,
  expect: {
    timeout: 7_500,
  },
  reporter: process.env.CI
    ? [["line"], ["html", {outputFolder: "playwright-report", open: "never"}]]
    : "list",
  use: {
    baseURL: "http://127.0.0.1:4173",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: "python3 -m http.server 4173 --bind 127.0.0.1",
    url: "http://127.0.0.1:4173/tests_browser/fixture.html",
    reuseExistingServer: !process.env.CI,
    timeout: 15_000,
  },
  projects: [
    {
      name: "firefox",
      use: {...devices["Desktop Firefox"]},
    },
    {
      name: "webkit",
      use: {...devices["Desktop Safari"]},
    },
  ],
});
