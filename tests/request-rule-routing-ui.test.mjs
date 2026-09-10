import assert from "node:assert/strict";

import {
  requestRulesDialog,
  syncRequestRuleRoutingControls,
} from "../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui.js";

function makeSelect(value, optionValues = []) {
  const options = optionValues.map((optionValue) => ({value:optionValue,disabled:false,textContent:optionValue}));
  return {
    value,
    disabled:false,
    options,
    ownerDocument:{createElement:() => ({value:"",disabled:false,textContent:""})},
    querySelector(selector) {
      const match = selector.match(/^option\[value="(.*)"\]$/);
      return match ? this.options.find((option) => option.value === match[1]) || null : null;
    },
    append(option) { this.options.push(option); },
  };
}

const actionType = makeSelect("model_routing");
const matchType = makeSelect("equals");
const scope = makeSelect("request", ["request", "conversation"]);
const model = {value:"gpt-6-astra"};
const reasoning = makeSelect("max", ["", "low", "medium", "high"]);
const reset = {checked:false};
const help = {textContent:""};
const elements = {
  "#rule-action-type": actionType,
  "#rule-match": matchType,
  "#rule-scope": scope,
  "#rule-model": model,
  "#rule-reasoning": reasoning,
  "#rule-reset": reset,
  "#rule-routing-scope-help": help,
};
const root = {querySelector:(selector) => elements[selector] || null};

syncRequestRuleRoutingControls(root, ["low", "medium", "high", "xhigh", "max"]);
assert.equal(scope.value, "conversation");
assert.equal(scope.disabled, true);
assert.equal(scope.querySelector('option[value="request"]').disabled, true);
assert.match(help.textContent, /complete routing command/i);
assert.match(help.textContent, /not sent to the AI provider/i);
assert.deepEqual(reasoning.options.map((option) => option.value), ["", "low", "medium", "high", "xhigh", "max"]);
assert.equal(reasoning.querySelector('option[value="xhigh"]').disabled, false);
assert.equal(reasoning.querySelector('option[value="max"]').disabled, false);

matchType.value = "starts_with";
scope.value = "request";
syncRequestRuleRoutingControls(root, ["low", "medium", "high", "xhigh", "max"]);
assert.equal(scope.disabled, false);
assert.equal(scope.querySelector('option[value="request"]').disabled, false);
assert.match(help.textContent, /original request to the AI unchanged/i);

model.value = "gpt-5.6";
reasoning.value = "max";
syncRequestRuleRoutingControls(root, ["low", "medium", "high"]);
assert.equal(reasoning.querySelector('option[value="xhigh"]').disabled, true);
assert.equal(reasoning.querySelector('option[value="max"]').disabled, true);
assert.equal(reasoning.value, "");

model.value = "";
syncRequestRuleRoutingControls(root, ["low", "medium", "high", "xhigh", "max"]);
assert.equal(reasoning.querySelector('option[value="max"]').disabled, false);

const dialog = requestRulesDialog({});
assert.match(dialog, /complete commands/);
assert.match(dialog, /original request to the provider/);
assert.match(dialog, /rule-routing-scope-help/);
