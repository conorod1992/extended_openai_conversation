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
  outputDir,
  fullyParallel: false,
  workers: process.env.CI ? 1 : undefined,
  timeout: 30_000,
  expect: {
    timeout: 7_500,
  },
  reporter: process.env.CI
    ? [["line"], ["html", {outputFolder: reportFolder, open: "never"}]]
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
      name: "chromium",
      use: {...devices["Desktop Chrome"]},
    },
  ],
});
