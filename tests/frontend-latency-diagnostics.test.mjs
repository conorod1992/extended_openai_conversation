import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {LATENCY_ROUTES} from "../ci/frontend_latency/routes.mjs";
import {NAVIGATION} from "../custom_components/extended_openai_conversation_responses/frontend/frontend-navigation.js";

const workflow = await readFile(".github/workflows/frontend-latency-diagnostics.yml", "utf8");
const pythonHarness = await readFile("ci/frontend_latency/test_latency_diagnostics.py", "utf8");
const browserHarness = await readFile("ci/frontend_latency/latency.spec.mjs", "utf8");
const routeManifest = await readFile("ci/frontend_latency/routes.mjs", "utf8");
const routeSmoke = await readFile("tests_browser/latency-route-readiness.spec.mjs", "utf8");
const comparison = await readFile("ci/frontend_latency/compare.py", "utf8");

for (const route of [
  "overview",
  "assistant-basics",
  "assistant-model-responses",
  "assistant-conversation",
  "assistant-prompt-context",
  "assistant-voice",
  "assistant-speech",
  "capabilities-home-assistant",
  "capabilities-web-skills",
  "capabilities-request-rules",
  "capabilities-functions",
  "capabilities-guest-mode",
  "capabilities-quiet-hours",
  "data-memory-memories",
  "data-memory-memory-settings",
  "data-memory-knowledge",
  "conversation-history",
  "usage-maintenance-usage",
  "usage-maintenance-diagnostics",
  "usage-maintenance-backup-restore",
  "usage-maintenance-retention",
  "usage-maintenance-request-debug",
]) {
  assert.match(routeManifest, new RegExp(`name: "${route}"`), route);
}

for (const operation of [
  "agents",
  "overview_summary",
  "configuration_get",
  "configuration_live_local_handling",
  "configuration_live_exposed_attributes",
  "guest_mode_get",
  "request_rules_list",
  "knowledge_list",
  "quiet_hours_get",
  "usage_summary",
  "usage_daily",
  "usage_runs",
  "usage_retention",
  "scopes_catalog",
  "memories_list",
  "temporary_memories_list",
  "conversations_list",
  "conversations_active",
]) {
  assert.match(pythonHarness, new RegExp(`"${operation}"`), operation);
}

assert.match(workflow, /baseline_ref:/);
assert.doesNotMatch(workflow, /candidate_ref:/);
assert.match(workflow, /Measure baseline/);
assert.match(workflow, /Measure current develop/);
assert.match(comparison, /baseline ms \| current ms/);
assert.match(comparison, /Negative deltas mean current develop is faster than the baseline/);

assert.match(pythonHarness, /label == "baseline" and optional_on_baseline/);
assert.match(pythonHarness, /"supported": False/);

assert.match(browserHarness, /baselineMode = label === "baseline"/);
assert.match(browserHarness, /supported: false/);
assert.match(browserHarness, /if \(!baselineMode\)/);
assert.match(comparison, /Backend operations or browser routes unavailable on the historical baseline are shown as `n\/a`/);

assert.match(browserHarness, /async function closeContext/);
assert.match(browserHarness, /baselineMode \? 2500 : 30000/);
assert.match(browserHarness, /waitForManagementRouteReady/);
assert.doesNotMatch(routeManifest, /ready:/);
assert.match(routeSmoke, /for \(const route of LATENCY_ROUTES\)/);
const latencyPlaywrightConfig = await readFile("ci/frontend_latency/playwright.config.mjs", "utf8");
assert.match(latencyPlaywrightConfig, /timeout: 240_000/);

const closeContextBody = browserHarness.match(/async function closeContext\(context\) \{([\s\S]*?)\n\}/)?.[1] || "";
assert.match(closeContextBody, /await context\.close\(\)/);
assert.doesNotMatch(closeContextBody, /await closeContext\(context\)/);

const currentManagementPaths = new Set(
  NAVIGATION.flatMap((page) => page.sections.length
    ? page.sections.map((section) => `${page.id}/${section.id}`)
    : page.id === "overview" ? ["overview"] : []),
);
for (const route of LATENCY_ROUTES) {
  assert.ok(currentManagementPaths.has(route.path), `stale latency route: ${route.path}`);
}
const latencyPaths = new Set(LATENCY_ROUTES.map((route) => route.path));
assert.equal(latencyPaths.size, LATENCY_ROUTES.length);
assert.deepEqual([...latencyPaths].sort(), [...currentManagementPaths].sort());

assert.match(browserHarness, /baselineMode && routeMismatch/);
assert.match(browserHarness, /Latency route \$\{route\.path\} failed readiness/);
assert.doesNotMatch(routeManifest, /assistant\/advanced/);
