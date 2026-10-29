import {completeNativeDocument} from "./frontend-test-helpers.mjs";
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

  removeEventListener(type, listener) {
    this.listeners.set(type, (this.listeners.get(type) || []).filter((item) => item !== listener));
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
    completeNativeDocument(this, value);
  }

  addEventListener(type, listener) {
    this.listeners.set(type, listener);
  }

  removeEventListener(type, listener) {
    if (this.listeners.get(type) === listener) this.listeners.delete(type);
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
  const dialog = {open:true};
  return {
    root: {
      querySelector(selector) {
        if (selector === "#tool-yaml") return textarea;
        if (selector === "#tool-yaml-native") return editor;
        if (selector === "#tool-error") return status;
        if (selector === "#tool-dialog") return dialog;
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
    dialog,
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
  const {bindNativeToolYaml, ensureNativeYamlEditor} = await import(moduleUrl);
  const {configurationDialogs} = await import("../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor.js");

  {
    delete globalThis.document;
    const html = configurationDialogs({_e:String, _viewKey:() => "capabilities/functions"});
    assert.match(html, /<textarea data-native-yaml-fallback id="tool-yaml"/);
    assert.match(html, /<ha-yaml-editor id="tool-yaml-native"[^>]*hidden in-dialog/,
      "the owner must emit the fallback and initially hidden native editor without a DOM");
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
    let defined = false;
    let panelLoaded = 0;
    let serviceLoaded = 0;
    const registry = {
      get: () => defined ? GuardEditor : undefined,
      whenDefined: async () => {
        if (!defined) throw new Error("editor was not registered by loader");
      },
    };
    const documentRef = {
      createElement(tag) {
        if (tag === "partial-panel-resolver") {
          return {
            getRoutes: () => ({
              routes: {
                a: {
                  async load() { panelLoaded += 1; },
                },
              },
            }),
          };
        }
        if (tag === "developer-tools-router") {
          return {
            routerOptions: {
              routes: {
                service: {
                  async load() {
                    serviceLoaded += 1;
                    defined = true;
                  },
                },
              },
            },
          };
        }
        throw new Error(`Unexpected createElement(${tag})`);
      },
    };

    assert.equal(await ensureNativeYamlEditor(registry, documentRef), true);
    assert.equal(panelLoaded, 1, "developer-tools panel should be loaded once");
    assert.equal(serviceLoaded, 1, "service editor bundle should be loaded once to register ha-yaml-editor");
  }

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
    assert.equal(harness.textarea.hidden, false, "failed native-editor loading must retain the textarea fallback");
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

    GuardTextArea.prototype.__lookupSetter__("value").call(harness.textarea, "typed fallback YAML");
    harness.textarea.dispatchEvent(new Event("input"));
    assert.equal(harness.textarea.value, "typed fallback YAML", "fallback input must refresh the raw-YAML bridge");

    bindNativeToolYaml({shadowRoot: harness.root, _call: async () => ({valid: true, config: {}})});
    assert.equal(harness.getAppendCount(), 1, "native editor styles should be installed only once per root");
    neverDefined.resolve();
    await flush();
  }

  {
    class NoValueDescriptorTextArea {}
    globalThis.HTMLTextAreaElement = NoValueDescriptorTextArea;
    const harness = makeRoot();
    globalThis.customElements = {
      get: () => GuardEditor,
      whenDefined: () => Promise.resolve(),
    };
    const panel = {shadowRoot: harness.root, _call: async () => ({valid: true, config: {}})};
    bindNativeToolYaml(panel);
    await flush();
    assert.equal(harness.editor.hidden, false, "adapter mounting must not depend on the textarea prototype value descriptor");
    assert.equal(Object.hasOwn(harness.textarea, "value"), false, "adapter must not redefine textarea.value");
    assert.equal(harness.textarea.focus, GuardTextArea.prototype.focus, "adapter must not shadow textarea.focus");
    globalThis.HTMLTextAreaElement = GuardTextArea;
  }

  // Delayed native hydration must not replace newer typing in the active editor.
  {
    const harness = makeRoot({textarea: new GuardTextArea("stored YAML")});
    const hydration = deferred();
    globalThis.customElements = {get: () => GuardEditor, whenDefined: () => Promise.resolve()};
    bindNativeToolYaml({shadowRoot: harness.root, _call: () => hydration.promise});
    harness.textarea.value = "newer user YAML";
    harness.textarea.dispatchEvent(new Event("input"));
    hydration.resolve({valid: true, config: {spec: {name: "stale"}}});
    await flush();
    assert.equal(harness.textarea.value, "newer user YAML");
    assert.deepEqual(harness.editor.values, [], "a stale hydration response must not call setValue");
  }

  // Hidden hydration never owns a draft, including after navigation, a new
  // editor visit or authentication changes while the native document is pending.
  for (const transition of ["typing", "selection", "agent", "auth", "visit", "close", "destroy", "root"]) {
    const harness = makeRoot({textarea:new GuardTextArea("stored YAML")});
    const panel = {shadowRoot:harness.root, _agentId:"a", hass:{user:{id:"user-a"}},
      _call:async () => ({valid:true, config:{name:"stored"}})};
    const outer = deferred(), inner = deferred();
    harness.editor.setValue = function(value) {
      completeNativeDocument(this, value);
      this.updateComplete = outer.promise;
      this.shadowRoot.querySelector("ha-code-editor").updateComplete = inner.promise;
    };
    globalThis.customElements = {get:() => GuardEditor};
    bindNativeToolYaml(panel);
    await flush();
    assert.equal(harness.editor.hidden, true);
    assert.equal(harness.textarea.hidden, false);
    if (transition === "typing") {
      harness.textarea.value = "new user draft";
      harness.textarea.dispatchEvent(new Event("input"));
    } else if (transition === "selection") harness.textarea.dispatchEvent(new Event("keydown"));
    else if (transition === "agent") panel._agentId = "b";
    else if (transition === "auth") panel._eocAuthenticationGeneration = 1;
    else if (transition === "visit") panel._toolEditorLoad = {};
    else if (transition === "close") harness.dialog.open = false;
    else if (transition === "destroy") panel._toolYamlEditorAdapter.destroy();
    else panel.shadowRoot = {querySelector:() => null};
    harness.editor.yaml = "late native document";
    harness.editor.emit("value-changed", {isValid:true});
    outer.resolve(); inner.resolve();
    await flush();
    assert.equal(harness.editor.hidden, true, transition);
    assert.equal(harness.textarea.value, transition === "typing" ? "new user draft" : "stored YAML", transition);
  }

  // An unavailable/stalled native document has a bounded fallback, rather than
  // leaving the dialog disabled forever or calling an empty document ready.
  {
    const harness = makeRoot({textarea:new GuardTextArea("fallback YAML")});
    const originalTimeout = globalThis.setTimeout;
    let expire;
    globalThis.setTimeout = (callback, delay) => {
      assert.equal(delay, 5000);
      expire = callback;
      return undefined;
    };
    try {
      harness.editor.setValue = function(value) {
        completeNativeDocument(this, value);
        this.updateComplete = new Promise(() => {});
      };
      globalThis.customElements = {get:() => GuardEditor};
      const panel = {shadowRoot:harness.root, _call:async () => ({valid:true, config:{name:"fallback"}})};
      bindNativeToolYaml(panel);
      await flush();
      expire();
      await flush();
      assert.equal(harness.editor.hidden, true);
      assert.equal(harness.textarea.hidden, false);
      assert.equal(panel._toolYamlEditorAdapter.getYaml(), "fallback YAML");
    } finally { globalThis.setTimeout = originalTimeout; }
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
