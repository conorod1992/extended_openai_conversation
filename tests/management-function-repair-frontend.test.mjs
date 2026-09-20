import assert from "node:assert/strict";

import {
  bindIsolatedRepair,
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


class RepairControl {
  constructor() {
    this.dataset = {};
    this.listeners = new Map();
    this.value = "";
    this.disabled = false;
    this.open = false;
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  emit(type) {
    const event = {
      preventDefault() {},
      stopImmediatePropagation() {},
    };
    for (const listener of this.listeners.get(type) || []) listener(event);
  }

  showModal() {
    this.open = true;
  }

  focus() {
    this.focused = true;
  }
}

{
  const invalidA = {
    index: 0,
    name: "invalid_a",
    yaml: "spec:\n  name: invalid_a\nfunction:\n  type: native\n",
    tool: {spec: {name: "invalid_a"}, function: {type: "native"}},
    validation_error: "invalid A",
  };
  const invalidB = {
    index: 1,
    name: "invalid_b",
    yaml: "spec:\n  name: invalid_b\nfunction:\n  type: native\n",
    tool: {spec: {name: "invalid_b"}, function: {type: "native"}},
    validation_error: "invalid B",
  };
  const textarea = new RepairControl();
  const dialog = new RepairControl();
  const status = new RepairControl();
  const save = new RepairControl();
  const editA = new RepairControl();
  const editB = new RepairControl();
  editA.dataset.repairIndex = "0";
  editB.dataset.repairIndex = "1";

  const calls = [];
  const adapter = {
    textarea,
    yaml: "spec:\n  name: previously_valid\n",
    setValues: [],
    setYaml(value) {
      this.yaml = String(value ?? "");
      this.setValues.push(this.yaml);
      textarea.value = this.yaml;
    },
    getYaml() {
      return this.yaml;
    },
    focus() {
      this.focused = true;
    },
  };

  const panel = {
    _viewKey: functionsView(),
    _result: {
      revision: "repair-revision",
      function_repair: {
        isolatable: true,
        invalid_tools: [invalidA, invalidB],
      },
    },
    _toolYamlEditorAdapter: adapter,
    _setSaving(button, saving) {
      button.disabled = saving;
    },
    async _call(section, action, payload) {
      calls.push({section, action, payload});
      if (section === "tools" && action === "validate_yaml") {
        return {valid: false, errors: {tool: "still invalid"}};
      }
      throw new Error(`Unexpected call ${section}/${action}`);
    },
    shadowRoot: {
      querySelector(selector) {
        return {
          "#tool-dialog": dialog,
          "#tool-yaml": textarea,
          "#tool-error": status,
          "#tool-save": save,
        }[selector] || null;
      },
      querySelectorAll(selector) {
        if (selector === ".edit-invalid-tool") return [editA, editB];
        if (selector === ".delete-invalid-tool") return [];
        return [];
      },
    },
  };

  bindIsolatedRepair(panel);

  editA.emit("click");
  assert.equal(adapter.yaml, invalidA.yaml, "opening an invalid tool must replace any previous editor value");
  assert.equal(panel._repairToolIndex, 0);
  assert.equal(dialog.open, true);
  assert.equal(adapter.focused, true);

  adapter.setYaml("spec:\n  name: valid_tool\n");
  editB.emit("click");
  assert.equal(adapter.yaml, invalidB.yaml, "a later invalid tool must replace YAML left by a valid-tool edit");
  assert.deepEqual(adapter.setValues.slice(-2), ["spec:\n  name: valid_tool\n", invalidB.yaml]);

  adapter.yaml = "spec:\n  name: native_editor_current\n";
  textarea.value = "STALE HIDDEN TEXTAREA";
  save.emit("click");
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls.at(-1).section, "tools");
  assert.equal(calls.at(-1).action, "validate_yaml");
  assert.equal(
    calls.at(-1).payload.yaml,
    "spec:\n  name: native_editor_current\n",
    "repair save must read the shared editor adapter rather than a stale textarea value",
  );
}
