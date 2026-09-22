import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import {LATENCY_ROUTES, routeStateMismatch} from "../ci/frontend_latency/routes.mjs";
import {NAVIGATION} from "../custom_components/extended_openai_conversation_responses/frontend/frontend-navigation.js";

const currentManagementPaths = new Set(
  NAVIGATION.flatMap((page) => page.sections.length
    ? page.sections.map((section) => `${page.id}/${section.id}`)
    : page.id === "overview" ? ["overview"] : []),
);
const latencyPaths = LATENCY_ROUTES.map((route) => route.path);
assert.equal(new Set(latencyPaths).size, LATENCY_ROUTES.length, "each route is measured once");
assert.equal(new Set(LATENCY_ROUTES.map((route) => route.name)).size, LATENCY_ROUTES.length, "metric names must be unique");
assert.deepEqual([...latencyPaths].sort(), [...currentManagementPaths].sort(), "measure every current management route");
for (const route of LATENCY_ROUTES) {
  assert.ok(route.name?.trim(), `missing metric name for ${route.path}`);
  const [page, subsection = null] = route.path.split("/");
  assert.equal(routeStateMismatch({page, subsection}, route), false, `${route.path} is ready on its intended route`);
  assert.equal(routeStateMismatch({page:"other", subsection}, route), true, `${route.path} rejects a stale route`);
}

// These operation names are external WebSocket contracts used by the genuine-HA
// benchmark. The harness must still exercise the full backend operation set.
const pythonHarness = await readFile("ci/frontend_latency/test_latency_diagnostics.py", "utf8");
for (const operation of [
  "agents", "overview_summary", "configuration_get", "configuration_live_local_handling",
  "configuration_live_exposed_attributes", "guest_mode_get", "request_rules_list",
  "knowledge_list", "quiet_hours_get", "usage_summary", "usage_daily", "usage_runs",
  "usage_retention", "scopes_catalog", "memories_list", "temporary_memories_list",
  "conversations_list", "conversations_active",
]) {
  assert.match(pythonHarness, new RegExp(`"${operation}"`), `benchmark must exercise ${operation}`);
}

// The manual workflow must still compare a historical baseline with develop.
const workflow = await readFile(".github/workflows/frontend-latency-diagnostics.yml", "utf8");
assert.match(workflow, /baseline_ref:/);
assert.match(workflow, /Measure baseline/);
assert.match(workflow, /Measure current develop/);
