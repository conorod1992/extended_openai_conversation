import assert from "node:assert/strict";

import {syncRequestRuleRoutingControls} from "../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui.js";

const requestOption = {disabled: false};
const actionType = {value: "model_routing"};
const matchType = {value: "equals"};
const scope = {
  value: "request",
  disabled: false,
  querySelector: (selector) => selector === 'option[value="request"]' ? requestOption : null,
};
const continueToAi = {checked: false};
const help = {textContent: ""};
const elements = new Map([
  ["#rule-action-type", actionType],
  ["#rule-match", matchType],
  ["#rule-scope", scope],
  ["#rule-continue-to-ai", continueToAi],
  ["#rule-reasoning", null],
  ["#rule-routing-scope-help", help],
]);
const root = {querySelector: (selector) => elements.get(selector) ?? null};

syncRequestRuleRoutingControls(root);
assert.equal(scope.disabled, true);
assert.equal(scope.value, "conversation");
assert.equal(requestOption.disabled, true);
assert.match(help.textContent, /not sent to the AI provider/);

continueToAi.checked = true;
syncRequestRuleRoutingControls(root);
assert.equal(scope.disabled, false);
assert.equal(requestOption.disabled, false);
assert.match(help.textContent, /original request to the AI unchanged/);

scope.value = "request";
syncRequestRuleRoutingControls(root);
assert.equal(scope.value, "request");
assert.equal(scope.disabled, false);

matchType.value = "sentence_pattern";
continueToAi.checked = false;
syncRequestRuleRoutingControls(root);
assert.equal(scope.disabled, true);
assert.equal(scope.value, "conversation");

continueToAi.checked = true;
syncRequestRuleRoutingControls(root);
assert.equal(scope.disabled, false);
assert.equal(requestOption.disabled, false);
