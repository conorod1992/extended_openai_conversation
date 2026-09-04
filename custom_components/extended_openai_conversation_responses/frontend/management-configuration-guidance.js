import {friendlySettingLabel, settingEffectBadges} from "./management-configuration-clarity.js";

const PATCHED = Symbol.for("extended-openai.management-configuration-guidance");

const MODEL_PARAMETERS = Object.freeze([
  Object.freeze({
    key: "temperature",
    capability: "supports_temperature",
    type: "number",
    label: "Response creativity (temperature)",
    description: "Higher values make responses more varied; lower values make them more predictable.",
  }),
  Object.freeze({
    key: "top_p",
    capability: "supports_top_p",
    type: "number",
    label: "Response diversity (Top P)",
    description: "Adjusts how widely the model samples possible words. Usually leave this at its default.",
  }),
  Object.freeze({
    key: "reasoning_effort",
    capability: "supports_reasoning_effort",
    type: "select",
    label: "Reasoning effort",
    description: "Choose how much work the model spends on difficult tasks; higher settings may be slower and cost more.",
  }),
  Object.freeze({
    key: "service_tier",
    capability: "supports_service_tier",
    type: "select",
    label: "Processing tier",
    description: "Choose the provider's service tier, which can affect request priority, availability, or cost.",
  }),
]);

function activeConfig(panel) {
  if (panel?._draft && panel._draftAgentId === panel._agentId) return panel._draft;
  return panel?._result?.config || panel?._configData?.config || panel?._settingsSearchConfig?.config || {};
}

function activeCapabilities(panel) {
  return panel?._result?.model_capabilities
    || panel?._configData?.model_capabilities
    || panel?._settingsSearchConfig?.model_capabilities
    || {};
}

function activeRuntimeGuidance(panel) {
  if (panel?._configurationGuidanceAgentId === panel?._agentId) return panel._configurationGuidance || {};
  return panel?._result?.configuration_guidance || panel?._configData?.configuration_guidance || {};
}

export function dependencyGuidance(parentKey, config = {}, localHandling = {}) {
  if (parentKey === "conversation_continuity" && config.conversation_continuity === "ha_default") {
    return {
      title: "Conversation timeout is inactive",
      text: "Home Assistant is managing conversation sessions. Choose Remember by voice device or Remember by user across devices to set an Extended OpenAI inactivity timeout.",
    };
  }
  if (parentKey === "web_search" && !config.web_search) {
    return {
      title: "Web search detail is inactive",
      text: "The saved detail level has no effect while Web search is off.",
    };
  }
  if (parentKey === "archive_enabled" && !config.archive_enabled) {
    return {
      title: "Conversation-history options are inactive",
      text: "These settings are retained but have no effect while Save conversation history is off.",
    };
  }
  if (parentKey === "speech_processing_enabled" && !config.speech_processing_enabled) {
    return {
      title: "Speech cleanup options are inactive",
      text: "These settings are retained but have no effect while Speech post-processing is off.",
    };
  }
  if (parentKey === "local_intents_enabled") {
    if (localHandling.supported === false) return null;
    if (!config.local_intents_enabled) {
      return {
        title: "Local-routing options are inactive",
        text: "These choices are retained but have no effect while Extended OpenAI local handling is off.",
      };
    }
  }
  return null;
}

export function hybridMemoryGuidance(config = {}) {
  if (config.memory_retrieval_mode === "hybrid") {
    return {
      kind: "info",
      title: "Hybrid matching uses embeddings when available",
      text: "Semantic matching uses the configured embedding model. If embedding generation is unavailable or fails, memory retrieval falls back to keyword matching.",
    };
  }
  return {
    kind: "dormant",
    title: "Embedding model is inactive",
    text: "It is used only with Semantic + keyword matching (Hybrid). The saved model is retained while Keyword matching (Lexical) is selected.",
  };
}

export function apiModeConsequence(config = {}, guidance = {}) {
  if (config.api_mode !== "auto" || !guidance.effective_api_mode) return null;
  const resolved = guidance.effective_api_mode === "responses" ? "Responses API" : "Chat Completions API";
  return {
    kind: "info",
    title: "Automatic format resolved",
    text: `Auto currently resolves to ${resolved} for this model.`,
  };
}

