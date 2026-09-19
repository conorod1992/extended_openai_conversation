import {SETTINGS_INDEX} from "./frontend-navigation.js";

const EXTRA_CONFIG_OWNERS = Object.freeze({
  functions: ["capabilities", "functions"],
  function_groups: ["capabilities", "functions"],
});

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

const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);

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

export function dirtyConfigurationKeys(panel) {
  if (!panel?._configData?.config || !panel?._draft || panel._draftAgentId !== panel._agentId) return new Set();
  if (panel._eocDirtyConfigKeys instanceof Set) {
    return new Set(panel._eocDirtyConfigKeys);
  }
  // Compatibility fallback for callers that do not install the management state
  // layer. Normal management-panel edits use the authoritative changed-key set.
  const baseline = panel._configData.config;
  const draft = panel._draft;
  const keys = new Set([...Object.keys(baseline), ...Object.keys(draft)]);
  const changed = new Set([...keys].filter((key) => !same(baseline[key], draft[key])));
  if (panel._draftTitle !== panel._configData.title) changed.add("__title");
  return changed;
}

export function configurationDestinations(panel) {
  const destinations = new Set();
  const unknown = [];
  for (const key of dirtyConfigurationKeys(panel)) {
    const owner = ownerForKey(key);
    if (owner) destinations.add(`${owner[0]}/${owner[1]}`);
    else unknown.push(key);
  }
  if (unknown.length && panel?._configDirty && panel?._page && panel?._subsection) {
    destinations.add(`${panel._page}/${panel._subsection}`);
  }
  return destinations;
}

export function dirtyConfigurationDestinations(panel) {
  return new Set([...configurationDestinations(panel), ...(panel?._unsavedState?.destinations() || [])]);
}

function controlValue(control) {
  if (!control) return undefined;
  if (control.type === "checkbox" || control.dataset?.type === "boolean") return Boolean(control.checked);
  return control.value;
}


function ensureStyles(panel) {
  const root = panel.shadowRoot;
  if (!root || root.querySelector("style[data-eoc-configuration-clarity]")) return;
  const style = document.createElement("style");
  style.dataset.eocConfigurationClarity = "";
  style.textContent = `
    .agent-picker.eoc-agent-context{padding:10px 12px;border:1px solid color-mix(in srgb,var(--primary-color) 35%,var(--divider-color));border-radius:12px;background:color-mix(in srgb,var(--primary-color) 5%,var(--card-background-color));box-shadow:0 1px 3px rgba(0,0,0,.06);min-width:min(330px,100%)}
    .agent-picker.eoc-agent-context>span{font-size:11px;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:var(--secondary-text-color)}
    .agent-picker.eoc-agent-context select{font-weight:700;font-size:15px}
    .agent-picker.eoc-agent-context small{font-weight:500}
    .eoc-effect-badge{display:inline-flex;align-items:center;min-height:20px;padding:2px 7px;border-radius:999px;background:var(--secondary-background-color);border:1px solid var(--divider-color);color:var(--secondary-text-color);font-size:11px;font-weight:650;line-height:1.25;white-space:nowrap}
    .setting-label-row{flex-wrap:wrap}
    .top-nav button.eoc-has-unsaved::after,.subsection-nav button.eoc-has-unsaved::after{content:"";display:inline-block;width:7px;height:7px;margin-left:7px;border-radius:50%;background:var(--primary-color);vertical-align:middle}
    .agent-picker.eoc-agent-context.eoc-has-unsaved{border-color:color-mix(in srgb,var(--primary-color) 60%,var(--divider-color))}
    @media (max-width:800px){.agent-picker.eoc-agent-context{width:100%;box-sizing:border-box;min-width:0}}
  `;
  root.append(style);
}

function setText(node, text) {
  if (node && node.textContent !== text) node.textContent = text;
}

function enhanceAgentContext(panel, destinations) {
  const root = panel.shadowRoot;
  const picker = root?.querySelector(".agent-picker");
  const agent = panel._selectedAgent?.();
  if (!picker) return;
  picker.classList.add("eoc-agent-context");
  picker.classList.toggle("eoc-has-unsaved", destinations.size > 0);
  const heading = picker.querySelector(":scope > span");
  setText(heading, "Editing assistant");
  const select = picker.querySelector("#agent");
  if (select) select.setAttribute("aria-label", "Editing assistant");
  const draftActive = panel._draft && panel._draftAgentId === panel._agentId;
  const detail = picker.querySelector("small");
  if (detail && agent) {
    const model = draftActive ? panel._draft.chat_model || agent.model : agent.model;
    setText(detail, `${agent.provider} · ${model}${destinations.size ? " · Unsaved changes" : ""}`);
  }
}

function setDirtyMarker(element, dirty, label) {
  if (!element) return;
  element.classList?.toggle?.("eoc-has-unsaved", dirty);
  if (dirty) element.setAttribute?.("aria-label", `${label}, has unsaved changes`);
  else element.removeAttribute?.("aria-label");
}

function optionBaseLabel(option) {
  if (!option) return "";
  if (!option.dataset.eocBaseLabel) option.dataset.eocBaseLabel = option.textContent.replace(/\s+•$/, "");
  return option.dataset.eocBaseLabel;
}

function enhanceDirtyNavigation(panel, destinations) {
  const root = panel.shadowRoot;
  if (!root) return;
  const dirtyPages = new Set([...destinations].map((item) => item.split("/", 1)[0]));
  root.querySelectorAll(".top-nav button[data-page]").forEach((button) => {
    const label = button.textContent.replace(/\s+•$/, "").trim();
    setDirtyMarker(button, dirtyPages.has(button.dataset.page), label);
  });
  root.querySelectorAll(".subsection-nav button[data-subsection]").forEach((button) => {
    const label = button.textContent.replace(/\s+•$/, "").trim();
    setDirtyMarker(button, destinations.has(`${panel._page}/${button.dataset.subsection}`), label);
  });
  const topMobile = root.querySelector("#top-section-mobile");
  topMobile?.querySelectorAll("option").forEach((option) => {
    const base = optionBaseLabel(option);
    setText(option, dirtyPages.has(option.value) ? `${base} •` : base);
  });
  const local = root.querySelector("#local-section");
  local?.querySelectorAll("option").forEach((option) => {
    const base = optionBaseLabel(option);
    setText(option, destinations.has(`${panel._page}/${option.value}`) ? `${base} •` : base);
  });
}

export function enhanceConfigurationClarity(panel) {
  if (!panel.shadowRoot) return;
  ensureStyles(panel);
  const destinations = dirtyConfigurationDestinations(panel);
  enhanceAgentContext(panel, destinations);
  enhanceDirtyNavigation(panel, destinations);
}

export function bindConfigurationClarity(panel) {
  const root = panel.shadowRoot;
  if (!root || root.__eocClarityInteractionBound) return;
  root.__eocClarityInteractionBound = true;
  // State safety dispatches this after updating authoritative dirty state.
  root.addEventListener("eoc-config-dirty-changed", () => enhanceConfigurationClarity(panel));
  for (const type of ["input", "change", "value-changed"]) {
    root.addEventListener(type, (event) => {
      refreshSettingEffects(panel, event.target);
      enhanceConfigurationClarity(panel);
    });
  }
}
