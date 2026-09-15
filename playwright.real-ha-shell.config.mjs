import {defineConfig, devices} from "@playwright/test";

function artifactSuffix() {
  const spec = process.argv.find((arg) => arg.endsWith(".spec.mjs")) || "";
  const pytestTest = (process.env.PYTEST_CURRENT_TEST || "").replace(/\s+\([^)]*\)$/, "");
  const raw = [
    process.env.PLAYWRIGHT_ARTIFACT_SUFFIX,
    spec,
    process.env.REAL_HA_RUNTIME_RELOAD_PHASE,
    pytestTest,
  ].filter(Boolean).join("-");
  if (!raw) return "";
  const safe = raw.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
  return safe.length <= 180 ? safe : `${safe.slice(0, 140)}-${safe.slice(-39)}`;
}

const artifactKey = artifactSuffix();
const outputDir = artifactKey ? `test-results/${artifactKey}` : "test-results";
const reportFolder = artifactKey ? `playwright-report/${artifactKey}` : "playwright-report";

export default defineConfig({
  testDir: "./tests_browser",
  testMatch: "real-ha-shell.spec.mjs",
  outputDir,
  fullyParallel: false,
  workers: 1,
  timeout: 45_000,
  expect: {
    timeout: 10_000,
  },
  reporter: process.env.CI
    ? [["line"], ["html", {outputFolder: reportFolder, open: "never"}]]
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