export function webSearchConflict(config = {}, guidance = {}) {
  const status = guidance.web_search || {};
  if (!config.web_search || status.available !== false) return null;
  if (status.reason === "requires_responses") {
    return {
      kind: "warning",
      title: "Web Search cannot run with the current API format",
      text: status.message || "Web Search requires the Responses API.",
      action: {
        label: "Review API format",
        page: "assistant",
        subsection: "basics",
        target: "config-api_mode",
      },
    };
  }
  if (status.reason === "direct_openai_only") {
    return {
      kind: "warning",
      title: "Web Search is unavailable for this provider endpoint",
      text: status.message || "Hosted Web Search is available only through the direct OpenAI Responses endpoint. Azure and custom base URLs cannot use it.",
    };
  }
  return {
    kind: "warning",
    title: "Web Search is currently unavailable",
    text: status.message || "The current provider configuration cannot attach the hosted Web Search tool.",
  };
}

export function webSearchDetailConsequence(config = {}, guidance = {}) {
  if (!config.web_search || guidance.web_search?.available === false) return null;
  if (!["medium", "high"].includes(config.web_search_context)) return null;
  return {
    kind: "info",
    title: "More search detail adds context",
    text: "This level can return more supporting material than Low detail and can increase the amount of context supplied with a request.",
  };
}

export function modelParameterGuidance(key, capabilities = {}) {
  const spec = MODEL_PARAMETERS.find((item) => item.key === key);
  if (!spec || capabilities[spec.capability] !== false) return null;
  return {
    kind: "unavailable",
    title: "Not used for the selected model",
    text: "Extended OpenAI does not send this parameter for the selected model. Its saved value is retained and becomes active again if you choose a model for which the integration uses it.",
  };
}

function ensureStyles(panel) {
  const root = panel.shadowRoot;
  if (!root || root.querySelector("style[data-eoc-configuration-guidance]")) return;
  const style = document.createElement("style");
  style.dataset.eocConfigurationGuidance = "";
  style.textContent = `
    .eoc-guidance-note{display:grid;gap:3px;margin:8px 0;padding:9px 11px;border:1px solid var(--divider-color);border-radius:9px;background:var(--secondary-background-color);color:var(--secondary-text-color);font-size:12px;line-height:1.45}
    .eoc-guidance-note strong{color:var(--primary-text-color);font-size:12px}
    .eoc-guidance-note.eoc-guidance-warning{border-left:3px solid var(--warning-color,var(--error-color));background:color-mix(in srgb,var(--warning-color,var(--error-color)) 6%,var(--card-background-color))}
    .eoc-guidance-note.eoc-guidance-unavailable{border-left:3px solid var(--disabled-text-color,var(--secondary-text-color))}
    .eoc-guidance-note .eoc-guidance-actions{margin-top:4px}
    .eoc-guidance-note .eoc-guidance-route{min-height:30px;padding:4px 9px}
    .eoc-injected-model-setting{opacity:.82}
    .eoc-injected-model-setting:focus{outline:2px solid var(--primary-color);outline-offset:3px}
  `;
  root.append(style);
}

function noteElement(guidance, key = "") {
  const note = document.createElement("div");
  note.className = `eoc-guidance-note eoc-guidance-${guidance.kind || "dormant"}`;
  note.dataset.eocGuidanceGenerated = key || "true";
  const title = document.createElement("strong");
  title.textContent = guidance.title;
  const text = document.createElement("span");
  text.textContent = guidance.text;
  note.append(title, text);
  if (guidance.action) {
    const actions = document.createElement("div");
    actions.className = "eoc-guidance-actions";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary eoc-guidance-route";
    button.textContent = guidance.action.label;
    button.dataset.page = guidance.action.page;
    button.dataset.subsection = guidance.action.subsection;
    button.dataset.target = guidance.action.target || "";
    actions.append(button);
    note.append(actions);
  }
  return note;
}

function clearGeneratedGuidance(panel) {
  panel.shadowRoot?.querySelectorAll("[data-eoc-guidance-generated]").forEach((item) => item.remove());
}

function decorateDependencies(panel) {
  const root = panel.shadowRoot;
  const config = activeConfig(panel);
  const localHandling = panel._result?.local_handling || panel._configData?.local_handling || {};
  root.querySelectorAll(".dependent[data-dependent]").forEach((container) => {
    const key = container.dataset.dependent;
    if (key === "local_intents_enabled" && localHandling.supported === false) {
      const parent = root.querySelector('[data-field="local_intents_enabled"] input');
      if (parent) parent.disabled = !config.local_intents_enabled;
      container.classList.add("is-disabled");
      container.querySelectorAll("input,select,textarea,button:not(.help-button)").forEach((control) => {
        control.disabled = true;
      });
      return;
    }
    const guidance = dependencyGuidance(key, config, localHandling);
    if (guidance) container.prepend(noteElement(guidance, `dependency-${key}`));
  });
}

