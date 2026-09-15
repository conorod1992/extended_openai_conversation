import assert from "node:assert/strict";

class FakeTextArea {
  constructor(value) {
    this._value = value;
    this.hidden = false;
  }
  get value() { return this._value; }
  set value(value) { this._value = String(value ?? ""); }
  addEventListener() {}
  dispatchEvent() { return true; }
  focus() {}
}

class FakeEditor {
  constructor() {
    this.hidden = true;
    this.isConnected = true;
    this.listeners = new Map();
    this.values = [];
  }
  setValue(value) { this.values.push(value); }
  addEventListener(type, listener) { this.listeners.set(type, listener); }
}

const originalDocument = globalThis.document;
const originalCustomElements = globalThis.customElements;
const originalTextAreaElement = globalThis.HTMLTextAreaElement;

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

  const {bindNativeToolYaml} = await import(new URL(
    "../custom_components/extended_openai_conversation_responses/frontend/agent-config-native-yaml.js",
    import.meta.url,
  ));

  const rawYaml = "spec:\n  description: legacy invalid tool\nfunction:\n  type: native\n";
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
  const panel = {
    shadowRoot: root,
    async _call(section, action, payload) {
      calls.push({section, action, payload});
      return {valid: false, errors: [{message: "spec.name is required"}]};
    },
  };

  bindNativeToolYaml(panel);
  await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(calls, [{section: "tools", action: "validate_yaml", payload: {yaml: rawYaml}}]);
  assert.equal(editor.hidden, true, "backend-invalid persisted YAML must not expose an unhydrated native editor");
  assert.equal(textarea.hidden, false, "backend-invalid persisted YAML must remain editable in the raw fallback");
  assert.equal(textarea.value, rawYaml, "falling back must preserve the authoritative raw YAML exactly");
  assert.deepEqual(editor.values, [], "invalid backend YAML must never hydrate a partial/stale native value");

  console.log("Native YAML invalid-hydration fallback test passed");
} finally {
  if (originalDocument === undefined) delete globalThis.document;
  else globalThis.document = originalDocument;
  if (originalCustomElements === undefined) delete globalThis.customElements;
  else globalThis.customElements = originalCustomElements;
  if (originalTextAreaElement === undefined) delete globalThis.HTMLTextAreaElement;
  else globalThis.HTMLTextAreaElement = originalTextAreaElement;
}
