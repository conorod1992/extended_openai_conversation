import assert from "node:assert/strict";

class FakeTextAreaElement {
  constructor(value = "") {
    this._value = String(value);
    this.dataset = {};
    this.hidden = false;
    this.focusCalls = 0;
    this.inputEvents = 0;
  }

  get value() {
    return this._value;
  }

  set value(value) {
    this._value = String(value ?? "");
  }

  focus() {
    this.focusCalls += 1;
  }

  dispatchEvent(event) {
    if (event?.type === "input") this.inputEvents += 1;
    return true;
  }
}

class FakeNativeEditor {
  constructor() {
    this.hidden = true;
    this.isConnected = true;
    this.isValid = false;
    this.inDialog = false;
    this.yaml = "";
    this.values = [];
    this.focusCalls = 0;
    this.listeners = new Map();
  }

  setValue(value) {
    this.values.push(value);
  }

  focus() {
    this.focusCalls += 1;
  }

  addEventListener(type, listener) {
    this.listeners.set(type, listener);
  }

  emit(type, detail = {}) {
    this.listeners.get(type)?.({type, detail});
  }
}

const originalDocument = globalThis.document;
const originalCustomElements = globalThis.customElements;
const originalTextAreaElement = globalThis.HTMLTextAreaElement;

globalThis.HTMLTextAreaElement = FakeTextAreaElement;
globalThis.document = {
  createElement(tag) {
    if (tag !== "style") throw new Error(`Unexpected createElement(${tag}) in adapter unit test`);
    return {dataset: {}, textContent: ""};
  },
};

const frontend = (name) => new URL(
  `../custom_components/extended_openai_conversation_responses/frontend/${name}`,
  import.meta.url,
);
const {bindNativeToolYaml} = await import(frontend("agent-config-native-yaml.js"));

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

function makeHarness({initialYaml = "", call, defined = true, definitionPromise = null} = {}) {
  const textarea = new FakeTextAreaElement(initialYaml);
  const editor = new FakeNativeEditor();
  const status = {className: "validation", textContent: ""};
  const dialog = {open: true};
  const save = {disabled: false, clicks: 0, click() { this.clicks += 1; }};
  let style = null;

  const root = {
    querySelector(selector) {
      if (selector === "#tool-yaml") return textarea;
      if (selector === "#tool-yaml-native") return editor;
      if (selector === "#tool-error") return status;
      if (selector === "#tool-dialog") return dialog;
      if (selector === "#tool-save") return save;
      if (selector === "style[data-native-tool-yaml]") return style;
      return null;
    },
    append(node) {
      style = node;
    },
  };

  globalThis.customElements = {
    get(tag) {
      assert.equal(tag, "ha-yaml-editor");
      return defined ? FakeNativeEditor : undefined;
    },
    whenDefined(tag) {
      assert.equal(tag, "ha-yaml-editor");
      return definitionPromise || Promise.resolve();
    },
  };

  const calls = [];
  const panel = {
    shadowRoot: root,
    async _call(section, action, payload) {
      calls.push({section, action, payload});
      if (call) return call(section, action, payload, calls.length - 1);
      return {valid: true, config: {name: "default", type: "script"}};
    },
  };

  return {panel, textarea, editor, status, dialog, save, calls, getStyle: () => style};
}

