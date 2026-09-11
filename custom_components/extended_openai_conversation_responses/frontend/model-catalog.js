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

export function modelDataControls() {
  return `<div class="section-actions"><button type="button" class="secondary" data-model-data="update">Update model data</button><button type="button" class="secondary" data-model-data="reset">Use bundled model data</button></div><p class="help" data-model-data-status role="status">Model data is shared by all agents and checked daily. Reset uses bundled data until the next daily check.</p>`;
}

export function bindModelDataControls(panel, onUpdated = () => {}) {
  const root = panel.shadowRoot;
  const buttons = root.querySelectorAll("[data-model-data]");
  buttons.forEach((button) => button.addEventListener("click", async () => {
    buttons.forEach((item) => { item.disabled = true; });
    const status = root.querySelector("[data-model-data-status]");
    try {
      const model = root.querySelector('[data-config="chat_model"]')?.value || "";
      const result = await lookupModelData(panel, model, button.dataset.modelData);
      status.textContent = result.last_error || `Using ${result.source} model data, version ${result.catalog_version}.`;
      if (!result.last_error) onUpdated(status.textContent);
    } catch (err) {
      if (button.dataset.modelData === "update") {
        status.textContent = "Unable to update model data. The existing model data is still in use. Check Home Assistant's internet connection and try again.";
      } else {
        status.textContent = `Unable to use bundled model data: ${err.message || String(err)}`;
      }
    } finally {
      buttons.forEach((item) => { item.disabled = false; });
    }
  }));
}
