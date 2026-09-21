import {mkdir, writeFile} from "node:fs/promises";
import {dirname} from "node:path";
import {expect, test} from "@playwright/test";
import {LATENCY_ROUTES, managementRouteState, waitForLatencyRoute, waitForManagementRouteReady} from "./routes.mjs";

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

    const readiness = await waitForLatencyRoute({
      route,
      baselineMode,
      waitReady: (timeout) => waitForManagementRouteReady(page, route, timeout),
      getState: () => managementRouteState(page).catch(() => null),
    });
    if (!readiness.supported) {
      await closeContext(context);
      return {
        route: route.name,
        iteration,
        supported: false,
        unavailable_reason: readiness.unavailable_reason,
        failures,
      };
    }
  } catch (error) {
    const state = await managementRouteState(page).catch(() => null);
    await closeContext(context);
    const detail = state ? ` panel=${JSON.stringify(state)}` : "";
    throw new Error(
      `Latency route ${route.path} failed readiness: ${error?.message || String(error)}${detail}`,
      {cause: error},
    );
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
  for (const route of LATENCY_ROUTES) {
    for (let iteration = 1; iteration <= runs; iteration += 1) {
      const sample = await measureRoute(browser, authData, route, iteration);
      samples.push(sample);
      if (sample.supported === false) break;
    }
  }
  await mkdir(dirname(output), {recursive: true});
  await writeFile(output, JSON.stringify({label, runs, samples}, null, 2));
});
