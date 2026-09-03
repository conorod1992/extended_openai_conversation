import {bindMemorySettings, renderMemorySettings} from "./memory-settings-ui.js";

const PATCHED = Symbol.for("extended-openai.management-memory-settings");
const MEMORY_FIELDS = [
  "memory_mode",
  "temporary_memory",
  "memory_auto_retrieve_limit",
  "memory_retrieval_mode",
  "memory_embedding_model",
  "shared_memory_mode",
];
const MODEL_RESET_FIELDS = ["temperature", "top_p", "reasoning_effort", "service_tier", "shorten_tool_call_id"];

function stripLegacyMemoryControls(html, view, documentRef = globalThis.document) {
  if (!documentRef?.createElement) return html;
  const template = documentRef.createElement("template");
  template.innerHTML = html;
  for (const key of MEMORY_FIELDS) template.content.querySelector(`[data-field="${key}"]`)?.remove();

  if (view === "assistant/advanced") {
    const description = template.content.querySelector("#config-capabilities .config-section-heading p:last-child");
    if (description) description.textContent = "Choose optional information sources and instruction sets the assistant can use.";
  }
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

export function installManagementMemorySettings(registry = globalThis.customElements) {
  if (!registry?.whenDefined) return Promise.resolve(false);
  return registry.whenDefined("extended-openai-management-panel").then(() => {
    const constructor = registry.get("extended-openai-management-panel");
    const prototype = constructor?.prototype;
    if (!prototype || prototype[PATCHED]) return false;

    const originalIsDraftView = prototype._isDraftView;
    prototype._isDraftView = function(page = this._page, subsection = this._subsection) {
      if (page === "data-memory" && subsection === "memory-settings") return this._data?.is_admin !== false;
      return originalIsDraftView.call(this, page, subsection);
    };

    const originalCanAccessView = prototype._canAccessView;
    prototype._canAccessView = function(page, subsection = null) {
      if (page === "data-memory" && subsection === "memory-settings" && this._data?.is_admin === false) return false;
      return originalCanAccessView.call(this, page, subsection);
    };

    const originalContent = prototype._content;
    prototype._content = function(agent) {
      const view = this._viewKey();
      if (view === "data-memory/memory-settings") return renderMemorySettings(this);
      const content = originalContent.call(this, agent);
      if (["assistant/advanced", "assistant/model-responses", "assistant/voice"].includes(view)) {
        return stripLegacyMemoryControls(content, view);
      }
      return content;
    };

    const originalBindActions = prototype._bindActions;
    prototype._bindActions = function(...args) {
      const result = originalBindActions.apply(this, args);
      const view = this._viewKey();
      if (view === "data-memory/memory-settings") bindMemorySettings(this);
      if (view === "assistant/model-responses") bindModelReset(this);
      return result;
    };

    prototype[PATCHED] = true;
    return true;
  });
}

if (typeof document !== "undefined" && typeof customElements !== "undefined") {
  installManagementMemorySettings();
}

export {MODEL_RESET_FIELDS, stripLegacyMemoryControls};
