import {bindTools as bindBaseTools, synchronizePersistedFunctions} from "./agent-config-tools-base.js";
import {
  getToolYamlEditor,
  installToolYamlEditor,
  toolYamlChangeHandler,
} from "./tool-yaml-editor-adapter.js";

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

async function assignToolToGroup(panel, select) {
  const config = panel?._draft || panel?._result?.config || {};
  const tools = config.functions || [];
  const groups = config.function_groups || [];
  const key = select.closest?.("[data-tool-key]")?.dataset.toolKey;
  const index = key === undefined ? Number(select.dataset.index) : tools.findIndex(tool => tool.spec?.name === key);
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
      synchronizePersistedFunctions(panel, response);
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
      synchronizePersistedFunctions(panel, response);
      panel._toast(`${name} is now available on every request`);
    }
    panel._render();
  } catch (err) {
    select.value = current?.id || "";
    select.disabled = false;
    panel._toast(`Unable to change Function Group: ${err.message || String(err)}`, true);
  } finally { select.disabled = false; }
}

export function bindNativeToolYaml(panel) {
  const root = panel?.shadowRoot;
  const textarea = root?.querySelector("#tool-yaml");
  const nativeEditor = root?.querySelector(`#${NATIVE_EDITOR_ID}`);
  if (!textarea || !nativeEditor) return;

  installNativeStyle(root);
  const current = getToolYamlEditor(panel);
  if (current?.nativeEditor === nativeEditor) return;

  let rawYaml = current?.getYaml?.() ?? String(textarea.value ?? "");
  let nativeReady = false;
  let destroyed = false;
  let syncGeneration = 0;

  const isCurrent = () => !destroyed
    && panel?._toolYamlEditorAdapter === adapter
    && nativeEditor.isConnected;

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
      if (isCurrent() && generation === syncGeneration && setNativeValue({})) {
        nativeEditor.isValid = true;
      }
      return;
    }

    const repairConfig = repairToolConfig(panel);
    if (repairConfig) {
      if (isCurrent() && generation === syncGeneration) setNativeValue(repairConfig);
      return;
    }

    try {
      const result = await panel._call("tools", "validate_yaml", {yaml});
      if (!isCurrent() || generation !== syncGeneration) return;
      if (result?.valid) {
        setNativeValue(result.config);
        return;
      }
      const starter = nativeStarterConfig(yaml, panel._toolOriginalName ?? null);
      if (starter) setNativeValue(starter);
      else showFallback();
    } catch (_err) {
      if (isCurrent() && generation === syncGeneration) showFallback();
    }
  };

  const onTextareaInput = () => {
    ++syncGeneration;
    rawYaml = String(textarea.value ?? "");
    toolYamlChangeHandler(panel, rawYaml);
  };

  const onNativeChange = (event) => {
    ++syncGeneration;
    rawYaml = String(nativeEditor.yaml ?? "");
    textarea.value = rawYaml;
    toolYamlChangeHandler(panel, rawYaml, event.detail || {});
  };

  const onNativeSave = () => {
    const dialog = root.querySelector("#tool-dialog");
    const save = root.querySelector("#tool-save");
    if (dialog?.open && save && !save.disabled) save.click();
  };

  textarea.addEventListener?.("input", onTextareaInput);
  nativeEditor.addEventListener("value-changed", onNativeChange);
  nativeEditor.addEventListener("editor-save", onNativeSave);

  const adapter = {
    textarea,
    nativeEditor,
    getYaml() {
      return rawYaml;
    },
    setYaml(value) {
      rawYaml = String(value ?? "");
      textarea.value = rawYaml;
      ++syncGeneration;
      if (nativeReady) void syncNativeFromYaml(rawYaml);
    },
    focus() {
      if (nativeReady && typeof nativeEditor.focus === "function") nativeEditor.focus();
      else textarea.focus();
    },
    destroy() {
      if (destroyed) return;
      destroyed = true;
      ++syncGeneration;
      textarea.removeEventListener?.("input", onTextareaInput);
      nativeEditor.removeEventListener?.("value-changed", onNativeChange);
      nativeEditor.removeEventListener?.("editor-save", onNativeSave);
    },
  };

  installToolYamlEditor(panel, adapter);

  const activate = () => {
    if (!isCurrent() || typeof nativeEditor.setValue !== "function") return;
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
      .then((ready) => {
        if (!isCurrent()) return;
        if (ready) activate();
        else showFallback();
      })
      .catch(() => {
        if (isCurrent()) showFallback();
      });
  }
}

export function bindTools(panel) {
  bindBaseTools(panel);
  bindNativeToolYaml(panel);
  const host = panel?.shadowRoot?.querySelector(".tools-surface");
  if (!host || host.__eocAssignmentBound) return;
  host.__eocAssignmentBound = true;
  host.addEventListener("change", event => {
    if (event.target.matches?.(".function-group-assignment") && !event.target.disabled) void assignToolToGroup(panel, event.target);
  });
}