function decorateMemory(panel) {
  const root = panel.shadowRoot;
  const config = activeConfig(panel);
  const retrieval = root.querySelector('[data-field="memory_retrieval_mode"]');
  const embedding = root.querySelector('[data-field="memory_embedding_model"]');
  if (!retrieval && !embedding) return;

  const guidance = hybridMemoryGuidance(config);
  if (config.memory_retrieval_mode === "hybrid") {
    retrieval?.append(noteElement(guidance, "memory-hybrid-consequence"));
  } else {
    embedding?.append(noteElement(guidance, "memory-embedding-dormant"));
  }
  const description = embedding?.querySelector(":scope > small");
  if (description) {
    description.textContent = config.memory_retrieval_mode === "hybrid"
      ? "Used for semantic matching in Hybrid mode; embedding failures fall back to keyword matching."
      : "Used only with Semantic + keyword matching (Hybrid).";
  }
}

function decorateProviderGuidance(panel) {
  const root = panel.shadowRoot;
  const config = activeConfig(panel);
  const runtime = activeRuntimeGuidance(panel);

  const apiMode = root.querySelector('[data-field="api_mode"]');
  const auto = apiModeConsequence(config, runtime);
  if (apiMode && auto) apiMode.append(noteElement(auto, "api-mode-auto"));

  const webSearch = root.querySelector('[data-field="web_search"]');
  const conflict = webSearchConflict(config, runtime);
  if (webSearch && conflict) webSearch.insertAdjacentElement("afterend", noteElement(conflict, "web-search-conflict"));

  const webDetail = root.querySelector('[data-field="web_search_context"]');
  const consequence = webSearchDetailConsequence(config, runtime);
  if (webDetail && consequence) webDetail.append(noteElement(consequence, "web-search-detail"));
}

function createDisabledModelField(panel, spec, value) {
  const field = document.createElement("div");
  field.className = "setting eoc-injected-model-setting";
  field.dataset.field = spec.key;
  field.dataset.setting = "";
  field.dataset.eocInjectedModel = "";
  field.id = `config-${spec.key}`;
  field.tabIndex = -1;

  const row = document.createElement("span");
  row.className = "setting-label-row";
  const label = document.createElement("span");
  label.textContent = friendlySettingLabel(spec.key) || spec.label;
  row.append(label);
  for (const badgeText of settingEffectBadges(spec.key, value)) {
    const badge = document.createElement("span");
    badge.className = "eoc-effect-badge";
    badge.textContent = badgeText;
    row.append(badge);
  }
  field.append(row);

  if (spec.type === "select") {
    const select = document.createElement("select");
    select.disabled = true;
    const choices = panel._result?.options?.[spec.key] || panel._configData?.options?.[spec.key] || [];
    if (choices.length) {
      for (const item of choices) {
        const option = document.createElement("option");
        option.value = String(typeof item === "string" ? item : item.value);
        option.textContent = typeof item === "string" ? item : item.label;
        option.selected = String(value ?? "") === option.value;
        select.append(option);
      }
    } else {
      const option = document.createElement("option");
      option.textContent = String(value ?? "Not set");
      select.append(option);
    }
    field.append(select);
  } else {
    const input = document.createElement("input");
    input.type = "number";
    input.value = String(value ?? "");
    input.disabled = true;
    field.append(input);
  }

  const description = document.createElement("small");
  description.textContent = spec.description;
  field.append(description);
  return field;
}

function injectAndDecorateModelParameters(panel) {
  const root = panel.shadowRoot;
  const grid = root.querySelector("#config-model .form-grid");
  if (!grid) return;
  const config = activeConfig(panel);
  const capabilities = activeCapabilities(panel);
  const anchor = grid.querySelector('[data-field="shorten_tool_call_id"]');

  for (const spec of MODEL_PARAMETERS) {
    let field = grid.querySelector(`[data-field="${spec.key}"]`);
    const unsupported = capabilities[spec.capability] === false;
    if (!unsupported && field?.dataset.eocInjectedModel !== undefined) {
      field.remove();
      field = null;
    }
    if (unsupported && !field) {
      field = createDisabledModelField(panel, spec, config[spec.key]);
    }
    if (field) grid.insertBefore(field, anchor || null);
    const guidance = modelParameterGuidance(spec.key, capabilities);
    if (field && guidance) field.append(noteElement(guidance, `model-${spec.key}`));
  }
}

