import assert from "node:assert/strict";
import {requestRuleSummary as coreSummary} from "../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui-core.js";
import {requestRuleSummary as guidanceSummary} from "../custom_components/extended_openai_conversation_responses/frontend/management-decision-guidance.js";

for (const summary of [coreSummary, guidanceSummary]) {
  for (const scope of ["request", "conversation", undefined]) {
    const action = {reset: true, ...(scope ? {scope} : {})};
    const result = summary({action_type: "model_routing", action});
    assert.equal(result.action, `Returns model and reasoning to the assistant's configured defaults for ${scope === "conversation" ? "the rest of this conversation" : "this request only"}.`);
    assert.deepEqual(action, {reset: true, ...(scope ? {scope} : {})});
  }
}
