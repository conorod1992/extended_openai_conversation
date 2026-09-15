import assert from "node:assert/strict";

const originalDocument = globalThis.document;
const originalCustomElements = globalThis.customElements;
const originalTextAreaElement = globalThis.HTMLTextAreaElement;

class GuardTextArea {
  constructor(value = "") {
    this._value = String(value);
    this.hidden = false;
    this.listeners = new Map();
    this.focusCalls = 0;
  }

  get value() {
    return this._value;
  }

  set value(value) {
    this._value = String(value ?? "");
  }

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) || [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  dispatchEvent(event) {
    for (const listener of this.listeners.get(event?.type) || []) listener(event);
    return true;
  }

  focus() {
    this.focusCalls += 1;
  }
}

class GuardEditor {
  constructor() {
    this.hidden = true;
    this.isConnected = true;
    this.yaml = "";
    this.listeners = new Map();
    this.values = [];
  }

  setValue(value) {
    this.values.push(value);
  }

  addEventListener(type, listener) {
    this.listeners.set(type, listener);
  }

  emit(type, detail = {}) {
    this.listeners.get(type)?.({type, detail});
  }
}

const flush = async () => {
  await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
};

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return {promise, resolve, reject};
}

function makeRoot({textarea = new GuardTextArea(), editor = new GuardEditor()} = {}) {
  const status = {className: "validation", textContent: ""};
  let style = null;
  let appendCount = 0;
  return {
    root: {
      querySelector(selector) {
        if (selector === "#tool-yaml") return textarea;
        if (selector === "#tool-yaml-native") return editor;
        if (selector === "#tool-error") return status;
        if (selector === "#tool-dialog") return {open: true};
        if (selector === "#tool-save") return {disabled: false, click() {}};
        if (selector === "style[data-native-tool-yaml]") return style;
        return null;
      },
      append(node) {
        style = node;
        appendCount += 1;
      },
    },
    textarea,
    editor,
    status,
    getAppendCount: () => appendCount,
  };
}

try {
  globalThis.HTMLTextAreaElement = GuardTextArea;
  globalThis.document = {
    createElement(tag) {
      if (tag === "style") return {dataset: {}, textContent: ""};
      throw new Error(`Unexpected createElement(${tag})`);
    },
  };

  const moduleUrl = new URL(
    "../custom_components/extended_openai_conversation_responses/frontend/agent-config-native-yaml.js",
    import.meta.url,
  );
  const {bindNativeToolYaml, decorateToolYamlEditor} = await import(moduleUrl);

  {
    const html = '<textarea id="tool-yaml"></textarea>';
    delete globalThis.document;
    assert.equal(decorateToolYamlEditor(html), html, "SSR/no-DOM rendering must leave the base dialog unchanged");
    globalThis.document = {
      createElement(tag) {
        if (tag === "style") return {dataset: {}, textContent: ""};
        throw new Error(`Unexpected createElement(${tag})`);
      },
    };
  }

  // Missing panel/root/editor/textarea are intentional no-ops.
  bindNativeToolYaml(null);
  bindNativeToolYaml({shadowRoot: {querySelector: () => null}});

  {
    const harness = makeRoot();
    const definition = deferred();
    globalThis.customElements = {
      get: () => undefined,
      whenDefined: () => definition.promise,
    };
    bindNativeToolYaml({shadowRoot: harness.root, _call: async () => ({valid: true, config: {}})});
    definition.reject(new Error("custom element registration failed"));
    await flush();
    assert.equal(harness.editor.hidden, true);
    assert.equal(harness.textarea.hidden, false, "rejected custom-element registration must retain the textarea fallback");
  }

  {
    const harness = makeRoot();
    harness.editor.isConnected = false;
    globalThis.customElements = {
      get: () => GuardEditor,
      whenDefined: () => Promise.resolve(),
    };
    bindNativeToolYaml({shadowRoot: harness.root, _call: async () => ({valid: true, config: {}})});
    await flush();
    assert.equal(harness.editor.hidden, true);
    assert.equal(harness.textarea.hidden, false, "a detached native editor must not replace the usable fallback");
  }

  {
    const harness = makeRoot();
    Object.defineProperty(harness.editor, "inDialog", {
      configurable: true,
      set() {
        throw new Error("inDialog initialization failed");
      },
    });
    globalThis.customElements = {
      get: () => GuardEditor,
      whenDefined: () => Promise.resolve(),
    };
    bindNativeToolYaml({shadowRoot: harness.root, _call: async () => ({valid: true, config: {}})});
    await flush();
    assert.equal(harness.editor.hidden, true);
    assert.equal(harness.textarea.hidden, false, "native initialization property failures must fall back safely");
  }

  {
    const harness = makeRoot();
    globalThis.customElements = {
      get: () => GuardEditor,
      whenDefined: () => Promise.resolve(),
    };
    bindNativeToolYaml({shadowRoot: harness.root, _call: async () => ({valid: true, config: {}})});
    await flush();
    harness.editor.yaml = null;
    harness.editor.emit("value-changed", {isValid: false});
    assert.equal(harness.textarea.value, "");
    assert.equal(harness.status.className, "validation invalid");
    assert.equal(harness.status.textContent, "Function Tool YAML is invalid.");
  }

  {
    const harness = makeRoot({textarea: new GuardTextArea("starter")});
    const neverDefined = deferred();
    globalThis.customElements = {
      get: () => undefined,
      whenDefined: () => neverDefined.promise,
    };
    bindNativeToolYaml({shadowRoot: harness.root, _call: async () => ({valid: true, config: {}})});

    // Simulate a browser/native DOM setter that bypasses the instance-level
    // overridden value property, then fires the real input event.
    GuardTextArea.prototype.__lookupSetter__("value").call(harness.textarea, "typed fallback YAML");
    harness.textarea.dispatchEvent(new Event("input"));
    assert.equal(harness.textarea.value, "typed fallback YAML", "fallback input must refresh the raw-YAML bridge");

    // Rebinding after a render should reuse the already-installed style node.
    bindNativeToolYaml({shadowRoot: harness.root, _call: async () => ({valid: true, config: {}})});
    assert.equal(harness.getAppendCount(), 1, "native editor styles should be installed only once per root");
    neverDefined.resolve();
    await flush();
  }

  {
    class NoValueTextArea {}
    globalThis.HTMLTextAreaElement = NoValueTextArea;
    const harness = makeRoot();
    globalThis.customElements = {
      get: () => GuardEditor,
      whenDefined: () => Promise.resolve(),
    };
    bindNativeToolYaml({shadowRoot: harness.root, _call: async () => ({valid: true, config: {}})});
    assert.equal(harness.editor.hidden, true, "missing native textarea value descriptor must leave the base UI untouched");
    globalThis.HTMLTextAreaElement = GuardTextArea;
  }

  console.log("Native Function Tool YAML guard/error tests passed");
} finally {
  if (originalDocument === undefined) delete globalThis.document;
  else globalThis.document = originalDocument;
  if (originalCustomElements === undefined) delete globalThis.customElements;
  else globalThis.customElements = originalCustomElements;
  if (originalTextAreaElement === undefined) delete globalThis.HTMLTextAreaElement;
  else globalThis.HTMLTextAreaElement = originalTextAreaElement;
}
