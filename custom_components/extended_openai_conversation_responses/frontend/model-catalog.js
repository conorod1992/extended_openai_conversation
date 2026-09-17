// All model matching and capability choices come from the Python catalogue.
export async function lookupModelData(panel, model = "", action = "lookup") {
  const result = await panel._hass.callWS({
    type: "extended_openai_conversation_responses/model_catalog", action, model,
  });
  if (panel && result) {
    panel._modelCatalogData = {...result, requested_model: String(model || "")};
    if (panel._result && result.model_capabilities) panel._result.model_capabilities = result.model_capabilities;
  }
  return result;
}

export function parameterControlState(capability = {}, effort = null, configuredValue = null) {
  const support = capability?.support || "undocumented";
  const configured = configuredValue !== undefined && configuredValue !== null && configuredValue !== "";
  if (support === "always") return {visible: true, enabled: true, inactive: false, reason: ""};
  if (support === "conditional") {
    const allowed = Array.isArray(capability.allowed_reasoning_efforts) ? capability.allowed_reasoning_efforts : [];
    if (allowed.includes(effort)) return {visible: true, enabled: true, inactive: false, reason: ""};
    return {
      visible: configured,
      enabled: false,
      inactive: configured,
      reason: configured
        ? `This saved value is inactive at reasoning effort “${effort || "unset"}” and will not be sent.`
        : "",
    };
  }
  return {
    visible: configured,
    enabled: false,
    inactive: configured,
    reason: configured
      ? `${support === "never" ? "This parameter is not supported by the selected model." : "Current model-specific documentation does not establish support for this parameter."} The saved value is inactive and will not be sent.`
      : "",
  };
}

export function apiPathSelectable(metadata = {}, api, toolsRequired = false) {
  if (api === "auto") return true;
  if (!metadata?.api?.[api]) return false;
  return !toolsRequired || Boolean(metadata?.function_calling?.[api]);
}

export function pickerModels(result = {}, selectedModel = "") {
  const selected = String(selectedModel || "");
  const models = Array.isArray(result.catalog_models) ? result.catalog_models : [];
  return models.filter((item) => item?.status === "current" || item?.id === selected);
}

export function modelDataStatusText(result = {}) {
  const active = Number(result.catalog_version);
  const available = Number(result.available_catalog_version);
  const activeLabel = Number.isInteger(active) ? `v${active}` : "the current version";
  if (result.last_error) return result.last_error;
  if (result.update_available && Number.isInteger(available)) {
    return `Update available: ${activeLabel} → v${available}. The newer catalogue will not be used until you apply it.`;
  }
  if (result.source === "bundled") {
    return `Using bundled model data (${activeLabel}). Background checks run daily but do not apply updates automatically.`;
  }
  return `Model data is current (${activeLabel}). Background checks run daily; updates are applied only when you choose to apply them.`;
}

export function modelDataControls() {
  return `<div class="section-actions"><button type="button" class="secondary" data-model-data="check">Check for updates</button><button type="button" class="secondary" data-model-data="apply" hidden disabled>Apply update</button><button type="button" class="secondary" data-model-data="reset">Restore bundled data</button></div><p class="help" data-model-data-status role="status">Model data is shared by all agents. Background checks run daily; updates are applied only when you choose to apply them.</p>`;
}

function syncModelDataControls(panel, result = panel?._modelCatalogData || {}) {
  const root = panel?.shadowRoot;
  if (!root) return;
  const apply = root.querySelector('[data-model-data="apply"]');
  if (apply) {
    apply.hidden = !result.update_available;
    apply.disabled = !result.update_available;
    if (result.update_available && Number.isInteger(Number(result.available_catalog_version))) {
      apply.textContent = `Apply v${Number(result.available_catalog_version)} update`;
    } else {
      apply.textContent = "Apply update";
    }
  }
  const status = root.querySelector("[data-model-data-status]");
  if (status && result.catalog_version !== undefined) status.textContent = modelDataStatusText(result);
}

export function bindModelDataControls(panel, onUpdated = () => {}) {
  const root = panel.shadowRoot;
  syncModelDataControls(panel);
  const buttons = root.querySelectorAll("[data-model-data]");
  buttons.forEach((button) => button.addEventListener("click", async () => {
    buttons.forEach((item) => { item.disabled = true; });
    const status = root.querySelector("[data-model-data-status]");
    try {
      const model = root.querySelector('[data-config="chat_model"]')?.value || "";
      const result = await lookupModelData(panel, model, button.dataset.modelData);
      if (status) status.textContent = modelDataStatusText(result);
      syncModelDataControls(panel, result);
      if (!result.last_error) onUpdated(status?.textContent || "Model data updated.");
    } catch (err) {
      if (!status) return;
      if (button.dataset.modelData === "check") {
        status.textContent = "Unable to check for model data updates. The existing model data is still in use. Check Home Assistant's internet connection and try again.";
      } else if (button.dataset.modelData === "apply") {
        status.textContent = `Unable to apply the model data update: ${err.message || String(err)}`;
      } else {
        status.textContent = `Unable to restore bundled model data: ${err.message || String(err)}`;
      }
    } finally {
      buttons.forEach((item) => {
        if (item.dataset.modelData === "apply") item.disabled = !panel?._modelCatalogData?.update_available;
        else item.disabled = false;
      });
    }
  }));
}
