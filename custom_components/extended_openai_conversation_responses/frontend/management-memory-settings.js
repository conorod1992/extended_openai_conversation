import {getRouteFeature} from "./management-route.js";

const MODEL_RESET_FIELDS = ["temperature", "top_p", "reasoning_effort", "service_tier", "shorten_tool_call_id"];

function bindModelReset(panel) {
  panel.shadowRoot.querySelector("#reset-model-parameters")?.addEventListener("click", () => {
    const defaults = panel._result?.defaults || {};
    let trigger = null;
    for (const key of MODEL_RESET_FIELDS) {
      const input = panel.shadowRoot.querySelector(`[data-config="${key}"]`);
      if (!input || !(key in defaults)) continue;
      if (input.dataset.type === "boolean") input.checked = Boolean(defaults[key]);
      else input.value = defaults[key] ?? "";
      trigger ||= input;
    }
    trigger?.dispatchEvent(new Event("input", {bubbles: true}));
  });
}

export function bindMemorySettings(panel) {
  const view = panel._viewKey();
  if (view === "data-memory/memory-settings") getRouteFeature(view)?.bindMemorySettings(panel);
  if (view === "assistant/model-responses") bindModelReset(panel);
}

export {MODEL_RESET_FIELDS};
