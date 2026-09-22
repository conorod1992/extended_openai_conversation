import {SETTINGS_INDEX} from "./frontend-navigation.js";
export {dirtyConfigurationKeys, configurationDestinations, dirtyConfigurationDestinations} from "./management-config-destinations.js";

const FRIENDLY_LABEL_OVERRIDES = Object.freeze({
  memory_retrieval_mode: "Relevance matching",
  max_function_calls_per_conversation: "Tool-call limit per conversation",
  speech_processing_enabled: "Clean responses for speech",
  speech_strip_markdown: "Remove Markdown formatting",
});

const TECHNICAL_SEARCH_ALIASES = Object.freeze({
  local_intents_enabled: "hassil",
});

const FRIENDLY_VALUE_LABELS = Object.freeze({
  api_mode: Object.freeze({
    auto: "Automatic (Auto)",
    responses: "Responses API",
    chat_completions: "Chat Completions API",
  }),
  memory_mode: Object.freeze({
    off: "Off",
    manual: "Only when I ask (Manual)",
    automatic: "Automatic",
  }),
  memory_retrieval_mode: Object.freeze({
    lexical: "Keyword matching (Lexical)",
    hybrid: "Semantic + keyword matching (Hybrid)",
  }),
  shared_memory_mode: Object.freeze({
    disabled: "Off",
    explicit: "Only when I ask (Explicit)",
    automatic: "Automatic",
  }),
  web_search_context: Object.freeze({
    low: "Low detail",
    medium: "Medium detail",
    high: "High detail",
  }),
});

const SETTING_BY_KEY = new Map();
for (const item of SETTINGS_INDEX) {
  if (item.configKey && !SETTING_BY_KEY.has(item.configKey)) SETTING_BY_KEY.set(item.configKey, item);
}

function ownerForKey(key) {
  const item = SETTING_BY_KEY.get(key);
  if (item) return [item.page, item.section];
  return EXTRA_CONFIG_OWNERS[key] || null;
}

export function friendlySettingLabel(key) {
  return FRIENDLY_LABEL_OVERRIDES[key] || SETTING_BY_KEY.get(key)?.label || null;
}

export function friendlySettingValue(key, value) {
  return FRIENDLY_VALUE_LABELS[key]?.[String(value)] || null;
}

export function settingEffectBadges(key, value, {disabled = false} = {}) {
  if (disabled) return [];
  if (key === "memory_retrieval_mode") {
    if (value === "hybrid") return ["Requires embeddings"];
    if (value === "lexical") return ["No embedding request"];
  }
  if (key === "local_intents_enabled" && value === true) return ["No AI call when matched"];
  return [];
}

export function settingSearchAliases(key) {
  const item = SETTING_BY_KEY.get(key);
  return item ? `${item.label || ""} ${item.description || ""} ${item.terms || ""} ${item.configKey || ""} ${TECHNICAL_SEARCH_ALIASES[key] || ""}`.toLowerCase() : "";
}

export function settingEffectMarkup(panel, key, value, disabled = false) {
  return settingEffectBadges(key, value, {disabled}).map((text) => `<span class="eoc-effect-badge">${panel._e(text)}</span>`).join("");
}

// Input changes update only the affected effect region, without scanning the page.
export function refreshSettingEffects(panel, control) {
  const key = control?.dataset?.config || control?.dataset?.memoryConfig;
  if (!["memory_retrieval_mode", "local_intents_enabled"].includes(key)) return;
  const region = control.closest?.("[data-field]")?.querySelector("[data-setting-effects]");
  if (region) region.innerHTML = settingEffectMarkup(panel, key, controlValue(control), Boolean(control.disabled));
}

export function controlValue(control) {
  if (!control) return undefined;
  if (control.type === "checkbox" || control.dataset?.type === "boolean") return Boolean(control.checked);
  return control.value;
}
