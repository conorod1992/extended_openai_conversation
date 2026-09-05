const STORAGE_VERSION = 1;
const STORAGE_PREFIX = `extended-openai-getting-started-v${STORAGE_VERSION}`;

const STEP_ACTIONS = {
  assistant_model: {page: "assistant", subsection: "basics", target: "config-chat_model"},
  instructions: {page: "assistant", subsection: "prompt-context", target: "prompt-editor"},
  home_assistant_access: {page: "capabilities", subsection: "home-assistant", target: ""},
  connection_test: {page: "usage-maintenance", subsection: "diagnostics", target: "test-agent"},
};

function defaultStorage() {
  try {
    return globalThis.localStorage || null;
  } catch (_) {
    return null;
  }
}

function providerLabel(value) {
  const normalized = String(value || "Provider").trim();
  if (normalized.toLowerCase() === "openai") return "OpenAI";
  if (normalized.toLowerCase() === "azure") return "Azure OpenAI";
  return normalized || "Provider";
}

function apiModeLabel(configuredMode, effectiveMode) {
  const configured = String(configuredMode || "").trim().toLowerCase();
  if (configured === "auto") return "Automatic API format";
  const mode = configured || String(effectiveMode || "").trim().toLowerCase();
  if (mode === "responses") return "Responses API";
  if (mode === "chat_completions") return "Chat Completions";
  return mode ? `${mode.replaceAll("_", " ")} API format` : "API format configured";
}

function normalizedReviewState(state = {}) {
  return {
    seen: state?.seen === true,
    dismissed: state?.dismissed === true,
    reviewed: Array.from(new Set(Array.isArray(state?.reviewed) ? state.reviewed.filter((item) => typeof item === "string") : [])),
  };
}

export function onboardingStorageKey(agent = {}) {
  const entryId = String(agent?.entry_id || "").trim();
  const subentryId = String(agent?.subentry_id || "").trim();
  return entryId && subentryId ? `${STORAGE_PREFIX}:${entryId}:${subentryId}` : null;
}

export function readOnboardingReviewState(agent = {}, storage = defaultStorage()) {
  const key = onboardingStorageKey(agent);
  if (!key || !storage?.getItem) return normalizedReviewState();
  try {
    const raw = storage.getItem(key);
    return raw ? normalizedReviewState(JSON.parse(raw)) : normalizedReviewState();
  } catch (_) {
    return normalizedReviewState();
  }
}

function writeOnboardingReviewState(agent, state, storage = defaultStorage()) {
  const key = onboardingStorageKey(agent);
  if (!key || !storage?.setItem) return false;
  try {
    storage.setItem(key, JSON.stringify(normalizedReviewState(state)));
    return true;
  } catch (_) {
    return false;
  }
}

export function markOnboardingSeen(agent, storage = defaultStorage()) {
  const current = readOnboardingReviewState(agent, storage);
  if (current.seen) return true;
  return writeOnboardingReviewState(agent, {...current, seen: true}, storage);
}

export function markOnboardingStepReviewed(agent, stepId, storage = defaultStorage()) {
  const current = readOnboardingReviewState(agent, storage);
  const reviewed = new Set(current.reviewed);
  reviewed.add(stepId);
  return writeOnboardingReviewState(agent, {...current, seen: true, reviewed: [...reviewed]}, storage);
}

export function dismissOnboarding(agent, storage = defaultStorage()) {
  const current = readOnboardingReviewState(agent, storage);
  return writeOnboardingReviewState(agent, {...current, seen: true, dismissed: true}, storage);
}

export function hasEstablishedUsage(result = {}) {
  const lifetime = result?.usage?.lifetime || {};
  return Number(lifetime.api_request_count || 0) > 0
    || Number(lifetime.conversation_count || 0) > 0
    || Boolean(result?.usage?.latest);
}

function usageStatusKnown(result = {}) {
  return !(Array.isArray(result?.load_errors)
    && result.load_errors.some((issue) => issue?.key === "usage"));
}

