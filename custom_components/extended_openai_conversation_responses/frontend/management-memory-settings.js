import {getRouteFeature} from "./management-route.js";

const MEMORY_FIELDS = [
  "memory_mode",
  "temporary_memory",
  "memory_auto_retrieve_limit",
  "memory_retrieval_mode",
  "memory_embedding_model",
  "shared_memory_mode",
];
const MODEL_RESET_FIELDS = ["temperature", "top_p", "reasoning_effort", "service_tier", "shorten_tool_call_id"];

function stripMovedMemoryControls(html, view, documentRef = globalThis.document) {
  if (!documentRef?.createElement) return html;
  const template = documentRef.createElement("template");
  template.innerHTML = html;
  for (const key of MEMORY_FIELDS) template.content.querySelector(`[data-field="${key}"]`)?.remove();

  if (view === "assistant/model-responses") {
    const reset = template.content.querySelector("#reset-advanced");
    if (reset) reset.id = "reset-model-parameters";
  }
  return template.innerHTML;
}

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

export {MODEL_RESET_FIELDS, stripMovedMemoryControls};
