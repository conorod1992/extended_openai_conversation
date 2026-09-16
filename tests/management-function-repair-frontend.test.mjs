import assert from "node:assert/strict";

import {
  editableToolsText,
  functionRepairView,
  loadFunctionRepair,
  renderFunctionRepair,
  repairIssue,
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
  const calls = [];
  let renders = 0;
  const panel = {
    _selectedAgent: () => repairableAgent(),
    _viewKey: functionsView(),
    _data: {is_admin: true},
    _loadToken: 0,
    _busy: false,
    _contentData: {old: true},
    _result: null,
    _error: "old error",
    _render: () => { renders += 1; },
    _call: async (section, action) => {
      calls.push([section, action]);
      return {
        tools: [{spec: {name: "legacy"}}],
        invalid_tools: [{index: 0, name: "legacy", tool: {spec: {name: "legacy"}}, validation_error: "bad schema"}],
        validation_error: "bad schema",
        revision: "rev-1",
      };
    },
  };

  assert.equal(await loadFunctionRepair(panel), true);
  assert.deepEqual(calls, [["function_repair", "get"]]);
  assert.equal(panel._busy, false);
  assert.equal(panel._contentData, null);
  assert.equal(panel._result.function_repair, true);
  assert.equal(panel._result.revision, "rev-1");
  assert.equal(panel._error, null);
  assert.equal(renders, 2);
}

{
  let called = false;
  const panel = {
    _selectedAgent: () => repairableAgent(),
    _viewKey: functionsView(),
    _data: {is_admin: false},
    _loadToken: 0,
    _busy: false,
    _contentData: null,
    _result: null,
    _error: null,
    _render: () => {},
    _call: async () => {
      called = true;
      throw new Error("non-admin repair must not call the backend");
    },
  };

  assert.equal(await loadFunctionRepair(panel), true);
  assert.equal(called, false);
  assert.equal(panel._result.administrator_required, true);
}

{
  const panel = {
    _selectedAgent: () => repairableAgent(),
    _viewKey: () => "assistant/model",
  };
  assert.equal(await loadFunctionRepair(panel), false);
}

{
  const html = renderFunctionRepair(
    {
      _data: {is_admin: true},
      _result: {
        tools: [{spec: {name: "good"}}, {spec: {name: "bad"}}],
        invalid_tools: [{
          index: 1,
          name: "bad",
          tool: {spec: {name: "bad"}},
          validation_error: '<script>alert("x")</script>',
        }],
        validation_error: '<script>alert("x")</script>',
      },
    },
    repairableAgent().configuration_issue,
  );
  assert.ok(html.includes("Function Tools need repair"));
  assert.ok(html.includes("Validate and save repair"));
  assert.ok(html.includes("bad"));
  assert.ok(!html.includes('name: "good"'));
  assert.ok(!html.includes('<script>alert("x")</script>'));
  assert.ok(html.includes("&lt;script&gt;"));
  assert.ok(html.includes("other assistant settings remain editable"));
}
