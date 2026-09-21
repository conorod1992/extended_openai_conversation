import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const workflow = await readFile(".github/workflows/frontend-latency-diagnostics.yml", "utf8");
const pythonHarness = await readFile("ci/frontend_latency/test_latency_diagnostics.py", "utf8");
const browserHarness = await readFile("ci/frontend_latency/latency.spec.mjs", "utf8");
const comparison = await readFile("ci/frontend_latency/compare.py", "utf8");

for (const route of [
  "overview",
  "assistant-basics",
  "assistant-model-responses",
  "assistant-conversation",
  "assistant-prompt-context",
  "assistant-voice",
  "assistant-speech",
  "assistant-advanced",
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
  assert.match(browserHarness, new RegExp(`name: "${route}"`), route);
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
