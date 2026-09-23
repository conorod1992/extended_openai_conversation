import assert from "node:assert/strict";
import {TestEventTarget} from "./frontend-test-helpers.mjs";

class FakeTextArea extends TestEventTarget {
  constructor(value) {
    super();
    this._value = value;
    this.hidden = false;
  }
  get value() { return this._value; }
  set value(value) { this._value = String(value ?? ""); }
  focus() {}
}

class FakeEditor extends TestEventTarget {
  constructor() {
    super();
    this.hidden = true;
    this.isConnected = true;
    this.values = [];
  }
  setValue(value) { this.values.push(structuredClone(value)); }
}

const originalDocument = globalThis.document;
const originalCustomElements = globalThis.customElements;
const originalTextAreaElement = globalThis.HTMLTextAreaElement;

const flush = async () => {
  await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
};

function makeHarness(rawYaml, originalName, validationResult) {
  const textarea = new FakeTextArea(rawYaml);
  const editor = new FakeEditor();
  let style = null;
  const root = {
    querySelector(selector) {
      if (selector === "#tool-yaml") return textarea;
      if (selector === "#tool-yaml-native") return editor;
      if (selector === "style[data-native-tool-yaml]") return style;
      return null;
    },
    append(node) { style = node; },
  };
  const calls = [];
  return {
    textarea,
    editor,
    calls,
    panel: {
      shadowRoot: root,
      _toolOriginalName: originalName,
      async _call(section, action, payload) {
        calls.push({section, action, payload});
        return validationResult;
      },
    },
  };
}

try {
  globalThis.HTMLTextAreaElement = FakeTextArea;
  globalThis.document = {
    createElement(tag) {
      if (tag !== "style") throw new Error(`Unexpected element ${tag}`);
      return {dataset: {}, textContent: ""};
    },
  };
  globalThis.customElements = {
    get: () => FakeEditor,
    whenDefined: () => Promise.resolve(),
  };

  const {bindNativeToolYaml, nativeStarterConfig} = await import(new URL(
    "../custom_components/extended_openai_conversation_responses/frontend/agent-config-native-yaml.js",
    import.meta.url,
  ));

  const starterYaml = "spec:\n  name: my_tool\n  description: Describe what this tool does.\n  parameters:\n    type: object\n    properties: {}\nfunction:\n  type: native\n  name: ''\n";
  assert.deepEqual(nativeStarterConfig(starterYaml, null), {
    spec: {
      name: "my_tool",
      description: "Describe what this tool does.",
      parameters: {type: "object", properties: {}},
    },
    function: {type: "native", name: ""},
  });
  assert.equal(nativeStarterConfig(starterYaml, "existing_tool"), null, "an existing invalid tool must not be mistaken for the new-tool starter");
  assert.equal(nativeStarterConfig(starterYaml.replace("my_tool", "other_tool"), null), null, "only the controlled backend starter shape may bypass semantic validation for hydration");

  {
    const harness = makeHarness(
      starterYaml,
      null,
      {valid: false, errors: {"functions[0].function.name": "unknown native implementation: "}},
    );
    bindNativeToolYaml(harness.panel);
    await flush();

    assert.deepEqual(harness.calls, [{section: "tools", action: "validate_yaml", payload: {yaml: starterYaml}}]);
    assert.equal(harness.editor.hidden, false, "the intentionally incomplete new-tool starter must remain in the native editor");
    assert.equal(harness.textarea.hidden, true, "the raw fallback must stay hidden for the controlled new-tool starter");
    assert.deepEqual(harness.editor.values.at(-1), {
      spec: {
        name: "my_tool",
        description: "Describe what this tool does.",
        parameters: {type: "object", properties: {}},
      },
      function: {type: "native", name: ""},
    });
  }

  {
    const rawYaml = "spec:\n  description: legacy invalid tool\nfunction:\n  type: native\n";
    const harness = makeHarness(
      rawYaml,
      "legacy_tool",
      {valid: false, errors: [{message: "spec.name is required"}]},
    );
    bindNativeToolYaml(harness.panel);
    await flush();

    assert.deepEqual(harness.calls, [{section: "tools", action: "validate_yaml", payload: {yaml: rawYaml}}]);
    assert.equal(harness.editor.hidden, true, "backend-invalid persisted YAML must not expose an unhydrated native editor");
    assert.equal(harness.textarea.hidden, false, "backend-invalid persisted YAML must remain editable in the raw fallback");
    assert.equal(harness.textarea.value, rawYaml, "falling back must preserve the authoritative raw YAML exactly");
    assert.deepEqual(harness.editor.values, [], "invalid persisted YAML must never hydrate a partial/stale native value");
  }

  console.log("Native YAML starter/invalid-hydration tests passed");
} finally {
  if (originalDocument === undefined) delete globalThis.document;
  else globalThis.document = originalDocument;
  if (originalCustomElements === undefined) delete globalThis.customElements;
  else globalThis.customElements = originalCustomElements;
  if (originalTextAreaElement === undefined) delete globalThis.HTMLTextAreaElement;
  else globalThis.HTMLTextAreaElement = originalTextAreaElement;
}
