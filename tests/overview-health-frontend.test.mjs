import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

import {buildSetupHealth} from "../custom_components/extended_openai_conversation_responses/frontend/overview-health.js";
import {renderOverview} from "../custom_components/extended_openai_conversation_responses/frontend/overview-page-impl.js";

const defaultFacts = {
  provider_runtime: {client_loaded: true, provider: "openai", model: "gpt-5-mini"},
  prompt_state: "starter",
  exposed_entity_count: 1,
  memory: {mode: "off", available: true},
  knowledge: {enabled: false, source_count: 0, available: true},
  web_search: {enabled: false, available: false, reason: "requires_responses"},
  can_manage: true,
  live_provider_tested: false,
};

const defaultHealth = buildSetupHealth(defaultFacts);
const defaultChecks = Object.fromEntries(defaultHealth.checks.map((check) => [check.id, check]));
assert.equal(defaultHealth.state, "ready");
assert.equal(defaultHealth.summary, "Ready");
assert.equal(defaultChecks.provider_runtime.state, "ready");
assert.equal(defaultChecks.instructions.value, "Starter instructions");
assert.equal(defaultChecks.home_assistant_exposure.value, "1 entity exposed to Assist");
assert.equal(defaultChecks.memory.state, "neutral");
assert.equal(defaultChecks.memory.value, "Off by choice");
assert.equal(defaultChecks.knowledge.state, "neutral");
assert.equal(defaultChecks.web_search.state, "neutral");

const warningFacts = {
  ...defaultFacts,
  prompt_state: "empty",
  exposed_entity_count: 0,
  knowledge: {enabled: true, source_count: 0, available: true},
  web_search: {
    enabled: true,
    available: false,
    reason: "requires_responses",
    message: "Web Search requires the Responses API.",
  },
};
const warningHealth = buildSetupHealth(warningFacts);
const warningChecks = Object.fromEntries(warningHealth.checks.map((check) => [check.id, check]));
assert.equal(warningHealth.state, "warning");
assert.equal(warningHealth.warning_count, 4);
assert.equal(warningChecks.instructions.state, "warning");
assert.equal(warningChecks.home_assistant_exposure.state, "warning");
assert.equal(warningChecks.knowledge.value, "Enabled, no sources");
assert.equal(warningChecks.web_search.action.target, "config-api_mode");

const unknownHealth = buildSetupHealth({
  ...defaultFacts,
  exposed_entity_count: null,
  memory: {mode: "automatic", available: false},
  knowledge: {enabled: true, source_count: 0, available: false},
});
const unknownChecks = Object.fromEntries(unknownHealth.checks.map((check) => [check.id, check]));
assert.equal(unknownHealth.state, "warning");
assert.equal(unknownHealth.unknown_count, 3);
assert.equal(unknownChecks.home_assistant_exposure.state, "unknown");
assert.equal(unknownChecks.memory.state, "unknown");
assert.equal(unknownChecks.knowledge.state, "unknown");
assert.equal(unknownChecks.knowledge.value, "Unable to determine");

const unavailableHealth = buildSetupHealth({unavailable: true, can_manage: true});
assert.equal(unavailableHealth.state, "warning");
assert.equal(unavailableHealth.unknown_count, 1);
assert.equal(unavailableHealth.checks[0].id, "setup_health");

const panel = {
  _result: {
    usage: {today: {total_tokens: 0}, month: {total_tokens: 0}},
    conversations: {archive_retention_days: 30},
    load_errors: [],
    setup_health: warningFacts,
  },
  _e(value) {
    return String(value ?? "").replace(/[&<>"']/g, (character) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[character]);
  },
  _titleCase(value) {
    return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
  },
};

const agent = {
  title: "Kitchen Assistant",
  provider: "openai",
  model: "gpt-5-mini",
  memory_mode: "off",
  memory_count: 0,
  knowledge_source_count: 0,
  function_count: 0,
  function_group_count: 0,
  archive_enabled: false,
  guest_mode: {state: "inactive", has_home_assistant_exclusions: false},
};

const markup = renderOverview(panel, agent);
assert.match(markup, /Setup & health/);
assert.match(markup, /Optional features that are off by choice are not treated as problems/);
assert.match(markup, /Off by choice/);
assert.match(markup, /Run live test/);
assert.match(markup, /data-target="config-api_mode"/);
assert.match(markup, /<ha-icon icon="mdi:information-outline" aria-hidden="true"><\/ha-icon> Connection tests only run when you start one from Diagnostics\./);
assert.match(markup, /<strong>Review recommended<\/strong><span>4 issues to review<\/span>/);

panel._result.setup_health = {
  ...defaultFacts,
  exposed_entity_count: null,
  knowledge: {enabled: true, source_count: 0, available: false},
  can_manage: false,
};
const nonAdminMarkup = renderOverview(panel, agent);
assert.match(nonAdminMarkup, /Unable to determine/);
assert.match(nonAdminMarkup, /<strong>Status incomplete<\/strong><span>2 checks unavailable<\/span>/);
assert.match(nonAdminMarkup, /Connection tests only run when you start one from Diagnostics\./);
assert.doesNotMatch(nonAdminMarkup, /<button[^>]*class="[^"]*setup-health-action/);

const source = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/overview-page-impl.js", import.meta.url),
  "utf8",
);
assert.match(source, /_pendingSettingFocus = button\.dataset\.target/);
assert.doesNotMatch(source, /diagnostics.*test_agent/s);

const claritySource = await readFile(
  new URL(
    "../custom_components/extended_openai_conversation_responses/frontend/management-overview-health-clarity.js",
    import.meta.url,
  ),
  "utf8",
);
assert.doesNotMatch(
  claritySource,
  /clarifySetupHealthSummary|setup-health-check-(?:error|warning|unknown)/,
  "Overview health summary should be final at render time, not rewritten from DOM classes",
);
assert.match(
  claritySource,
  /<h2>Model Data<\/h2>/,
  "Overview should surface model catalogue health as its own card",
);
assert.match(claritySource, /status: "Update available"/);
assert.match(claritySource, /The newer catalogue is waiting for approval/);
assert.match(claritySource, /action: Number\.isInteger\(available\) \? `Apply v\$\{available\}` : "Apply update"/);
assert.match(
  claritySource,
  /lookupModelData\(panel, model, "apply"\)/,
  "the Overview apply action must explicitly activate the staged catalogue",
);
assert.match(claritySource, /status: "Using bundled data"/);
assert.match(claritySource, /status: "Current"/);
assert.match(claritySource, /status: "Check failed"/);
assert.match(
  claritySource,
  /panel\._navigate\("assistant", "model-responses"\)/,
  "the Model Data card should open the page that contains its catalogue controls",
);
assert.match(
  claritySource,
  /panel\?\._data\?\.is_admin === false/,
  "the Model Data card should respect the existing admin-only management boundary",
);
assert.match(
  claritySource,
  /panel\._modelCatalogData\?\.requested_model === model/,
  "Overview should reuse model data already loaded for the selected model",
);


const overviewEntrySource = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/overview-page.js", import.meta.url),
  "utf8",
);
assert.match(
  overviewEntrySource,
  /import\("\.\/management-overview-health-clarity\.js"\)/,
  "Model Data health should load only after the full Overview implementation is available",
);
assert.match(overviewEntrySource, /renderOverviewSnapshot/);
assert.match(overviewEntrySource, /Detailed health, usage, and stored-data counts are still loading/);
assert.doesNotMatch(
  source,
  /management-overview-health-clarity/,
  "Overview implementation must not statically pull Model Data/catalog work into first useful paint",
);
