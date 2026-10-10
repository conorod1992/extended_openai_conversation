import assert from "node:assert/strict";
import {requestRuleCapabilityModel} from "../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui-impl.js";
const panel = {_result: {effective_model: "gpt-6-astra"}};
assert.equal(requestRuleCapabilityModel(panel), "gpt-6-astra");
assert.equal(requestRuleCapabilityModel(panel, "gpt-5-pro"), "gpt-5-pro");
assert.equal(requestRuleCapabilityModel(panel, "{model}"), "{model}");
assert.equal(requestRuleCapabilityModel({_result:{config:{azure_model:"gpt-4.1",chat_model:"deployment"}}}), "gpt-4.1");
