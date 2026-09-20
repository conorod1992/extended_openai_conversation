import {same, clone} from "./unsaved-state.js";

export const CONFIGURATION_CONTROLS = "[data-config],[data-memory-config],#voice-mappings,[data-local-intent-exclusion],.regex-pattern,.regex-replacement";
export const skillNamesFromText = (value) => String(value || "").split(/\r?\n/).map((item) => item.trim()).filter(Boolean);

export function configKeyForControl(control) {
  if (!control) return null;
  if (control.dataset?.config) return control.dataset.config;
  if (control.dataset?.memoryConfig) return control.dataset.memoryConfig;
  if (control.id === "voice-mappings") return "voice_device_mappings";
  if (control.matches?.("[data-local-intent-exclusion]")) return "local_intent_exclusions";
  if (control.matches?.(".regex-pattern,.regex-replacement")) {
    return "speech_regex_replacements";
  }
  if (control.id === "conversation-timeout-preset") {
    return "conversation_timeout_minutes";
  }
  return null;
}

export function configurationControlValue(control) {
  const key = control.dataset?.config || control.dataset?.memoryConfig;
  if (control.id === "voice-mappings") {
    try { return JSON.parse(control.value || "{}"); }
    catch (_) { return control.value; }
  }
  if (control.dataset?.type === "boolean" || control.type === "checkbox") return Boolean(control.checked);
  if (control.dataset?.type === "number") return Number(control.value);
  if (key === "skills") return skillNamesFromText(control.value);
  if (key?.startsWith("guest_readable_") || key?.startsWith("guest_controllable_")) {
    return String(control.value).split(",").map((item) => item.trim()).filter(Boolean);
  }
  return control.value;
}

// The full reader and delegated edits share both parsing and assignment rules.
// Compound controls touch only their own field/row, never the rest of the form.
export function applyConfigurationControl(panel, control) {
  if (!panel._draft || !control) return null;
  const key = configKeyForControl(control);
  let value = configurationControlValue(control);
  if (key === "local_intent_exclusions") {
    const values = new Set(panel._draft[key] || []);
    if (control.checked) values.add(control.value);
    else values.delete(control.value);
    // Rechecking a baseline choice restores its original position, so an
    // off/on round trip does not leave a spurious order-only dirty change.
    const baseline = panel._configData?.config?.local_intent_exclusions || [];
    const baselineKeys = new Set(baseline);
    value = [...baseline.filter((item) => values.has(item)), ...[...values].filter((item) => !baselineKeys.has(item))];
  }
  if (key === "speech_regex_replacements") {
    const index = Number(control.closest?.(".rule-row")?.dataset?.regexIndex);
    const rule = panel._draft[key]?.[index];
    if (!Number.isInteger(index) || index < 0 || !rule) return null;
    const field = control.classList.contains("regex-pattern") ? "pattern" : "replacement";
    const changed = rule[field] !== value;
    if (changed) rule[field] = value;
    return {key, changed};
  }
  if (!key) return null;
  const target = key === "__title" ? panel : panel._draft;
  const property = key === "__title" ? "_draftTitle" : key;
  const changed = !same(target[property], value);
  if (changed) target[property] = value;
  return {key, changed};
}

export function readConfigurationDraft(panel) {
  const root = panel.shadowRoot;
  const state = {_draft:clone(panel._draft || panel._result?.config || {}), _draftTitle:panel._draftTitle, _configData:panel._configData};
  if (root.querySelector("#local-intent-list")) state._draft.local_intent_exclusions = [];
  if (root.querySelector("#regex-rules")) {
    state._draft.speech_regex_replacements = [...root.querySelectorAll(".rule-row")].map(() => ({pattern:"", replacement:""}));
  }
  root.querySelectorAll(CONFIGURATION_CONTROLS).forEach((control) => applyConfigurationControl(state, control));
  panel._draft = state._draft;
  panel._draftTitle = state._draftTitle;
  return panel._draft;
}
