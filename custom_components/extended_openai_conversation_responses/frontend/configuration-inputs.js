import {CONFIGURATION_CONTROLS, applyConfigurationControl} from "./configuration-controls.js";
import {applyTargetedConfigDirty} from "./management-state-safety.js";
import {refreshSettingEffects} from "./management-setting-metadata.js";
import {configurationGuidanceChanged} from "./management-configuration-guidance.js";
import {bindSingleRequestSave} from "./management-actions.js";
import {saveBarMarkup, clone} from "./unsaved-state.js";
import {enhancementChanged} from "./management-enhancement-state.js";

export function refreshConfigurationSaveBar(panel) {
  const root = panel.shadowRoot;
  if (enhancementChanged(panel, "configuration-save-state", [Boolean(panel._configDirty)])) {
    for (const id of ["duplicate-agent", "export-agent"]) {
      const button = root.querySelector(`#${id}`);
      if (!button) continue;
      button.disabled = Boolean(panel._configDirty);
      if (panel._configDirty) button.title = "Save or revert the shared draft first";
      else button.removeAttribute("title");
    }
    const help = root.querySelector(".action-help");
    if (help) help.hidden = !panel._configDirty;
  }
  const bar = root.querySelector(".save-bar");
  if (!panel._configDirty) { bar?.remove(); return; }
  if (bar) return;
  const anchor = root.querySelector("#save-bar-anchor") || root.querySelector("#memory-settings-save-anchor");
  anchor?.insertAdjacentHTML("beforebegin", saveBarMarkup({configuration:true, pending:Boolean(panel._configurationSaving)}));
}

function setDependent(root, key, enabled) {
  const container = key === "memory_retrieval_mode" ? root.querySelector("[data-memory-hybrid]") : root.querySelector(`[data-dependent="${CSS.escape(key)}"]`);
  if (!container) return;
  container.classList.toggle("is-disabled", !enabled);
  container.querySelectorAll("input:not([readonly]),select,textarea:not([readonly]),button:not(.help-button)").forEach((control) => { control.disabled = !enabled; });
}

export function updateConfigurationControl(panel, control, eventType = "input") {
  const result = applyConfigurationControl(panel, control);
  if (!result) return null;
  const {key, changed} = result;
  if (changed) {
    const root = panel.shadowRoot;
    if (control.dataset?.type === "boolean") setDependent(root, key, control.checked);
    if (key === "conversation_continuity") setDependent(root, key, control.value !== "ha_default");
    if (key === "memory_retrieval_mode") setDependent(root, key, control.value === "hybrid");
    if (key === "prompt") {
      const counter = root.querySelector("#prompt-count");
      if (counter) counter.textContent = `${control.value.length.toLocaleString()} characters`;
    }
    applyTargetedConfigDirty(panel, [key], control, false);
    refreshConfigurationSaveBar(panel);
    refreshSettingEffects(panel, control);
    configurationGuidanceChanged(panel, key);
  }
  if (key === "api_mode" && eventType === "change") configurationGuidanceChanged(panel, key, true);
  return result;
}

export function bindConfigurationInputs(panel, handlers = {}) {
  const root = panel.shadowRoot;
  if (!root) return;
  if (root.__eocConfigurationInputs) {
    Object.assign(root.__eocConfigurationInputs, handlers);
    return;
  }
  root.__eocConfigurationInputs = {...handlers};
  bindSingleRequestSave(panel);
  const handle = (event) => {
    const control = event.composedPath?.().find((node) => node?.matches?.(CONFIGURATION_CONTROLS)) || event.target;
    if (!control?.matches?.(CONFIGURATION_CONTROLS)) return;
    const result = updateConfigurationControl(panel, control, event.type);
    if (!result || event.type !== "change") return;
    if (result.key === "chat_model") void root.__eocConfigurationInputs.modelChanged?.(control);
    if (result.key === "reasoning_effort") root.__eocConfigurationInputs.reasoningChanged?.(control);
    if (result.key === "api_mode" || result.key === "web_search") root.__eocConfigurationInputs.capabilityChanged?.(control);
  };
  root.addEventListener("input", handle);
  root.addEventListener("change", handle);
  root.addEventListener("click", (event) => {
    if (!event.target?.closest?.("#revert-config") || panel._configurationSaving) return;
    panel._draft = clone(panel._configData.config);
    panel._draftTitle = panel._configData.title;
    panel._setConfigDirty(false);
    // Explicit discard restores every mounted control, even when the cached
    // pre-edit markup already equals the baseline being restored.
    panel._eocMainMarkup = null;
    panel._render();
  });
}
