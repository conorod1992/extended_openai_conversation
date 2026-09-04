import {SETTINGS_INDEX} from "./frontend-navigation.js";
import {SETTINGS_SEARCH_PROJECTION} from "./management-navigation-search.js";

const PATCHED = Symbol.for("extended-openai.management-configuration-clarity");

const EXTRA_CONFIG_OWNERS = Object.freeze({
  functions: ["capabilities", "functions"],
  function_groups: ["capabilities", "functions"],
});

const FRIENDLY_LABEL_OVERRIDES = Object.freeze({
  memory_retrieval_mode: "Relevance matching",
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

const ADVANCED_KEYS = new Set([
  "temperature",
  "top_p",
  "reasoning_effort",
  "service_tier",
  "shorten_tool_call_id",
]);

const same = (left, right) => JSON.stringify(left) === JSON.stringify(right);

const SETTING_BY_KEY = new Map();
for (const item of SETTINGS_INDEX) {
  if (item.configKey && !SETTING_BY_KEY.has(item.configKey)) SETTING_BY_KEY.set(item.configKey, item);
}
for (const entry of SETTINGS_SEARCH_PROJECTION) {
  const alias = TECHNICAL_SEARCH_ALIASES[entry.item?.configKey];
  if (alias && !entry.haystack.includes(alias)) entry.haystack += ` ${alias}`;
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
  if (ADVANCED_KEYS.has(key)) return ["Advanced"];
  if (disabled) return [];
  if (key === "memory_retrieval_mode") {
    if (value === "hybrid") return ["Requires embeddings"];
    if (value === "lexical") return ["No embedding request"];
  }
  if (key === "local_intents_enabled" && value === true) return ["No AI call when matched"];
  if (key === "archive_enabled" && value === true) return ["Stores data"];
  if (key === "shared_archive_enabled" && value === true) return ["Stores shared data"];
  if (key === "memory_mode" && !["off", "disabled", ""].includes(String(value))) return ["Stores data"];
  if (key === "temporary_memory" && !["off", "disabled", ""].includes(String(value))) return ["Stores temporary data"];
  if (key === "shared_memory_mode" && !["off", "disabled", ""].includes(String(value))) return ["Stores shared data"];
  if (["current_datetime_enabled", "exposed_entities_enabled"].includes(key) && value === true) return ["Adds context"];
  return [];
}

export function dirtyConfigurationKeys(panel) {
  if (!panel?._configData?.config || !panel?._draft || panel._draftAgentId !== panel._agentId) return new Set();
  const baseline = panel._configData.config;
  const draft = panel._draft;
  const keys = new Set([...Object.keys(baseline), ...Object.keys(draft)]);
  const changed = new Set([...keys].filter((key) => !same(baseline[key], draft[key])));
  if (panel._draftTitle !== panel._configData.title) changed.add("__title");
  return changed;
}

export function dirtyConfigurationDestinations(panel) {
  const destinations = new Set();
  const unknown = [];
  for (const key of dirtyConfigurationKeys(panel)) {
    const owner = ownerForKey(key);
    if (owner) destinations.add(`${owner[0]}/${owner[1]}`);
    else unknown.push(key);
  }
  if (panel?._guestDirty) destinations.add("capabilities/guest-mode");
  if (unknown.length && panel?._configDirty && panel?._page && panel?._subsection) {
    destinations.add(`${panel._page}/${panel._subsection}`);
  }
  return destinations;
}

function controlValue(control) {
  if (!control) return undefined;
  if (control.type === "checkbox" || control.dataset?.type === "boolean") return Boolean(control.checked);
  return control.value;
}

function currentConfigurationValue(panel, key) {
  if (key === "__title") {
    if (panel._draft && panel._draftAgentId === panel._agentId) return panel._draftTitle;
    return panel._configData?.title ?? panel._settingsSearchConfig?.title;
  }
  if (panel._draft && panel._draftAgentId === panel._agentId) return panel._draft[key];
  if (panel._configData?.config) return panel._configData.config[key];
  if (panel._settingsSearchConfigAgentId === panel._agentId) return panel._settingsSearchConfig?.config?.[key];
  return undefined;
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

function enhanceAgentContext(panel, destinations) {
  const root = panel.shadowRoot;
  const picker = root?.querySelector(".agent-picker");
  const agent = panel._selectedAgent?.();
  if (!picker) return;
  picker.classList.add("eoc-agent-context");
  picker.classList.toggle("eoc-has-unsaved", destinations.size > 0);
  const heading = picker.querySelector(":scope > span");
  if (heading) heading.textContent = "Editing assistant";
  const select = picker.querySelector("#agent");
  if (select) select.setAttribute("aria-label", "Editing assistant");
  const draftActive = panel._draft && panel._draftAgentId === panel._agentId;
  const detail = picker.querySelector("small");
  if (detail && agent) {
    const model = draftActive ? panel._draft.chat_model || agent.model : agent.model;
    detail.textContent = `${agent.provider} · ${model}${destinations.size ? " · Unsaved changes" : ""}`;
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
    option.textContent = dirtyPages.has(option.value) ? `${base} •` : base;
  });
  const local = root.querySelector("#local-section");
  local?.querySelectorAll("option").forEach((option) => {
    const base = optionBaseLabel(option);
    option.textContent = destinations.has(`${panel._page}/${option.value}`) ? `${base} •` : base;
  });
}

function replaceLabelText(label, text) {
  if (!label || !text) return;
  const strong = label.querySelector(":scope > strong");
  if (strong) strong.textContent = text;
  else label.textContent = text;
}

function addEffectBadges(field, badges) {
  const row = field.querySelector(".setting-label-row");
  if (!row) return;
  row.querySelectorAll(".eoc-effect-badge").forEach((badge) => badge.remove());
  for (const text of badges) {
    const badge = document.createElement("span");
    badge.className = "eoc-effect-badge";
    badge.textContent = text;
    row.append(badge);
  }
}

function enhanceSettingField(panel, field) {
  const key = field.dataset.field;
  if (!key) return;
  const item = SETTING_BY_KEY.get(key);
  const labelText = friendlySettingLabel(key);
  const label = field.querySelector(".setting-label-row label");
  if (label && labelText) replaceLabelText(label, labelText);

  if (item) {
    const aliases = `${item.label || ""} ${item.description || ""} ${item.terms || ""} ${item.configKey || ""} ${TECHNICAL_SEARCH_ALIASES[key] || ""}`.toLowerCase();
    if (!String(field.dataset.search || "").includes(aliases)) field.dataset.search = `${field.dataset.search || ""} ${aliases}`.trim();
  }

  const control = field.querySelector(`[data-config="${CSS.escape(key)}"],[data-memory-config="${CSS.escape(key)}"]`);
  const value = controlValue(control);
  if (control?.tagName === "SELECT" && FRIENDLY_VALUE_LABELS[key]) {
    [...control.options].forEach((choice) => {
      const friendly = friendlySettingValue(key, choice.value);
      if (friendly) choice.textContent = friendly;
    });
  }
  addEffectBadges(field, settingEffectBadges(key, value, {disabled: Boolean(control?.disabled)}));
}

function enhanceSettingsSearch(panel) {
  const root = panel.shadowRoot;
  if (!root) return;
  root.querySelectorAll(".settings-result").forEach((button) => {
    const item = SETTINGS_INDEX.find((candidate) =>
      candidate.page === button.dataset.page
      && candidate.section === button.dataset.subsection
      && String(candidate.target || "") === String(button.dataset.target || "")
    );
    if (!item?.configKey) return;
    const friendlyLabel = friendlySettingLabel(item.configKey);
    const title = button.querySelector("strong");
    if (friendlyLabel && title?.textContent !== friendlyLabel) title.textContent = friendlyLabel;
    const friendlyValue = friendlySettingValue(item.configKey, currentConfigurationValue(panel, item.configKey));
    const current = button.querySelector(".settings-current");
    if (friendlyValue && current) {
      const prefix = current.textContent.includes(":") ? current.textContent.split(":", 1)[0] : "Current";
      const next = `${prefix}: ${friendlyValue}`;
      if (current.textContent !== next) current.textContent = next;
    }
  });
}

function enhanceSettingPresentation(panel) {
  const root = panel.shadowRoot;
  root?.querySelectorAll("[data-setting][data-field]").forEach((field) => enhanceSettingField(panel, field));
  enhanceSettingsSearch(panel);
}

function enhancePanel(panel) {
  if (!panel.shadowRoot) return;
  ensureStyles(panel);
  const destinations = dirtyConfigurationDestinations(panel);
  enhanceAgentContext(panel, destinations);
  enhanceDirtyNavigation(panel, destinations);
  enhanceSettingPresentation(panel);
}

function scheduleEnhance(panel) {
  if (panel._eocClarityScheduled) return;
  panel._eocClarityScheduled = true;
  queueMicrotask(() => {
    panel._eocClarityScheduled = false;
    enhancePanel(panel);
  });
}

function bindInteractionRefresh(panel) {
  if (!panel._eocClarityHostBound) {
    panel._eocClarityHostBound = true;
    panel.addEventListener("input", () => scheduleEnhance(panel), true);
    panel.addEventListener("change", () => scheduleEnhance(panel), true);
  }
  const root = panel.shadowRoot;
  if (root && !root.__eocClarityValueBound) {
    root.__eocClarityValueBound = true;
    root.addEventListener("value-changed", () => scheduleEnhance(panel));
  }
  const searchTarget = root?.querySelector("#eoc-settings-host") || root;
  if (searchTarget && panel._eocClarityObservedTarget !== searchTarget) {
    panel._eocClarityObserver?.disconnect?.();
    panel._eocClarityObserver = new MutationObserver(() => scheduleEnhance(panel));
    panel._eocClarityObserver.observe(searchTarget, {childList: true, subtree: true});
    panel._eocClarityObservedTarget = searchTarget;
  }
}

export function installManagementConfigurationClarity(registry = globalThis.customElements) {
  if (typeof document === "undefined" || !registry?.whenDefined) return Promise.resolve(false);
  return registry.whenDefined("extended-openai-management-panel").then(() => {
    const constructor = registry.get("extended-openai-management-panel");
    const prototype = constructor?.prototype;
    if (!prototype || prototype[PATCHED]) return false;

    const originalRender = prototype._render;
    prototype._render = function(...args) {
      const result = originalRender.apply(this, args);
      bindInteractionRefresh(this);
      enhancePanel(this);
      return result;
    };

    const originalDisconnected = prototype.disconnectedCallback;
    prototype.disconnectedCallback = function(...args) {
      this._eocClarityObserver?.disconnect?.();
      this._eocClarityObserver = null;
      this._eocClarityObservedTarget = null;
      return originalDisconnected?.apply(this, args);
    };

    prototype[PATCHED] = true;
    return true;
  });
}

if (typeof document !== "undefined" && typeof customElements !== "undefined") {
  void installManagementConfigurationClarity();
}
