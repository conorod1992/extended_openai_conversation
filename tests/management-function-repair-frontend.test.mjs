import assert from "node:assert/strict";

import {
  editableToolsText,
  functionRepairView,
  repairIssue,
  repairMetadata,
  renderFallbackRepair,
} from "../custom_components/extended_openai_conversation_responses/frontend/management-function-repair.js";

function repairableAgent() {
  return {
    configuration_issue: {
      field: "functions",
      message: "functions[0].spec.parameters: invalid description",
      repairable: true,
    },
  };
}

function functionsView() {
  return () => "capabilities/functions";
}

{
  const agent = repairableAgent();
  assert.equal(repairIssue({_selectedAgent: () => agent}), agent.configuration_issue);
  assert.equal(
    repairIssue({_selectedAgent: () => ({configuration_issue: {...agent.configuration_issue, field: "prompt"}})}),
    null,
  );
  assert.equal(
    repairIssue({_selectedAgent: () => ({configuration_issue: {...agent.configuration_issue, repairable: false}})}),
    null,
  );
  assert.equal(functionRepairView({_viewKey: functionsView()}), true);
  assert.equal(functionRepairView({_viewKey: () => "assistant/model"}), false);
}

{
  assert.equal(editableToolsText("raw persisted text"), "raw persisted text");
  assert.equal(
    editableToolsText([{spec: {name: "one"}}]),
    '[\n  {\n    "spec": {\n      "name": "one"\n    }\n  }\n]',
  );
}

{
  const repair = {
    isolatable: true,
    invalid_tools: [{index: 1, name: "bad", validation_error: "bad schema"}],
  };
  assert.equal(repairMetadata({_result: {function_repair: repair}}), repair);
  assert.equal(repairMetadata({_result: {}}), null);
}

{
  const html = renderFallbackRepair(
    {
      _result: {
        tools: [{spec: {name: "good"}}, {spec: {name: "bad"}}],
        function_repair: {
          isolatable: false,
          validation_error: '<script>alert("x")</script>',
        },
      },
      _e: (value) => String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;"),
    },
    repairableAgent().configuration_issue,
  );
  assert.ok(html.includes("Function Tools need repair"));
  assert.ok(html.includes("Validate and save repair"));
  assert.ok(html.includes("collection-level corruption"));
  assert.ok(!html.includes('<script>alert("x")</script>'));
  assert.ok(html.includes("&lt;script&gt;"));
}
