// All model matching and capability choices come from the Python catalogue.
export async function lookupModelData(panel, model = "", action = "lookup") {
  return panel._hass.callWS({
    type: "extended_openai_conversation_responses/model_catalog", action, model,
  });
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
      if (!result.last_error) {
        panel._result.model_capabilities = result.model_capabilities;
        onUpdated(status.textContent);
      }
    } catch (err) {
      status.textContent = `Unable to update model data: ${err.message || String(err)}`;
    } finally {
      buttons.forEach((item) => { item.disabled = false; });
    }
  }));
}