try {
  {
    const yaml = "name: Existing tool\ntype: script\nsequence: []\n";
    const harness = makeHarness({
      initialYaml: yaml,
      call: async (_section, _action, payload) => ({
        valid: true,
        config: {name: payload.yaml.includes("Existing") ? "Existing tool" : "Unexpected", type: "script"},
      }),
    });

    bindNativeToolYaml(harness.panel);
    await flush();

    assert.equal(harness.textarea.hidden, true, "native editor should replace the fallback after activation");
    assert.equal(harness.editor.hidden, false);
    assert.equal(harness.editor.inDialog, true);
    assert.deepEqual(harness.editor.values.at(-1), {name: "Existing tool", type: "script"});
    assert.deepEqual(harness.calls, [{section: "tools", action: "validate_yaml", payload: {yaml}}]);
    assert.match(harness.getStyle().textContent, /#tool-yaml-native/);

    harness.textarea.focus();
    assert.equal(harness.editor.focusCalls, 1, "legacy focus calls should be forwarded to the native editor");
    assert.equal(harness.textarea.focusCalls, 0);
  }

  {
    const harness = makeHarness({
      initialYaml: "",
      call: async (_section, _action, payload) => ({
        valid: true,
        config: {name: payload.yaml.includes("Starter") ? "Starter" : "Replacement", type: "script"},
      }),
    });
    bindNativeToolYaml(harness.panel);
    await flush();

    assert.deepEqual(harness.editor.values.at(-1), {}, "new/empty Function Tools should initialise as an empty native document");
    assert.equal(harness.editor.isValid, true);
    assert.equal(harness.calls.length, 0, "empty YAML should not require a backend validation round trip");

    const starterYaml = "name: Starter\ntype: script\nsequence: []\n";
    harness.textarea.value = starterYaml;
    await flush();
    assert.equal(harness.textarea.value, starterYaml, "programmatic starter/preset replacement must preserve exact raw YAML");
    assert.deepEqual(harness.editor.values.at(-1), {name: "Starter", type: "script"});
    assert.deepEqual(harness.calls.at(-1), {section: "tools", action: "validate_yaml", payload: {yaml: starterYaml}});

    const replacementYaml = "name: Replacement\ntype: script\nsequence:\n  - stop: done\n";
    harness.textarea.value = replacementYaml;
    await flush();
    assert.equal(harness.textarea.value, replacementYaml);
    assert.deepEqual(harness.editor.values.at(-1), {name: "Replacement", type: "script"});
  }

  {
    const first = deferred();
    const second = deferred();
    const harness = makeHarness({
      initialYaml: "",
      call: async (_section, _action, payload) => {
        if (payload.yaml.includes("First")) return first.promise;
        if (payload.yaml.includes("Second")) return second.promise;
        throw new Error("Unexpected YAML");
      },
    });
    bindNativeToolYaml(harness.panel);
    await flush();

    harness.textarea.value = "name: First\ntype: script\n";
    harness.textarea.value = "name: Second\ntype: script\n";
    second.resolve({valid: true, config: {name: "Second", type: "script"}});
    await flush();
    assert.deepEqual(harness.editor.values.at(-1), {name: "Second", type: "script"});

    first.resolve({valid: true, config: {name: "First", type: "script"}});
    await flush();
    assert.deepEqual(
      harness.editor.values.at(-1),
      {name: "Second", type: "script"},
      "a late validation from a previously opened/replaced tool must not overwrite newer YAML",
    );
  }

  {
    const harness = makeHarness({
      initialYaml: "",
      call: async () => ({valid: false, error: "bad yaml"}),
    });
    bindNativeToolYaml(harness.panel);
    await flush();

    const invalidYaml = "name: [broken\n";
    harness.textarea.value = invalidYaml;
    await flush();
    assert.equal(harness.textarea.value, invalidYaml, "invalid raw YAML must remain available to the authoritative backend validator");
    assert.deepEqual(harness.calls.at(-1), {section: "tools", action: "validate_yaml", payload: {yaml: invalidYaml}});
    assert.deepEqual(harness.editor.values.at(-1), {}, "invalid backend YAML must not replace the native editor value with a partial config");

    const nativeInvalidYaml = "name: still [broken\n";
    harness.editor.yaml = nativeInvalidYaml;
    harness.editor.emit("value-changed", {isValid: false, errorMsg: "Expected closing bracket"});
    assert.equal(harness.textarea.value, nativeInvalidYaml, "native edits must synchronise back to the raw textarea contract");
    assert.equal(harness.textarea.inputEvents, 1, "native edits must trigger the existing input/change handling");
    assert.equal(harness.status.className, "validation invalid");
    assert.equal(harness.status.textContent, "Expected closing bracket");

    harness.editor.yaml = "name: valid again\ntype: script\n";
    harness.editor.emit("value-changed", {isValid: true});
    assert.equal(harness.textarea.value, "name: valid again\ntype: script\n");
    assert.equal(harness.textarea.inputEvents, 2);

    harness.editor.emit("editor-save");
    assert.equal(harness.save.clicks, 1, "native Ctrl/Cmd+S should use the existing Save function button");
    harness.save.disabled = true;
    harness.editor.emit("editor-save");
    harness.dialog.open = false;
    harness.save.disabled = false;
    harness.editor.emit("editor-save");
    assert.equal(harness.save.clicks, 1, "editor-save must not submit a disabled or closed dialog");
  }

  {
    const harness = makeHarness({
      initialYaml: "name: Broken persisted tool\ntype: script\n",
      call: async () => { throw new Error("validation service unavailable"); },
    });
    bindNativeToolYaml(harness.panel);
    await flush();
    assert.equal(harness.editor.hidden, true, "failed native hydration should fall back safely");
    assert.equal(harness.textarea.hidden, false);
    harness.textarea.focus();
    assert.equal(harness.textarea.focusCalls, 1, "fallback should restore normal textarea focus");
  }

  {
    const definition = deferred();
    const yaml = "name: Delayed definition\ntype: script\n";
    const harness = makeHarness({
      initialYaml: yaml,
      defined: false,
      definitionPromise: definition.promise,
      call: async () => ({valid: true, config: {name: "Delayed definition", type: "script"}}),
    });
    bindNativeToolYaml(harness.panel);
    assert.equal(harness.textarea.hidden, false);
    assert.equal(harness.editor.hidden, true);
    assert.equal(harness.calls.length, 0);

    definition.resolve();
    await flush();
    assert.equal(harness.textarea.hidden, true, "late custom-element registration should activate the native editor");
    assert.equal(harness.editor.hidden, false);
    assert.deepEqual(harness.editor.values.at(-1), {name: "Delayed definition", type: "script"});
  }

  console.log("Native Function Tool YAML adapter unit tests passed");
} finally {
  if (originalDocument === undefined) delete globalThis.document;
  else globalThis.document = originalDocument;
  if (originalCustomElements === undefined) delete globalThis.customElements;
  else globalThis.customElements = originalCustomElements;
  if (originalTextAreaElement === undefined) delete globalThis.HTMLTextAreaElement;
  else globalThis.HTMLTextAreaElement = originalTextAreaElement;
}
