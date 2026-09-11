import * as base from "./agent-config-editor-base.js";
import {
  apiPathSelectable,
  lookupModelData,
  parameterControlState,
} from "./model-catalog.js";

export * from "./agent-config-editor-base.js";

const hasValue = (value) => value !== undefined && value !== null && value !== "";
const titleCase = (value) => String(value || "").replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());

function currentConfig(panel) {
  return panel?._draft || panel?._result?.config || {};
}

function toolsRequired(config = {}) {
  return (config.functions || []).some((tool) => tool?.enabled !== false);
}

function currentCatalogData(panel, model) {
  const data = panel?._modelCatalogData;
  return data?.requested_model === String(model || "") ? data : null;
}

function addStatus(setting, text, className = "capability-note") {
  if (!setting || !text || setting.querySelector(`.${className}`)) return;
  const note = setting.ownerDocument.createElement("small");
  note.className = className;
  note.textContent = text;
  setting.append(note);
}

function decorateModelPicker(root, panel, config, data) {
  const input = root.querySelector('[data-config="chat_model"]');
  if (!input || !data) return;
  const listId = "extended-openai-model-catalog";
  input.setAttribute("list", listId);
  let list = root.querySelector(`#${listId}`);
  if (!list) {
    list = input.ownerDocument.createElement("datalist");
    list.id = listId;
    input.closest("[data-field=\"chat_model\"]")?.append(list);
  }
  list.replaceChildren(...(data.catalog_models || []).map((item) => {
    const option = input.ownerDocument.createElement("option");
    option.value = item.id;
    option.label = `${item.display_name || item.id}${item.status === "deprecated" ? " — Deprecated" : ""}`;
    return option;
  }));

  const metadata = data.model_metadata || {};
  const setting = input.closest('[data-field="chat_model"]');
  if (metadata.status === "deprecated") {
    addStatus(
      setting,
      `Deprecated model. It remains selected for this existing configuration and will not be changed automatically.${metadata.lifecycle_note ? ` ${metadata.lifecycle_note}` : ""}`,
      "model-lifecycle-note",
    );
  } else if (metadata.status === "unknown" && hasValue(config.chat_model)) {
    addStatus(
      setting,
      "Custom or unknown model. Extended OpenAI will use conservative capabilities until explicit metadata is available.",
      "model-lifecycle-note",
    );
  }
}

function decorateReasoning(root, config, metadata) {
  const reasoning = metadata?.reasoning || {};
  const setting = root.querySelector('[data-field="reasoning_effort"]');
  if (!reasoning.supported) {
    setting?.remove();
    return;
  }
  const select = setting?.querySelector('[data-config="reasoning_effort"]');
  if (!select) return;
  const selected = String(config.reasoning_effort ?? metadata.recommended_profile?.reasoning_effort ?? "");
  select.replaceChildren(...(reasoning.efforts || []).map((effort) => {
    const option = select.ownerDocument.createElement("option");
    option.value = effort;
    option.textContent = titleCase(effort);
    option.selected = effort === selected;
    return option;
  }));
}

function decorateSampling(root, config, metadata) {
  for (const parameter of ["temperature", "top_p"]) {
    const setting = root.querySelector(`[data-field="${parameter}"]`);
    if (!setting) continue;
    const state = parameterControlState(metadata?.[parameter], config.reasoning_effort, config[parameter]);
    if (!state.visible) {
      setting.remove();
      continue;
    }
    const input = setting.querySelector(`[data-config="${parameter}"]`);
    if (!input) continue;
    input.disabled = !state.enabled;
    setting.classList.toggle("is-disabled", !state.enabled);
    if (!state.enabled && state.reason) addStatus(setting, state.reason);
  }
}

function decorateApiSelector(root, config, metadata) {
  const select = root.querySelector('[data-config="api_mode"]');
  if (!select || !metadata) return;
  const needsTools = toolsRequired(config);
  let selectedInvalid = false;
  for (const option of select.options) {
    if (option.value === "auto") {
      option.disabled = false;
      continue;
    }
    const allowed = apiPathSelectable(metadata, option.value, needsTools);
    option.disabled = !allowed;
    if (option.selected && !allowed) selectedInvalid = true;
  }
  const setting = select.closest('[data-field="api_mode"]');
  if (selectedInvalid) {
    addStatus(
      setting,
      needsTools
        ? "This API path cannot be used with the currently enabled tools for this model. Choose Auto or a supported path before saving."
        : "This API path is not supported by the selected model.",
    );
  }
}

function decorateConfiguration(panel, html) {
  if (typeof document === "undefined" || typeof document.createElement !== "function") return html;
  const config = currentConfig(panel);
  const data = currentCatalogData(panel, config.chat_model);
  const metadata = data?.model_metadata || panel?._result?.model_capabilities || {};
  const template = document.createElement("template");
  template.innerHTML = html;
  const root = template.content;
  decorateModelPicker(root, panel, config, data);
  decorateReasoning(root, config, metadata);
  decorateSampling(root, config, metadata);
  decorateApiSelector(root, config, metadata);
  return template.innerHTML;
}

async function ensureCatalogData(panel) {
  const config = currentConfig(panel);
  const model = String(config.chat_model || "");
  if (currentCatalogData(panel, model)) return;
  try {
    await lookupModelData(panel, model);
    panel._configRestoreFocus = '[data-config="chat_model"]';
    panel._render();
  } catch (err) {
    panel._toast?.(`Unable to inspect model capabilities: ${err.message || String(err)}`, true);
  }
}

export function renderConfiguration(panel) {
  return decorateConfiguration(panel, base.renderConfiguration(panel));
}

export function bindConfiguration(panel) {
  const result = base.bindConfiguration(panel);
  void ensureCatalogData(panel);
  const reasoning = panel?.shadowRoot?.querySelector('[data-config="reasoning_effort"]');
  reasoning?.addEventListener("change", () => {
    if (!panel._draft) panel._draft = {...(panel._result?.config || {})};
    panel._draft.reasoning_effort = reasoning.value;
    panel._setConfigDirty?.(true);
    panel._configRestoreFocus = '[data-config="reasoning_effort"]';
    panel._render();
  });
  return result;
}
