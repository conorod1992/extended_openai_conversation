import assert from "node:assert/strict";
import {requestRuleCapabilityModel} from "../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui-impl.js";
const panel = {_result: {effective_model: "gpt-6-astra"}};
assert.equal(requestRuleCapabilityModel(panel), "gpt-6-astra");
assert.equal(requestRuleCapabilityModel(panel, "gpt-5-pro"), "gpt-5-pro");
assert.equal(requestRuleCapabilityModel(panel, "{model}"), "{model}");
assert.equal(requestRuleCapabilityModel({_result:{config:{azure_model:"gpt-4.1",chat_model:"deployment"}}}), "gpt-4.1");

const azure = {_result: {api_provider: "azure", effective_model: "gpt-5.6"}};
for (const deployment of ["home-deployment", "another-deployment", "{deployment}", ""]) {
  assert.equal(requestRuleCapabilityModel(azure, deployment), "gpt-5.6");
}
assert.equal(requestRuleCapabilityModel({_result: {api_provider: "openai", effective_model: "gpt-5.6"}}, "gpt-4o"), "gpt-4o");

const {isFunctionServiceAction} = await import("../custom_components/extended_openai_conversation_responses/frontend/script-payload.js");
for (const field of ["action", "service", "service_template"]) {
  assert.equal(isFunctionServiceAction({[field]: "EXTENDED_OPENAI_CONVERSATION_RESPONSES.CALL_FUNCTION"}), true);
  assert.equal(isFunctionServiceAction({[field]: "script.CALL_FUNCTION"}), false);
}
assert.equal(isFunctionServiceAction({event_data: {action: "EXTENDED_OPENAI_CONVERSATION_RESPONSES.CALL_FUNCTION"}}), false);
assert.equal(isFunctionServiceAction({action: "{{ 'extended_openai_conversation_responses.call_function' }}"}), false);

assert.equal(requestRuleCapabilityModel({_result: {api_provider:"openai", effective_model:"gpt-5.6", config:{azure_model:"stale-model"}}}, "gpt-4o"), "gpt-4o");