export function buildGettingStarted({facts = {}, agent = {}, result = {}, reviewState = {}, isAdmin = false} = {}) {
  const review = normalizedReviewState(reviewState);
  if (!isAdmin || facts?.unavailable === true || review.dismissed) return null;
  if (!review.seen && (!usageStatusKnown(result) || hasEstablishedUsage(result))) return null;

  const reviewed = new Set(review.reviewed);
  const runtime = facts.provider_runtime || {};
  const model = String(runtime.model || agent.model || "").trim();
  const provider = providerLabel(runtime.provider || agent.provider);
  const apiMode = apiModeLabel(runtime.configured_api_mode, facts.web_search?.effective_api_mode);
  const promptState = String(facts.prompt_state || "");
  const exposureCount = facts.exposed_entity_count;
  const exposureKnown = Number.isFinite(exposureCount);

  const assistantReviewed = Boolean(model);
  const instructionsReviewed = promptState === "custom"
    || (promptState === "starter" && reviewed.has("instructions"));
  const accessReviewed = (exposureKnown && Number(exposureCount) > 0)
    || reviewed.has("home_assistant_access");
  const connectionReviewed = reviewed.has("connection_test");

  const steps = [
    {
      id: "assistant_model",
      reviewed: assistantReviewed,
      state: assistantReviewed ? "ready" : "warning",
      title: "Assistant & model",
      value: model ? `${provider} · ${model} · ${apiMode}` : `${provider} · No model selected`,
      detail: assistantReviewed
        ? "The assistant has a model and provider configuration."
        : "Choose the model this assistant should normally use.",
      action: STEP_ACTIONS.assistant_model,
      actionLabel: "Review",
    },
    {
      id: "instructions",
      reviewed: instructionsReviewed,
      state: promptState === "empty" ? "warning" : instructionsReviewed ? "ready" : "pending",
      title: "Assistant instructions",
      value: promptState === "custom"
        ? "Custom instructions are in use"
        : promptState === "empty"
          ? "No instructions configured"
          : "Starter instructions are currently in use",
      detail: promptState === "empty"
        ? "Add system instructions before treating this setup step as complete."
        : promptState === "custom"
          ? "Permanent assistant instructions have been customised."
          : "The starter prompt is usable; review it once so you know where permanent instructions live.",
      action: STEP_ACTIONS.instructions,
      actionLabel: promptState === "empty" ? "Add instructions" : "Review",
    },
    {
      id: "home_assistant_access",
      reviewed: accessReviewed,
      state: !exposureKnown ? "unknown" : Number(exposureCount) > 0 ? "ready" : "warning",
      title: "Home Assistant access",
      value: !exposureKnown
        ? "Unable to determine Assist exposure"
        : Number(exposureCount) > 0
          ? `${Number(exposureCount).toLocaleString()} ${Number(exposureCount) === 1 ? "entity" : "entities"} exposed to Assist`
          : "No entities are exposed to Assist",
      detail: Number(exposureCount) > 0
        ? "Home Assistant already exposes entities that this assistant can work with."
        : "Review Assist exposure. Leaving this at zero is valid if you only want non-device conversations.",
      action: STEP_ACTIONS.home_assistant_access,
      actionLabel: "Review access",
    },
    {
      id: "connection_test",
      reviewed: connectionReviewed,
      state: connectionReviewed ? "ready" : "pending",
      title: "Test the connection",
      value: connectionReviewed ? "Diagnostics reviewed" : "Optional live test not reviewed",
      detail: "Diagnostics can send one minimal live request to the selected provider and model. Nothing runs automatically from Overview.",
      action: STEP_ACTIONS.connection_test,
      actionLabel: "Open Diagnostics",
    },
  ];

  const reviewedCount = steps.filter((step) => step.reviewed).length;
  if (reviewedCount === steps.length) return null;
  return {
    reviewed_count: reviewedCount,
    total_count: steps.length,
    remaining_count: steps.length - reviewedCount,
    steps,
  };
}

const STEP_ICONS = {
  ready: "mdi:check-circle-outline",
  pending: "mdi:circle-outline",
  warning: "mdi:alert-circle-outline",
  unknown: "mdi:help-circle-outline",
};

