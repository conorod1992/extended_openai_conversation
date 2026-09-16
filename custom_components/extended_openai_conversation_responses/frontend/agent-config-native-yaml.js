import * as base from "./agent-config-editor-model-v2.js";

export * from "./agent-config-editor-model-v2.js";

const NATIVE_EDITOR_TAG = "ha-yaml-editor";
const NATIVE_EDITOR_ID = "tool-yaml-native";
const NEW_TOOL_STARTER_YAML = `spec:\n  name: my_tool\n  description: Describe what this tool does.\n  parameters:\n    type: object\n    properties: {}\nfunction:\n  type: native\n  name: ''\n`;
const NEW_TOOL_STARTER_CONFIG = Object.freeze({
  spec: Object.freeze({
    name: "my_tool",
    description: "Describe what this tool does.",
    parameters: Object.freeze({type: "object", properties: Object.freeze({})}),
  }),
  function: Object.freeze({type: "native", name: ""}),
});
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
const FUNCTION_GROUP_ASSIGNMENT_STYLE = `
  .function-group-assignment-control {
    display: inline-flex;
    align-items: center;
    gap: 0;
    width: fit-content;
    max-width: min(300px, 100%);
    min-height: 32px;
    margin-top: 9px;
    border: 1px solid var(--divider-color, rgba(127, 127, 127, .35));
    border-radius: 999px;
    background: var(--card-background-color, transparent);
    color: var(--primary-text-color);
    transition: border-color 120ms ease, background 120ms ease;
  }
  .function-group-assignment-control:hover,
  .function-group-assignment-control:focus-within {
    border-color: var(--primary-color);
    background: var(--secondary-background-color, transparent);
  }
  .function-group-assignment-control.is-disabled-group { opacity: .72; }
  .function-group-assignment-icon {
    --mdc-icon-size: 16px;
    display: inline-flex;
    align-items: center;
    flex: 0 0 auto;
    padding-inline-start: 10px;
    color: var(--secondary-text-color);
  }
  .function-group-assignment {
    width: auto;
    min-width: 0;
    max-width: 250px;
    min-height: 30px;
    border: 0;
    border-radius: 999px;
    outline: 0;
    background: transparent;
    color: inherit;
    font: inherit;
    font-size: 13px;
    font-weight: 500;
    padding: 4px 10px 4px 6px;
    cursor: pointer;
  }
  .function-group-assignment:disabled { cursor: progress; }
  @media (max-width: 700px) {
    .function-group-assignment-control { max-width: 100%; }
    .function-group-assignment { max-width: 210px; }
  }
`;

export function nativeStarterConfig(yaml, originalName = null) {
  if (originalName !== null) return null;
  const normalized = String(yaml || "").replace(/\r\n/g, "\n");
  if (normalized !== NEW_TOOL_STARTER_YAML) return null;
  return {
    spec: {
      name: NEW_TOOL_STARTER_CONFIG.spec.name,
      description: NEW_TOOL_STARTER_CONFIG.spec.description,
      parameters: {type: "object", properties: {}},
    },
    function: {type: "native", name: ""},
  };
}

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

function currentGroupForTool(groups, name) {
  return (groups || []).find((group) => (group.functions || []).includes(name)) || null;
}

function assignmentOptions(panel, groups, currentId) {
  return [
    `<option value="" ${currentId ? "" : "selected"}>Available on every request</option>`,
    ...(groups || []).map((group) => {
      const disabled = group.enabled === false ? " (disabled)" : "";
      return `<option value="${panel._e(group.id)}" ${group.id === currentId ? "selected" : ""}>${panel._e(group.name)}${disabled}</option>`;
    }),
  ].join("");
}

