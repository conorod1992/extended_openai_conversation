import assert from "node:assert/strict";
import {bindModelDataControls, lookupModelData, modelDataControls} from "../custom_components/extended_openai_conversation_responses/frontend/model-catalog.js";
import {syncRequestRuleRoutingControls} from "../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui.js";

const calls = [];
let result = {source:"downloaded", catalog_version:2, last_error:null, model_capabilities:{reasoning_effort_options:["minimal","high"]}};
let failure = null;
const buttons = ["update","reset"].map((action) => ({dataset:{modelData:action}, disabled:false, addEventListener(_event, handler) { this.click = handler; }}));
const status = {textContent:""};
const panel = {
  _result:{},
  _hass:{async callWS(message) { calls.push(message); if (failure) throw failure; return result; }},
  shadowRoot:{querySelectorAll:() => buttons, querySelector:(selector) => selector === "[data-model-data-status]" ? status : {value:"gpt-future"}},
};
assert.match(modelDataControls(), /Update model data/);
assert.match(modelDataControls(), /Use bundled model data/);
let refreshes = 0;
bindModelDataControls(panel, () => { refreshes++; });
await buttons[0].click();
assert.deepEqual(calls[0], {type:"extended_openai_conversation_responses/model_catalog", action:"update", model:"gpt-future"});
assert.equal(refreshes, 1);
assert.deepEqual(panel._result.model_capabilities.reasoning_effort_options, ["minimal","high"]);
await buttons[1].click();
assert.equal(calls[1].action, "reset");
result = {last_error:"Previous catalogue retained"};
await buttons[0].click();
assert.equal(refreshes, 2);
assert.equal(status.textContent, result.last_error);
assert.ok(buttons.every((button) => !button.disabled));

failure = new Error("model_catalog_update_failed");
await buttons[0].click();
assert.equal(refreshes, 2, "a failed manual update must not be reported as updated");
assert.equal(
  status.textContent,
  "Unable to update model data. The existing model data is still in use. Check Home Assistant's internet connection and try again.",
);
assert.ok(buttons.every((button) => !button.disabled));
failure = null;

await lookupModelData(panel, "another-model");
assert.equal(calls.at(-1).model, "another-model");
assert.equal(calls.at(-1).action, "lookup");

// Future model choices supplied by Python must work without another JS release.
const select = {
  value:"minimal",
  options:[{value:""},{value:"high"}],
  ownerDocument:{createElement:() => ({value:"",disabled:false,textContent:""})},
  replaceChildren(...options) { this.options = options; },
  querySelector(selector) { return this.options.find((item) => selector === `option[value="${item.value}"]`) || null; },
};
const scopeOption = {value:"request",disabled:false};
const scope = {value:"request", disabled:false, querySelector:() => scopeOption};
const root = {querySelector:(selector) => ({"#rule-action-type":{value:"model_routing"},"#rule-match":{value:"contains"},"#rule-scope":scope,"#rule-reasoning":select})[selector]};
syncRequestRuleRoutingControls(root, ["minimal"]);
assert.equal(select.value, "minimal");
assert.deepEqual(select.options.map((item) => item.value), ["", "minimal"]);
assert.equal(select.querySelector('option[value="high"]'), null);
