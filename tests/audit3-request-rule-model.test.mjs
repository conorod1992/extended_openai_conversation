import assert from "node:assert/strict";
import {requestRuleCapabilityModel} from "../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui-impl.js";
import {setReasoningOptions} from "../custom_components/extended_openai_conversation_responses/frontend/request-rule-routing-controls.js";

const reasoning = {
  ownerDocument: {createElement: () => ({})},
  replaceChildren(...options) { this.options = options; },
};
const routingForm = {querySelector: selector => selector === "#rule-reasoning" ? reasoning : null};
setReasoningOptions(routingForm, ["low"], "high", true);
assert.equal(reasoning.value, "high");
assert.equal(reasoning.options.find(option => option.value === "high").disabled, true);
setReasoningOptions(routingForm, ["low"], "high");
assert.equal(reasoning.value, "");
const panel = {_result: {effective_model: "gpt-6-astra"}};
assert.equal(requestRuleCapabilityModel({_result: {effective_model: "gpt-4.1-mini"}}), "");
assert.equal(requestRuleCapabilityModel(panel, "gpt-5-pro"), "gpt-5-pro");
assert.equal(requestRuleCapabilityModel(panel, "{model}"), "");
assert.equal(requestRuleCapabilityModel({_result:{config:{azure_model:"gpt-4.1",chat_model:"deployment"}}}, "deployment"), "gpt-4.1");

const azure = {_result: {api_provider: "azure", configured_model:"home-deployment", azure_model:"gpt-5.6", effective_model: "gpt-5.6"}};
assert.equal(requestRuleCapabilityModel(azure, "home-deployment"), "gpt-5.6");
assert.equal(requestRuleCapabilityModel(azure), "");
for (const deployment of ["another-deployment", "gpt-4.1-mini"]) assert.equal(requestRuleCapabilityModel(azure, deployment), deployment);
assert.equal(requestRuleCapabilityModel(azure,"{deployment}","gpt-4.1-mini"),"");
assert.equal(requestRuleCapabilityModel(azure,"home-deployment","{binding}"),"");
assert.equal(requestRuleCapabilityModel(azure,"","gpt-4.1-mini"),"gpt-4.1-mini");
assert.equal(requestRuleCapabilityModel(azure,"another-deployment","gpt-4.1-mini"),"gpt-4.1-mini");
assert.equal(requestRuleCapabilityModel({_result: {api_provider: "openai", effective_model: "gpt-5.6"}}, "gpt-4o"), "gpt-4o");

assert.equal(requestRuleCapabilityModel({_result: {api_provider:"openai", effective_model:"gpt-5.6", config:{azure_model:"stale-model"}}}, "gpt-4o"), "gpt-4o");


// Current backends distinguish an empty Azure binding from an inherited model.
for (const binding of [null, ""]) {
  const unbound = {_result:{api_provider:"azure", effective_model:"gpt-4.1", azure_model:binding}};
  assert.equal(requestRuleCapabilityModel(unbound, "gpt-5.6"), "gpt-5.6");
  assert.equal(requestRuleCapabilityModel(unbound, "{deployment}"), "");
  assert.equal(requestRuleCapabilityModel(unbound), "");
}
assert.equal(requestRuleCapabilityModel({_result:{api_provider:"azure", configured_model:"deployment", effective_model:"gpt-5.6", azure_model:"gpt-5.6"}}, "deployment"), "gpt-5.6");
