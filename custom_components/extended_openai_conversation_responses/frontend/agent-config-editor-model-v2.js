import * as base from "./agent-config-editor-base.js";
import {lookupModelData} from "./model-catalog.js";

export * from "./agent-config-editor-base.js";

function currentConfig(panel) {
  return panel?._draft || panel?._result?.config || {};
}

function currentCatalogData(panel, model) {
  const data = panel?._modelCatalogData;
  return data?.requested_model === String(model || "") ? data : null;
}

async function ensureCatalogData(panel) {
  const config = currentConfig(panel);
  const model = String(config.chat_model || "");
  if (currentCatalogData(panel, model)) return;
  const searchTarget = panel._pendingSettingFocus;
  try {
    await lookupModelData(panel, model);
    panel._configRestoreFocus = searchTarget ? `#${CSS.escape(searchTarget)}` : '[data-config="chat_model"]';
    panel._render();
  } catch (err) {
    panel._toast?.(`Unable to inspect model capabilities: ${err.message || String(err)}`, true);
  }
}

function applyModelDefaults(panel, model, data) {
  if (!panel._draft) panel._draft = {...(panel._result?.config || {})};
  panel._draft.chat_model = model;
  const reasoning = data?.model_metadata?.reasoning || {};
  const efforts = Array.isArray(reasoning.efforts) ? reasoning.efforts : [];
  if (!reasoning.supported || !efforts.length) {
    delete panel._draft.reasoning_effort;
    return;
  }
  const recommended = data?.model_metadata?.recommended_profile?.reasoning_effort;
  panel._draft.reasoning_effort = efforts.includes(recommended) ? recommended : efforts[0];
}

export function bindConfiguration(panel) {
  const result = base.bindConfiguration(panel);
  const root = panel?.shadowRoot;
  const modelInput = root?.querySelector('[data-config="chat_model"]');
  const reasoning = root?.querySelector('[data-config="reasoning_effort"]');
  const hasModelAwareControls = Boolean(
    modelInput
    || reasoning
    || root?.querySelector('[data-config="temperature"],[data-config="top_p"],[data-config="api_mode"],[data-config="max_tokens"]')
  );
  if (hasModelAwareControls) void ensureCatalogData(panel);

  modelInput?.addEventListener("change", async (event) => {
    event.stopImmediatePropagation();
    try {
      const model = modelInput.value;
      const data = await lookupModelData(panel, model);
      if (modelInput.value !== model) return;
      applyModelDefaults(panel, model, data);
      const validation = await panel._call("configuration", "validate", {config: panel._draft});
      if (!validation.valid) return;
      panel._result.model_capabilities = validation.model_capabilities;
      panel._setConfigDirty?.(true);
      panel._configRestoreFocus = '[data-config="chat_model"]';
      panel._render();
    } catch (err) {
      panel._toast?.(`Unable to inspect model options: ${err.message || String(err)}`, true);
    }
  }, true);
  reasoning?.addEventListener("change", () => {
    if (!panel._draft) panel._draft = {...(panel._result?.config || {})};
    panel._draft.reasoning_effort = reasoning.value;
    panel._setConfigDirty?.(true);
    panel._configRestoreFocus = '[data-config="reasoning_effort"]';
    panel._render();
  });
  return result;
}
