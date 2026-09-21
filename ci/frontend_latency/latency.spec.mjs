import {mkdir, writeFile} from "node:fs/promises";
import {dirname} from "node:path";
import {expect, test} from "@playwright/test";

const baseUrl = process.env.REAL_HA_FRONTEND_URL;
const authDataRaw = process.env.REAL_HA_FRONTEND_AUTH;
const output = process.env.EOAI_BROWSER_LATENCY_OUTPUT;
const label = process.env.EOAI_LATENCY_LABEL || "current";
const baselineMode = label === "baseline";
const runs = Math.max(1, Number(process.env.EOAI_LATENCY_RUNS || "3"));

test.skip(!baseUrl || !authDataRaw || !output, "requires the manual genuine-HA latency harness");

async function closeContext(context) {
  try {
    await context.close();
  } catch (error) {
    const message = error?.message || String(error);
    if (!message.includes("Failed to find context with id")) throw error;
  }
}

const routes = [
  {name: "overview", path: "overview", ready: ".dashboard-grid"},

  {name: "assistant-basics", path: "assistant/basics", ready: '[data-config="__title"]'},
  {name: "assistant-model-responses", path: "assistant/model-responses", ready: "#reset-model-parameters"},
  {name: "assistant-conversation", path: "assistant/conversation", ready: '[data-config="conversation_continuity"]'},
  {name: "assistant-prompt-context", path: "assistant/prompt-context", ready: "#prompt-editor"},
  {name: "assistant-voice", path: "assistant/voice", ready: "#voice-mappings"},
  {name: "assistant-speech", path: "assistant/speech", ready: "#preview-speech"},
  {name: "assistant-advanced", path: "assistant/advanced", ready: "#reset-advanced"},

  {name: "capabilities-home-assistant", path: "capabilities/home-assistant", ready: '[data-config="local_intents_enabled"]'},
  {name: "capabilities-web-skills", path: "capabilities/web-skills", ready: '[data-config="web_search"]'},
  {name: "capabilities-request-rules", path: "capabilities/request-rules", ready: "#rule-search"},
  {name: "capabilities-functions", path: "capabilities/functions", ready: "#tool-search"},
  {name: "capabilities-guest-mode", path: "capabilities/guest-mode", ready: "#guest-start"},
  {name: "capabilities-quiet-hours", path: "capabilities/quiet-hours", ready: "#qh-enabled"},

  {name: "data-memory-memories", path: "data-memory/memories", ready: "#add-memory"},
  {name: "data-memory-memory-settings", path: "data-memory/memory-settings", ready: '[data-memory-config="memory_mode"]'},
  {name: "data-memory-knowledge", path: "data-memory/knowledge", ready: "#add-source"},
  {name: "conversation-history", path: "data-memory/conversations", ready: "#archive-query"},

  {name: "usage-maintenance-usage", path: "usage-maintenance/usage", ready: "#usage-window"},
  {name: "usage-maintenance-diagnostics", path: "usage-maintenance/diagnostics", ready: "#test-agent"},
  {name: "usage-maintenance-backup-restore", path: "usage-maintenance/backup-restore", ready: "#create-backup-transfer"},
  {name: "usage-maintenance-retention", path: "usage-maintenance/retention", ready: '[data-config="usage_request_retention_days"]'},
  {name: "usage-maintenance-request-debug", path: "usage-maintenance/request-debug", ready: "extended-openai-debug-panel #refresh"},
];

async function measureRoute(browser, authData, route, iteration) {
  const context = await browser.newContext();
  await context.addInitScript((tokens) => {
    localStorage.setItem("hassTokens", JSON.stringify(tokens));
    window.__eoaiLcp = [];
    try {
      const observer = new PerformanceObserver((list) => {
        for (const entry of list.getEntries()) window.__eoaiLcp.push(entry.startTime);
      });
      observer.observe({type: "largest-contentful-paint", buffered: true});
    } catch {
      // LCP is advisory; route timing and resource timing remain available.
    }
  }, authData);

  const page = await context.newPage();
  const failures = [];
  page.on("requestfailed", (request) => {
    if (request.url().includes("extended_openai_conversation_responses")) {
      failures.push(`${request.method()} ${request.url()}: ${request.failure()?.errorText || "failed"}`);
    }
  });

  const wallStarted = Date.now();
  try {
    await page.goto(`${baseUrl}/extended-openai/${route.path}`, {waitUntil: "domcontentloaded"});
    await expect(page.locator("extended-openai-management-panel")).toHaveCount(1);
    await expect(page.locator(`extended-openai-management-panel ${route.ready}`))
      .toBeVisible({timeout: baselineMode ? 2500 : 30000});
  } catch (error) {
    if (!baselineMode) {
      await closeContext(context);
      throw error;
    }
    await closeContext(context);
    return {
      route: route.name,
      iteration,
      supported: false,
      unavailable_reason: error?.message || String(error),
      failures,
    };
  }
  const wallReadyMs = Date.now() - wallStarted;
  await page.waitForTimeout(100);

  const browserMetrics = await page.evaluate(() => {
    const nav = performance.getEntriesByType("navigation")[0];
    const marks = Object.fromEntries(
      performance.getEntriesByType("mark")
        .filter((entry) => entry.name.startsWith("extended-openai:"))
        .map((entry) => [entry.name, entry.startTime]),
    );
    const measures = performance.getEntriesByType("measure")
      .filter((entry) => entry.name.startsWith("extended-openai:"))
      .map((entry) => ({name: entry.name, startTime: entry.startTime, duration: entry.duration}));
    const resources = performance.getEntriesByType("resource")
      .filter((entry) => entry.name.includes("/extended_openai_conversation_responses/"))
      .map((entry) => ({
        name: new URL(entry.name).pathname.split("/").pop(),
        startTime: entry.startTime,
        responseStart: entry.responseStart,
        responseEnd: entry.responseEnd,
        duration: entry.duration,
        transferSize: entry.transferSize,
        encodedBodySize: entry.encodedBodySize,
        initiatorType: entry.initiatorType,
      }));
    return {
      now_ms: performance.now(),
      lcp_ms: Math.max(0, ...(window.__eoaiLcp || [])),
      navigation: nav ? {
        responseStart: nav.responseStart,
        domContentLoadedEventEnd: nav.domContentLoadedEventEnd,
        loadEventEnd: nav.loadEventEnd,
      } : null,
      marks,
      measures,
      resources,
      integration_assets_response_end_ms: Math.max(0, ...resources.map((entry) => entry.responseEnd)),
    };
  });

  await closeContext(context);
  return {
    route: route.name,
    iteration,
    supported: true,
    wall_ready_ms: wallReadyMs,
    failures,
    ...browserMetrics,
  };
}

test("collect genuine HA cold-route latency diagnostics", async ({browser}) => {
  const authData = JSON.parse(authDataRaw);
  const samples = [];
  for (const route of routes) {
    for (let iteration = 1; iteration <= runs; iteration += 1) {
      const sample = await measureRoute(browser, authData, route, iteration);
      samples.push(sample);
      if (sample.supported === false) break;
    }
  }
  await mkdir(dirname(output), {recursive: true});
  await writeFile(output, JSON.stringify({label, runs, samples}, null, 2));
});
