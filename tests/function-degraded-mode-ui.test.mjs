import assert from "node:assert/strict";
import test from "node:test";

import {buildSetupHealth} from "../custom_components/extended_openai_conversation_responses/frontend/overview-health.js";
import {
  renderFunctionRepairCards,
  invalidToolCards,
} from "../custom_components/extended_openai_conversation_responses/frontend/management-function-repair.js";
import {renderTools} from "../custom_components/extended_openai_conversation_responses/frontend/agent-config-tools.js";

const panel = {
  _e(value) {
    return String(value)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  },
};

const repair = {
  isolatable: true,
  invalid_tools: [{
    index: 4,
    name: "create_recurring_reminder",
    validation_error: "unknown native implementation: reminders.create_recurring",
    yaml: "spec:\n  name: create_recurring_reminder\n",
  }],
  persisted_groups: [{
    id: "reminders",
    name: "Reminders",
    functions: ["valid_reminder", "create_recurring_reminder"],
  }],
};

test("invalid Function Tools render in a Needs attention group with repair-only actions", () => {
  const html = invalidToolCards(panel, repair);

  assert.match(html, /Needs attention/);
  assert.match(html, /create_recurring_reminder/);
  assert.match(html, /Needs repair/);
  assert.match(html, /Assigned to: Reminders/);
  assert.match(html, /actions tool-card-actions/);
  assert.match(html, /edit-invalid-tool/);
  assert.match(html, /danger delete-invalid-tool/);
  assert.doesNotMatch(html, /duplicate-tool/);
  assert.doesNotMatch(html, /tool-enabled/);
});

test("Needs attention group is inserted before normal Function Groups", () => {
  const decoratedPanel = {...panel, _empty:String, _result: {function_repair: repair}};
  const html = renderTools(decoratedPanel, {repairCards:renderFunctionRepairCards(decoratedPanel)});

  assert.ok(html.indexOf("Needs attention") < html.indexOf('class="function-group-card always-card"'));
  assert.match(html, /<div class="function-groups"><article class="function-group-card function-repair-attention"/);
});

test("Overview reports quarantined Function Tools without treating valid siblings as unavailable", () => {
  const health = buildSetupHealth({
    provider_runtime: {client_loaded: true, provider: "openai", model: "gpt-5.6-luna"},
    function_tools: {
      usable_count: 30,
      invalid_count: 1,
      total_count: 31,
      isolatable: true,
      validation_error: "unknown native implementation",
    },
    prompt_state: "starter",
    exposed_entity_count: 4,
    memory: {mode: "off", available: true},
    knowledge: {enabled: false, available: true, source_count: 0},
    web_search: {enabled: false},
    can_manage: true,
  });

  const functions = health.checks.find((check) => check.id === "function_tools");
  assert.equal(functions.state, "warning");
  assert.equal(functions.value, "1 function needs repair");
  assert.match(functions.detail, /30 valid Function Tools remain available/);
  assert.equal(health.checks.find((check) => check.id === "provider_runtime").state, "ready");
});