function decorateSearchAvailability(panel) {
  const root = panel.shadowRoot;
  const capabilities = activeCapabilities(panel);
  for (const spec of MODEL_PARAMETERS) {
    if (capabilities[spec.capability] !== false) continue;
    const current = root.querySelector(`.settings-result[data-target="config-${spec.key}"] .settings-current`);
    if (!current) continue;
    const prefix = current.textContent.includes(":") ? current.textContent.split(":", 1)[0] : "Current";
    current.textContent = `${prefix}: Not used for current model`;
  }
}

function bindGuidanceRoutes(panel) {
  panel.shadowRoot?.querySelectorAll(".eoc-guidance-route").forEach((button) => {
    if (button.dataset.eocGuidanceBound) return;
    button.dataset.eocGuidanceBound = "";
    button.addEventListener("click", async () => {
      panel._pendingSettingFocus = button.dataset.target || "";
      await panel._navigate(button.dataset.page, button.dataset.subsection);
    });
  });
}

function storeRuntimeGuidance(panel, result, agentId = panel._agentId) {
  if (!result?.configuration_guidance || panel._agentId !== agentId) return;
  panel._configurationGuidance = result.configuration_guidance;
  panel._configurationGuidanceAgentId = agentId;
  if (panel._result && typeof panel._result === "object") {
    panel._result.configuration_guidance = result.configuration_guidance;
  }
}

function enhancePanel(panel) {
  if (!panel.shadowRoot) return;
  ensureStyles(panel);
  clearGeneratedGuidance(panel);
  injectAndDecorateModelParameters(panel);
  decorateDependencies(panel);
  decorateMemory(panel);
  decorateProviderGuidance(panel);
  decorateSearchAvailability(panel);
  bindGuidanceRoutes(panel);
}

function queueEnhance(panel) {
  if (panel._eocGuidanceEnhanceQueued) return;
  panel._eocGuidanceEnhanceQueued = true;
  queueMicrotask(() => {
    panel._eocGuidanceEnhanceQueued = false;
    enhancePanel(panel);
  });
}

function configControlFromEvent(event) {
  return event.composedPath?.().find((item) => item?.dataset?.config) || null;
}

function refreshRuntimeGuidance(panel) {
  const agentId = panel._agentId;
  if (!agentId) return;
  const config = JSON.parse(JSON.stringify(activeConfig(panel)));
  void panel._call("configuration", "validate", {config})
    .catch(() => null)
    .finally(() => {
      if (panel._agentId === agentId) queueEnhance(panel);
    });
}

function bindHostEvents(panel) {
  if (panel._eocGuidanceHostBound) return;
  panel._eocGuidanceHostBound = true;
  panel.addEventListener("input", () => queueEnhance(panel), true);
  panel.addEventListener("change", (event) => {
    queueEnhance(panel);
    const control = configControlFromEvent(event);
    if (control?.dataset?.config === "api_mode") {
      queueMicrotask(() => refreshRuntimeGuidance(panel));
    }
  }, true);
}

export function installManagementConfigurationGuidance(registry = globalThis.customElements) {
  if (!registry?.whenDefined) return Promise.resolve(false);
  return registry.whenDefined("extended-openai-management-panel").then(() => {
    const constructor = registry.get("extended-openai-management-panel");
    const prototype = constructor?.prototype;
    if (!prototype || prototype[PATCHED]) return false;

    const originalCall = prototype._call;
    prototype._call = async function(...args) {
      const guidanceCall = args[0] === "configuration" && ["get", "validate", "update"].includes(args[1]);
      const agentId = this._agentId;
      const revision = guidanceCall
        ? (this._eocGuidanceCallRevision = (this._eocGuidanceCallRevision || 0) + 1)
        : null;
      const result = await originalCall.apply(this, args);
      if (guidanceCall && revision === this._eocGuidanceCallRevision && this._agentId === agentId) {
        storeRuntimeGuidance(this, result, agentId);
      }
      return result;
    };

    const originalRender = prototype._render;
    prototype._render = function(...args) {
      const result = originalRender.apply(this, args);
      bindHostEvents(this);
      enhancePanel(this);
      return result;
    };

    prototype[PATCHED] = true;
    return true;
  });
}

if (typeof document !== "undefined" && typeof customElements !== "undefined") {
  installManagementConfigurationGuidance();
}

export {MODEL_PARAMETERS};
