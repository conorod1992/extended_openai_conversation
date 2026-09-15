import * as base from "./agent-config-editor-model-v2.js";

export * from "./agent-config-editor-model-v2.js";

const NATIVE_EDITOR_TAG = "ha-yaml-editor";
const NATIVE_EDITOR_ID = "tool-yaml-native";
const NATIVE_STYLE = `
  #${NATIVE_EDITOR_ID} {
    display: block;
    width: 100%;
    min-height: 360px;
    height: min(58vh, 620px);
  }
  #${NATIVE_EDITOR_ID}[hidden] { display: none; }
  #tool-yaml[hidden] { display: none !important; }
`;

export function decorateToolYamlEditor(html) {
  if (typeof document === "undefined" || typeof document.createElement !== "function") return html;
  const template = document.createElement("template");
  template.innerHTML = html;
  const textarea = template.content.querySelector("#tool-yaml");
  if (!textarea || template.content.querySelector(`#${NATIVE_EDITOR_ID}`)) return template.innerHTML;

  textarea.dataset.nativeYamlFallback = "";
  const editor = document.createElement(NATIVE_EDITOR_TAG);
  editor.id = NATIVE_EDITOR_ID;
  editor.className = "tool-yaml-native-editor";
  editor.hidden = true;
  editor.setAttribute("in-dialog", "");
  editor.setAttribute("aria-label", "Function Tool YAML");
  editor.setAttribute("aria-describedby", "tool-error");
  textarea.insertAdjacentElement("afterend", editor);
  return template.innerHTML;
}

function installNativeStyle(root) {
  if (!root || root.querySelector("style[data-native-tool-yaml]")) return;
  const style = document.createElement("style");
  style.dataset.nativeToolYaml = "";
  style.textContent = NATIVE_STYLE;
  root.append(style);
}

export function bindNativeToolYaml(panel) {
  const root = panel?.shadowRoot;
  const textarea = root?.querySelector("#tool-yaml");
  const nativeEditor = root?.querySelector(`#${NATIVE_EDITOR_ID}`);
  if (!textarea || !nativeEditor) return;

  installNativeStyle(root);
  const valueDescriptor = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value");
  if (!valueDescriptor?.get || !valueDescriptor?.set) return;

  let rawYaml = valueDescriptor.get.call(textarea) || "";
  let nativeReady = false;
  let syncGeneration = 0;
  const originalFocus = textarea.focus.bind(textarea);

  const setFallbackValue = (value) => {
    rawYaml = String(value ?? "");
    valueDescriptor.set.call(textarea, rawYaml);
  };

  const showFallback = () => {
    nativeReady = false;
    nativeEditor.hidden = true;
    textarea.hidden = false;
  };

  const setNativeValue = (value) => {
    try {
      nativeEditor.setValue(value);
      return true;
    } catch (_err) {
      showFallback();
      return false;
    }
  };

  const syncNativeFromYaml = async (yaml) => {
    if (!nativeReady || typeof nativeEditor.setValue !== "function") return;
    const generation = ++syncGeneration;
    if (!String(yaml || "").trim()) {
      if (setNativeValue({})) nativeEditor.isValid = true;
      return;
    }
    try {
      const result = await panel._call("tools", "validate_yaml", {yaml});
      if (!nativeReady || generation !== syncGeneration) return;
      if (result?.valid) setNativeValue(result.config);
    } catch (_err) {
      // The existing backend validation/save flow remains authoritative. If the
      // native editor cannot be initialised from persisted YAML, keep the plain
      // textarea usable rather than making Function Tools inaccessible.
      if (generation === syncGeneration) showFallback();
    }
  };

  Object.defineProperty(textarea, "value", {
    configurable: true,
    get: () => rawYaml,
    set: (value) => {
      setFallbackValue(value);
      void syncNativeFromYaml(rawYaml);
    },
  });

  // Browser/user edits use the native HTMLTextAreaElement value setter directly
  // in some environments (including Playwright), bypassing the instance-level
  // property above. Keep the raw-YAML bridge synchronized from the real DOM value
  // so textarea fallback remains fully functional when ha-yaml-editor is absent.
  textarea.addEventListener("input", () => {
    rawYaml = String(valueDescriptor.get.call(textarea) ?? "");
  });

  textarea.focus = (...args) => {
    if (nativeReady && typeof nativeEditor.focus === "function") nativeEditor.focus();
    else originalFocus(...args);
  };

  nativeEditor.addEventListener("value-changed", (event) => {
    const yaml = String(nativeEditor.yaml ?? "");
    setFallbackValue(yaml);
    textarea.dispatchEvent(new Event("input", {bubbles: true}));
    if (event.detail?.isValid === false) {
      const status = root.querySelector("#tool-error");
      if (status) {
        status.className = "validation invalid";
        status.textContent = event.detail.errorMsg || "Function Tool YAML is invalid.";
      }
    }
  });

  nativeEditor.addEventListener("editor-save", () => {
    const dialog = root.querySelector("#tool-dialog");
    const save = root.querySelector("#tool-save");
    if (dialog?.open && save && !save.disabled) save.click();
  });

  const activate = () => {
    if (!nativeEditor.isConnected || typeof nativeEditor.setValue !== "function") return;
    try {
      nativeReady = true;
      textarea.hidden = true;
      nativeEditor.hidden = false;
      nativeEditor.inDialog = true;
      void syncNativeFromYaml(rawYaml);
    } catch (_err) {
      showFallback();
    }
  };

  if (customElements.get(NATIVE_EDITOR_TAG)) activate();
  else customElements.whenDefined(NATIVE_EDITOR_TAG).then(activate).catch(showFallback);
}

export function configurationDialogs(panel) {
  return decorateToolYamlEditor(base.configurationDialogs(panel));
}

export function bindTools(panel) {
  const result = base.bindTools(panel);
  bindNativeToolYaml(panel);
  return result;
}
