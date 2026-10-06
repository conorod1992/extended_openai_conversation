import {defineConfig, devices} from "@playwright/test";

export default defineConfig({
  testDir: "./tests_browser",
  testMatch: ["real-ha-https-proxy.spec.mjs"],
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  expect: {timeout: 15_000},
  reporter: process.env.CI
    ? [["line"], ["html", {outputFolder:"playwright-report/https-proxy", open:"never"}]]
    : "list",
  outputDir: "test-results/https-proxy",
  use: {
    ...devices["Desktop Chrome"],
    ignoreHTTPSErrors: true,
    // Trust this lane's ephemeral local certificate before the first handshake.
    // Context-level ignoring alone makes Chromium first reject it, which HA's
    // strict event-loop exception fixture correctly treats as an error.
    launchOptions: {args: ["--ignore-certificate-errors"]},
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  projects: [{name:"chromium-https-proxy", use:{...devices["Desktop Chrome"], ignoreHTTPSErrors:true}}],
});