function ensureStyles(panel) {
  const root = panel.shadowRoot;
  if (!root || root.querySelector("style[data-eoc-getting-started]")) return;
  const style = document.createElement("style");
  style.dataset.eocGettingStarted = "";
  style.textContent = `
    .getting-started{margin:0 0 18px;padding:20px;border:1px solid color-mix(in srgb,var(--primary-color) 38%,var(--divider-color));border-left:4px solid var(--primary-color);border-radius:14px;background:color-mix(in srgb,var(--primary-color) 4%,var(--card-background-color))}
    .getting-started-heading{display:flex;align-items:flex-start;justify-content:space-between;gap:20px}.getting-started-heading h2{margin:0;font-size:21px}.getting-started-heading p{margin:5px 0 0;color:var(--secondary-text-color);line-height:1.45}
    .getting-started-progress{display:grid;gap:2px;flex:0 0 auto;min-width:150px;padding:10px 13px;border:1px solid var(--divider-color);border-radius:10px;background:var(--card-background-color)}.getting-started-progress strong{font-size:15px}.getting-started-progress span{font-size:12px;color:var(--secondary-text-color)}
    .getting-started-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin-top:15px}.getting-started-step{display:grid;grid-template-columns:auto minmax(0,1fr) auto;gap:11px;align-items:start;padding:13px;border:1px solid var(--divider-color);border-radius:10px;background:var(--card-background-color)}
    .getting-started-icon{margin-top:2px;--mdc-icon-size:21px;color:var(--secondary-text-color)}.getting-started-step-ready .getting-started-icon{color:var(--success-color,#0f9d58)}.getting-started-step-warning .getting-started-icon{color:var(--warning-color,#b26a00)}.getting-started-step-unknown .getting-started-icon{color:var(--secondary-text-color)}
    .getting-started-copy{display:grid;gap:3px;min-width:0}.getting-started-copy>span{font-size:12px;color:var(--secondary-text-color)}.getting-started-copy>strong{font-size:15px;line-height:1.35}.getting-started-copy p{margin:0;color:var(--secondary-text-color);font-size:12px;line-height:1.4}.getting-started-action{align-self:center;min-height:34px;padding:5px 10px;white-space:nowrap}
    .getting-started-footer{display:flex;align-items:center;justify-content:space-between;gap:14px;margin-top:14px}.getting-started-footer p{margin:0;color:var(--secondary-text-color);font-size:12px;line-height:1.45}.getting-started-dismiss{flex:0 0 auto}
    @media (max-width:800px){.getting-started-heading{display:grid}.getting-started-progress{min-width:0}.getting-started-grid{grid-template-columns:1fr}.getting-started-step{grid-template-columns:auto minmax(0,1fr)}.getting-started-action{grid-column:2;justify-self:start}}
    @media (max-width:600px){.getting-started-footer{align-items:stretch;flex-direction:column}.getting-started-dismiss{width:100%}}
  `;
  root.append(style);
}

function renderGettingStarted(panel, model) {
  const steps = model.steps.map((step) => {
    const state = STEP_ICONS[step.state] ? step.state : "pending";
    const action = !step.reviewed
      ? `<button type="button" class="secondary getting-started-action" data-onboarding-step="${panel._e(step.id)}" data-page="${panel._e(step.action.page)}" data-subsection="${panel._e(step.action.subsection)}" data-target="${panel._e(step.action.target || "")}">${panel._e(step.actionLabel)}</button>`
      : "";
    return `<article class="getting-started-step getting-started-step-${state}"><ha-icon class="getting-started-icon" icon="${STEP_ICONS[state]}" aria-hidden="true"></ha-icon><div class="getting-started-copy"><span>${panel._e(step.title)}</span><strong>${panel._e(step.value)}</strong><p>${panel._e(step.detail)}</p></div>${action}</article>`;
  }).join("");
  return `
    <section id="eoc-getting-started" class="getting-started" aria-label="Getting started">
      <div class="getting-started-heading">
        <div><span class="section-kicker"><ha-icon icon="mdi:map-marker-path"></ha-icon> First-time setup</span><h2>Getting started</h2><p>Review the core setup for this assistant. Optional capabilities such as Memory, Knowledge, Web Search and Function Tools are not required here.</p></div>
        <div class="getting-started-progress" role="status"><strong>${model.reviewed_count} of ${model.total_count} reviewed</strong><span>${model.remaining_count} ${model.remaining_count === 1 ? "step" : "steps"} remaining</span></div>
      </div>
      <div class="getting-started-grid">${steps}</div>
      <div class="getting-started-footer"><p>Setup & health below remains the ongoing source of truth even after this one-time checklist is dismissed or completed.</p><button type="button" class="secondary getting-started-dismiss" id="eoc-dismiss-getting-started">Dismiss</button></div>
    </section>`;
}

export function bindGettingStarted(panel) {
  const root = panel.shadowRoot;
  if (!root) return;
  root.querySelector("#eoc-getting-started")?.remove();
  const agent = panel._selectedAgent?.();
  const result = panel._result || {};
  const reviewState = readOnboardingReviewState(agent);
  const model = buildGettingStarted({
    facts: result.setup_health || {},
    agent,
    result,
    reviewState,
    isAdmin: panel._data?.is_admin === true,
  });
  if (!model) return;
  const intro = root.querySelector(".page-intro");
  if (!intro) return;
  ensureStyles(panel);
  markOnboardingSeen(agent);
  const host = document.createElement("div");
  host.innerHTML = renderGettingStarted(panel, model).trim();
  const card = host.firstElementChild;
  if (!card) return;
  intro.insertAdjacentElement("afterend", card);

  card.querySelectorAll("[data-onboarding-step]").forEach((button) => button.addEventListener("click", async () => {
    const stepId = button.dataset.onboardingStep || "";
    if (stepId) markOnboardingStepReviewed(agent, stepId);
    panel._pendingSettingFocus = button.dataset.target || "";
    await panel._navigate(button.dataset.page, button.dataset.subsection || null);
  }));
  card.querySelector("#eoc-dismiss-getting-started")?.addEventListener("click", () => {
    dismissOnboarding(agent);
    card.remove();
  });
}
