import assert from "node:assert/strict";

import {
  commonCapturedSlotNames,
  loadRequestRuleActions,
  readRequestRuleActions,
} from "../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui-impl.js";

assert.deepEqual(
  commonCapturedSlotNames("ask {question}\nanswer {question}"),
  ["question"],
);
assert.deepEqual(
  commonCapturedSlotNames("ask {question} about {topic}\nanswer {question}"),
  ["question"],
);
assert.deepEqual(
  commonCapturedSlotNames("ask {question}\nanswer {topic}"),
  [],
);
assert.deepEqual(commonCapturedSlotNames(""), []);

const actions = [
  {
    action: "extended_openai_conversation_responses.call_function",
    data: {function: "battery", arguments: {entity: "{{ request.slots.device }}"}},
    response_variable: "battery",
  },
  {
    if: [{condition: "template", value_template: "{{ battery.result is number }}"}],
    then: [{set_conversation_response: "Battery at {{ battery.result }} percent"}],
  },
];
const selector = {};
loadRequestRuleActions(selector, {action: {actions}});
assert.deepEqual(readRequestRuleActions(selector), actions);
assert.notEqual(readRequestRuleActions(selector), actions);
selector.value[1].then[0].set_conversation_response = "{{ battery.result.level }}";
assert.equal(actions[1].then[0].set_conversation_response, "Battery at {{ battery.result }} percent");

console.log("Request Rule captures and native Function response actions passed");
