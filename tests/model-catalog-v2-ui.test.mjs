import assert from "node:assert/strict";

import {
  apiPathSelectable,
  parameterControlState,
  pickerModels,
} from "../custom_components/extended_openai_conversation_responses/frontend/model-catalog.js";

{
  const conditional = {
    support: "conditional",
    allowed_reasoning_efforts: ["none"],
  };
  assert.deepEqual(parameterControlState(conditional, "none", 0.2), {
    visible: true,
    enabled: true,
    inactive: false,
    reason: "",
  });
  const inactive = parameterControlState(conditional, "high", 0.2);
  assert.equal(inactive.visible, true);
  assert.equal(inactive.enabled, false);
  assert.equal(inactive.inactive, true);
  assert.match(inactive.reason, /will not be sent/i);
  assert.deepEqual(parameterControlState(conditional, "high", ""), {
    visible: false,
    enabled: false,
    inactive: false,
    reason: "",
  });
}

{
  assert.equal(parameterControlState({support: "always"}, null, null).enabled, true);
  assert.equal(parameterControlState({support: "never"}, "minimal", null).visible, false);
  const staleNever = parameterControlState({support: "never"}, "minimal", 0.7);
  assert.equal(staleNever.visible, true);
  assert.equal(staleNever.enabled, false);
  assert.match(staleNever.reason, /not supported/i);
  const undocumented = parameterControlState({support: "undocumented"}, "low", 0.7);
  assert.equal(undocumented.enabled, false);
  assert.match(undocumented.reason, /documentation/i);
}

{
  const astra = {
    api: {responses: true, chat_completions: true},
    function_calling: {responses: true, chat_completions: false},
  };
  assert.equal(apiPathSelectable(astra, "chat_completions", false), true);
  assert.equal(apiPathSelectable(astra, "chat_completions", true), false);
  assert.equal(apiPathSelectable(astra, "responses", true), true);
  assert.equal(apiPathSelectable(astra, "auto", true), true);
}

{
  const result = {
    catalog_models: [
      {id: "gpt-6-astra", status: "current"},
      {id: "gpt-4-legacy", status: "deprecated"},
      {id: "gpt-5.3", status: "unknown"},
    ],
  };
  assert.deepEqual(pickerModels(result, "").map((item) => item.id), ["gpt-6-astra"]);
  assert.deepEqual(
    pickerModels(result, "gpt-4-legacy").map((item) => item.id),
    ["gpt-6-astra", "gpt-4-legacy"],
  );
  assert.equal(pickerModels(result, "").some((item) => item.id === "gpt-5.3"), false);
}
