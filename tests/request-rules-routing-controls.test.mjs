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


const {syncRequestRuleRoutingControls: actualEditorControls} = await import("../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui-impl.js");
const continueMatching = {checked: false};
elements.set("#rule-continue-matching", continueMatching);
for (const controls of [actualEditorControls, syncRequestRuleRoutingControls]) {
  for (const match of ["equals", "starts_with", "ends_with", "contains", "sentence_pattern"]) {
    for (const ai of [false, true]) for (const matching of [false, true]) {
      matchType.value = match; continueToAi.checked = ai; continueMatching.checked = matching; scope.value = "request";
      controls(root);
      assert.equal(requestOption.disabled, !ai && !matching, `${match}: ai=${ai}, matching=${matching}`);
      assert.equal(scope.value, !ai && !matching ? "conversation" : "request");
    }
  }
  const reasoning = {value: "high", ownerDocument:{createElement:()=>({})}, replaceChildren(...options){this.options=options;}};
  elements.set("#rule-reasoning", reasoning);
  controls(root, []);
  assert.equal(reasoning.value, "");
  assert.deepEqual(reasoning.options.map(option => option.value), [""]);
  reasoning.value = "{effort}";
  controls(root, ["low", "high"]);
  assert.equal(reasoning.value, "{effort}");
  elements.set("#rule-reasoning", null);
}
