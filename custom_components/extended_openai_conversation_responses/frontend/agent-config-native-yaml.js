import * as base from "./agent-config-editor-model-v2.js";

export * from "./agent-config-editor-model-v2.js";

const NATIVE_EDITOR_TAG = "ha-yaml-editor";
const NATIVE_EDITOR_ID = "tool-yaml-native";
let nativeEditorLoadPromise = null;
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
  #tool-dialog.tool-dialog {
    width: min(1100px, calc(100vw - 32px));
    max-height: calc(100dvh - 32px);
    overflow: hidden;
  }
  #tool-dialog.tool-dialog[open] {
    display: flex;
    flex-direction: column;
  }
  #tool-dialog .tool-dialog-body {
    display: flex;
    flex: 1 1 auto;
    flex-direction: column;
    min-height: 0;
    overflow: hidden;
  }
  #tool-dialog #tool-editor-label {
    display: flex;
    flex: 1 1 auto;
    flex-direction: column;
    min-height: 0;
  }
  #${NATIVE_EDITOR_ID} {
    display: block;
    flex: 1 1 auto;
    width: 100%;
    min-height: 240px;
    height: 100%;
    cursor: text;
  }
  #tool-dialog .dialog-actions {
    flex: 0 0 auto;
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
  .function-group-card[data-group-id],
  .function-group-card.always-card {
    background: var(--secondary-background-color, var(--card-background-color));
    background: color-mix(in srgb, var(--primary-color) 5%, var(--card-background-color));
    box-shadow: inset 3px 0 0 color-mix(in srgb, var(--primary-color) 28%, transparent);
  }
  .function-group-card.function-repair-attention {
    background: color-mix(in srgb, var(--warning-color, #ff9800) 8%, var(--card-background-color));
    box-shadow: inset 3px 0 0 color-mix(in srgb, var(--warning-color, #ff9800) 45%, transparent);
  }
  .function-group-card > details .tool-card {
    background: var(--card-background-color);
  }
  .function-group-card[data-group-id] + .function-group-card[data-group-id] {
    margin-top: 16px;
  }
  .group-enabled-control { flex: 0 0 auto; }
  @media (max-width: 700px) {
    .function-group-assignment-control { max-width: 100%; }
    .function-group-assignment { max-width: 210px; }
  }
`;

export async function ensureNativeYamlEditor(
  registry = globalThis.customElements,
  documentRef = globalThis.document,
) {
  if (registry?.get?.(NATIVE_EDITOR_TAG)) return true;
  if (!registry?.whenDefined) return false;
  if (!nativeEditorLoadPromise) {
    nativeEditorLoadPromise = (async () => {
      if (documentRef?.createElement) {
        try {
          const resolver = documentRef.createElement("partial-panel-resolver");
          const routes = resolver?.getRoutes?.([
            {component_name: "developer-tools", url_path: "a"},
          ]);
          await routes?.routes?.a?.load?.();

          if (!registry.get?.(NATIVE_EDITOR_TAG)) {
            const router = documentRef.createElement("developer-tools-router");
            await router?.routerOptions?.routes?.service?.load?.();
          }
        } catch (_err) {
          // Some HA versions/tests do not expose the internal lazy-loader elements.
          // Fall through to the standards-based custom-element readiness contract.
        }
      }
      if (registry.get?.(NATIVE_EDITOR_TAG)) return true;
      await registry.whenDefined(NATIVE_EDITOR_TAG);
      return true;
    })().finally(() => { nativeEditorLoadPromise = null; });
  }
  return nativeEditorLoadPromise;
}

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

export function repairToolConfig(panel) {
  const index = panel?._repairToolIndex;
  if (!Number.isInteger(index)) return null;
  const invalidTools = panel?._result?.function_repair?.invalid_tools;
  if (!Array.isArray(invalidTools)) return null;
  const item = invalidTools.find((candidate) => Number(candidate?.index) === index);
  const tool = item?.tool;
  if (!tool || typeof tool !== "object" || Array.isArray(tool)) return null;
  return tool;
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

function decorateFunctionGroupCards(panel, root, groups) {
  for (const card of root.querySelectorAll(".function-group-card[data-group-id]")) {
    const group = groups.find((item) => item.id === card.dataset.groupId);
    if (!group) continue;
    const enabled = group.enabled !== false;
    card.classList.toggle("is-disabled", !enabled);
    const title = card.querySelector(".tool-title");
    if (!enabled && title && !title.querySelector(".group-disabled-badge")) {
      title.insertAdjacentHTML("beforeend", '<span class="availability-badge group-disabled-badge">Disabled</span>');
    }
    const editButton = card.querySelector(".edit-group");
    if (editButton) {
      editButton.textContent = "Edit";
      editButton.disabled = !enabled;
      editButton.setAttribute("aria-label", `Edit Function Group ${group.name}`);
      editButton.title = enabled ? `Edit Function Group ${group.name}` : "Enable this Function Group before editing it";
    }
    const deleteButton = card.querySelector(".delete-group");
    if (deleteButton) {
      deleteButton.textContent = "Delete";
      deleteButton.setAttribute("aria-label", `Delete Function Group ${group.name}`);
      deleteButton.title = `Delete Function Group ${group.name}`;
    }
    const actions = card.querySelector(".function-group-heading .actions");
    if (actions && !actions.querySelector(".group-enabled")) {
      actions.insertAdjacentHTML("afterbegin", `<label class="tool-enabled-control group-enabled-control" title="Disable the group without changing the enabled state of its member Function Tools"><span>Enabled</span><span class="switch-control"><input type="checkbox" role="switch" class="group-enabled" data-group-id="${panel._e(group.id)}" aria-label="Enable Function Group ${panel._e(group.name)}" ${enabled ? "checked" : ""}><span class="switch-track" aria-hidden="true"></span></span></label>`);
    }
  }
}

export function decorateFunctionGroupAssignments(panel, html) {
  if (typeof document === "undefined" || typeof document.createElement !== "function") return html;
  const template = document.createElement("template");
  template.innerHTML = html;
  const config = panel?._draft || panel?._result?.config || {};
  const tools = config.functions || [];
  const groups = config.function_groups || [];

  decorateFunctionGroupCards(panel, template.content, groups);

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
    style.dataset.functionGroupsDecorated = "";
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

    const repairConfig = repairToolConfig(panel);
    if (repairConfig) {
      setNativeValue(repairConfig);
      return;
    }

    try {
      const result = await panel._call("tools", "validate_yaml", {yaml});
      if (!nativeReady || generation !== syncGeneration) return;
      if (result?.valid) {
        setNativeValue(result.config);
        return;
      }
      const starter = nativeStarterConfig(yaml, panel._toolOriginalName ?? null);
      if (starter) setNativeValue(starter);
      else showFallback();
    } catch (_err) {
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
  else {
    void ensureNativeYamlEditor()
      .then((ready) => { if (ready) activate(); else showFallback(); })
      .catch(showFallback);
  }
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