export function decorateFunctionGroupAssignments(panel, html) {
  if (typeof document === "undefined" || typeof document.createElement !== "function") return html;
  const template = document.createElement("template");
  template.innerHTML = html;
  const config = panel?._draft || panel?._result?.config || {};
  const tools = config.functions || [];
  const groups = config.function_groups || [];

  for (const card of template.content.querySelectorAll(".tool-card[data-tool-index]")) {
    const index = Number(card.dataset.toolIndex);
    const tool = tools[index];
    const name = tool?.spec?.name;
    if (!name || card.querySelector(".function-group-assignment")) continue;
    const current = currentGroupForTool(groups, name);
    const main = card.querySelector(".card-main");
    if (!main) continue;

    const label = document.createElement("label");
    label.className = `function-group-assignment-control${current?.enabled === false ? " is-disabled-group" : ""}`;
    label.title = current?.enabled === false
      ? `Function group: ${current.name}. This group is currently disabled.`
      : `Function group: ${current?.name || "Available on every request"}`;
    label.innerHTML = `<span class="sr-only">Function group for ${panel._e(name)}</span><ha-icon class="function-group-assignment-icon" icon="mdi:folder-outline" aria-hidden="true"></ha-icon><select class="function-group-assignment" data-index="${index}" aria-label="Function group for ${panel._e(name)}">${assignmentOptions(panel, groups, current?.id || "")}</select>`;
    main.insertAdjacentElement("beforeend", label);
  }

  if (!template.content.querySelector("style[data-function-group-assignment]")) {
    const style = document.createElement("style");
    style.dataset.functionGroupAssignment = "";
    style.textContent = FUNCTION_GROUP_ASSIGNMENT_STYLE;
    template.content.prepend(style);
  }
  return template.innerHTML;
}

async function assignToolToGroup(panel, select) {
  const config = panel?._draft || panel?._result?.config || {};
  const tools = config.functions || [];
  const groups = config.function_groups || [];
  const index = Number(select.dataset.index);
  const tool = tools[index];
  const name = tool?.spec?.name;
  if (!name) return;

  const current = currentGroupForTool(groups, name);
  const targetId = select.value;
  if ((current?.id || "") === targetId) return;
  select.disabled = true;

  try {
    let response;
    if (targetId) {
      const target = groups.find((group) => group.id === targetId);
      if (!target) throw new Error("The selected Function Group no longer exists");
      response = await panel._call("tools", "save_group", {
        group: {
          ...target,
          functions: [...new Set([...(target.functions || []).filter((item) => item !== name), name])],
        },
        original_id: target.id,
      });
      base.synchronizePersistedFunctions(panel, response);
      panel._toast(`${name} moved to ${target.name}`);
    } else {
      if (!current) {
        select.disabled = false;
        return;
      }
      response = await panel._call("tools", "save_group", {
        group: {
          ...current,
          functions: (current.functions || []).filter((item) => item !== name),
        },
        original_id: current.id,
      });
      base.synchronizePersistedFunctions(panel, response);
      panel._toast(`${name} is now available on every request`);
    }
    panel._render();
  } catch (err) {
    select.value = current?.id || "";
    select.disabled = false;
    panel._toast(`Unable to change Function Group: ${err.message || String(err)}`, true);
  }
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
      if (result?.valid) {
        setNativeValue(result.config);
        return;
      }
      // The backend's generic new-tool starter is intentionally semantically
      // incomplete (native implementation name is blank) so it cannot pass the
      // save validator yet. It is nevertheless valid YAML and has a stable,
      // controlled shape; hydrate that partial object so Add Function Tool still
      // opens in Home Assistant's native editor. Other backend-invalid documents
      // fall back to the raw textarea rather than showing an empty/stale editor.
      const starter = nativeStarterConfig(yaml, panel._toolOriginalName ?? null);
      if (starter) setNativeValue(starter);
      else showFallback();
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
  textarea.addEventListener?.("input", () => {
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

export function renderTools(panel) {
  return decorateFunctionGroupAssignments(panel, base.renderTools(panel));
}

export function bindTools(panel) {
  const result = base.bindTools(panel);
  bindNativeToolYaml(panel);
  panel?.shadowRoot?.querySelectorAll(".function-group-assignment").forEach((select) => {
    select.addEventListener("change", () => void assignToolToGroup(panel, select));
  });
  return result;
}
