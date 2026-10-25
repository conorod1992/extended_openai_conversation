import {bindConfigurationInputs} from "./configuration-inputs.js";
import {applyTargetedConfigDirty} from "./management-state-safety.js";
import {bindConfiguration as bindBaseConfiguration} from "./agent-config-editor-base.js";
import {lookupModelData} from "./model-catalog.js";
import {refreshApiModePresentation, refreshReasoningEffortPresentation} from "./agent-config-model-presentation.js";

function currentConfig(panel) {
  return panel?._draft || panel?._result?.config || {};
}

function currentCatalogData(panel, model) {
  const data = panel?._modelCatalogData;
  return data?.requested_model === String(model || "") ? data : null;
}

const protectedModelElement = (element) => element?.matches?.("input,textarea,select,button,a[href],summary,[tabindex],[contenteditable='true']");
const protectedModelFocus = (root) => protectedModelElement(root?.activeElement);

function modelPresentationState(panel) {
  const root = panel.shadowRoot;
  if (!root?.addEventListener) return;
  const state = root.__eocModelPresentation ||= {pending:null, bound:false, pointerActive:false, scheduled:false};
  if (!state.bound) {
    // One listener belongs to this shadow root, rather than each lookup or
    // document. A full configuration bind clears already-presented work.
    state.bound = true;
    const flush = () => {
      state.scheduled = false;
      const pending = state.pending;
      if (!pending) return;
      if (!pending.current()) { state.pending = null; return; }
      if (state.pointerActive || protectedModelFocus(root)) return;
      state.pending = null;
      panel._render();
    };
    const schedule = () => {
      if (!state.pending || state.scheduled) return;
      state.scheduled = true;
      if (typeof requestAnimationFrame === "function") requestAnimationFrame(flush);
      else queueMicrotask(flush);
    };
    root.addEventListener("focusout", (event) => {
      // activeElement can be null until native focusin runs. Rendering in a
      // focusout microtask would remove the next Tab target or clicked button.
      if (!protectedModelElement(event.relatedTarget)) schedule();
    });
    root.addEventListener("pointerdown", () => { state.pointerActive = true; }, true);
    const releasePointer = () => { state.pointerActive = false; schedule(); };
    root.addEventListener("pointerup", releasePointer, true);
    root.addEventListener("pointercancel", releasePointer, true);
    root.addEventListener("pointerout", (event) => {
      if (!root.contains?.(event.relatedTarget)) releasePointer();
    }, true);
    root.addEventListener("keydown", () => { state.pointerActive = false; }, true);
  }
  return state;
}

function deferModelRender(panel, isCurrent) {
  const state = modelPresentationState(panel);
  if (!state) return;
  const draft = panel._draft, user = panel.hass?.user?.id;
  const entry = panel._selectedAgent?.()?.entry_id, agent = panel._agentId;
  const view = panel._viewKey?.(), visit = panel._loadToken;
  state.pending = {current:() => panel.isConnected !== false && isCurrent()
    && panel._draft === draft && panel.hass?.user?.id === user
    && panel._agentId === agent && panel._selectedAgent?.()?.entry_id === entry
    && panel._viewKey?.() === view && panel._loadToken === visit};
}

async function ensureCatalogData(panel) {
  const config = currentConfig(panel);
  const model = String(config.azure_model || config.chat_model || "");
  if (currentCatalogData(panel, model)) { refreshApiModePresentation(panel); return; }
  const searchTarget = panel._pendingSettingFocus;
  const agent = panel._agentId;
  const view = panel._viewKey?.();
  const loadToken = panel._loadToken;
  const current = () => panel._agentId === agent && panel._viewKey?.() === view
    && panel._loadToken === loadToken
    && String(currentConfig(panel).azure_model || currentConfig(panel).chat_model || "") === model;
  try {
    await lookupModelData(panel, model, "lookup", current);
    if (!current()) return;
    refreshApiModePresentation(panel);
    // Keep active controls intact, then publish the remaining derived fields
    // after focus actually leaves editing instead of leaving them stale.
    if (protectedModelFocus(panel.shadowRoot)) { deferModelRender(panel, current); return; }
    if (searchTarget) panel._configRestoreFocus = `#${CSS.escape(searchTarget)}`;
    panel._render();
  } catch (err) {
    panel._toast?.(`Unable to inspect model capabilities: ${err.message || String(err)}`, true);
  }
}

function applyModelDefaults(panel, data) {
  const reasoning = data?.model_metadata?.reasoning || {};
  const efforts = Array.isArray(reasoning.efforts) ? reasoning.efforts : [];
  if (!reasoning.supported || !efforts.length) {
    delete panel._draft.reasoning_effort;
    return;
  }
  const recommended = data?.model_metadata?.recommended_profile?.reasoning_effort;
  panel._draft.reasoning_effort = efforts.includes(recommended) ? recommended : efforts[0];
}

function createModelIntent(panel, field, model) {
  const agent = panel._agentId, entry = panel._selectedAgent?.()?.entry_id, user = panel.hass?.user?.id;
  const capabilityModel = String(panel._draft.azure_model || panel._draft.chat_model || "");
  const owns = target => target._agentId === agent && target._selectedAgent?.()?.entry_id === entry && target.hass?.user?.id === user;
  const matchesModel = config => config?.[field] === model && String(config?.azure_model || config?.chat_model || "") === capabilityModel;
  const sameModel = (left, right) => left?.chat_model === right?.chat_model
    && String(left?.azure_model || left?.chat_model || "") === String(right?.azure_model || right?.chat_model || "");
  let derived;
  const intent = {
    reasoningDefaultRevision:0,
    recordReasoningDefault(target) {
      if (!owns(target) || target._eocModelIntent !== intent || !matchesModel(target._draft)) return;
      derived = {
        revision:++intent.reasoningDefaultRevision,
        choiceRevision:target._eocReasoningChoiceRevision || 0,
        present:Object.hasOwn(target._draft, "reasoning_effort"),
        value:target._draft.reasoning_effort,
      };
    },
    acknowledgeReasoning(target, savedConfig, submission, comparisonConfig = savedConfig) {
      if (!derived || !owns(target) || target._draftAgentId !== agent || target._eocModelIntent !== intent
          || submission.modelIntent !== intent || (!matchesModel(target._draft) && !sameModel(target._draft, comparisonConfig))
          || !matchesModel(savedConfig) || !matchesModel(submission.draft) || !sameModel(savedConfig, submission.draft)
          || derived.revision <= (submission.modelDefaultsRevision || 0)
          || derived.choiceRevision !== (submission.reasoningChoiceRevision || 0)
          || (target._eocReasoningChoiceRevision || 0) !== (submission.reasoningChoiceRevision || 0)
          || Object.hasOwn(target._draft, "reasoning_effort") !== derived.present
          || (derived.present && target._draft.reasoning_effort !== derived.value)) return false;
      // This default belongs to the model intent included in this Save, not a
      // later authored edit. Restore its acknowledged value before draft merge.
      if (Object.hasOwn(comparisonConfig, "reasoning_effort")) target._draft.reasoning_effort = comparisonConfig.reasoning_effort;
      else delete target._draft.reasoning_effort;
      refreshReasoningEffortPresentation(target);
      return true;
    },
  };
  return intent;
}

function ownModelPreparation(panel, field, model, token, preserveReasoning, intent) {
  const agent = panel._agentId, entry = panel._selectedAgent?.()?.entry_id, user = panel.hass?.user?.id;
  const capabilityModel = String(panel._draft.azure_model || panel._draft.chat_model || "");
  const owns = target => target._agentId === agent && target._selectedAgent?.()?.entry_id === entry && target.hass?.user?.id === user;
  const preparation = {
    draft:panel._draft, reasoningRevision:panel._eocReasoningChoiceRevision || 0,
    defaultsPending:true, running:true, failed:false,
    intentCurrent:() => owns(panel)
      && panel._eocModelChangeToken === token && panel._eocModelIntent === intent && panel._draft === preparation.draft
      && panel._draft?.[field] === model
      && String(panel._draft?.azure_model || panel._draft?.chat_model || "") === capabilityModel,
    current:() => panel.isConnected !== false && preparation.intentCurrent(),
    rebindDraft(previous, next, {savedConfig} = {}) {
      if (panel._eocPendingModelPreparation === preparation && preparation.intentCurrent() && preparation.draft === previous && next?.[field] === model
          && String(next.azure_model || next.chat_model || "") === capabilityModel) {
        if (savedConfig && Object.hasOwn(savedConfig, "chat_model") && Object.hasOwn(savedConfig, field)
            && savedConfig[field] === model
            && String(savedConfig.azure_model || savedConfig.chat_model || "") === capabilityModel) {
          // An authoritative read already contains this model choice. Its
          // accepted settings must not be rewritten by a delayed default.
          panel._eocPendingModelPreparation = null;
          panel._eocModelChangeToken++;
          return;
        }
        preparation.draft = next;
      }
    },
    transferTo(destination) {
      if (panel._eocPendingModelPreparation !== preparation || !preparation.intentCurrent() || !owns(destination) || destination.isConnected === false
          || destination._draft?.[field] !== model
          || String(destination._draft?.azure_model || destination._draft?.chat_model || "") !== capabilityModel
          || (destination !== panel && destination._eocPendingModelPreparation)) return false;
      const keepReasoning = preserveReasoning || (panel._eocReasoningChoiceRevision || 0) !== preparation.reasoningRevision;
      panel._eocPendingModelPreparation = null;
      panel._eocModelChangeToken++;
      destination._eocModelIntent = intent;
      // Defaults already in the transferred draft need no second application.
      // Otherwise restart against the destination's own current catalogue.
      if (preparation.defaultsPending) void changeConfigurationModel(destination, {dataset:{config:field}, value:model}, {preserveReasoning:keepReasoning, intent});
      return true;
    },
    resumeIfIdle({reconnect = false, retry = false} = {}) {
      if (preparation.running) return;
      if (!preparation.intentCurrent()) {
        if (panel._eocPendingModelPreparation === preparation) panel._eocPendingModelPreparation = null;
        return;
      }
      if (preparation.defaultsPending && (!preparation.failed || reconnect || retry) && panel.isConnected !== false) preparation.transferTo(panel);
    },
  };
  panel._eocPendingModelPreparation = preparation;
  return preparation;
}

export function bindConfiguration(panel) {
  if (panel.shadowRoot?.__eocModelPresentation) panel.shadowRoot.__eocModelPresentation.pending = null;
  const result = bindBaseConfiguration(panel);
  const root = panel?.shadowRoot;
  const modelInput = root?.querySelector('[data-config="chat_model"]');
  const reasoning = root?.querySelector('[data-config="reasoning_effort"]');
  const hasModelAwareControls = Boolean(
    modelInput
    || reasoning
    || root?.querySelector('[data-config="temperature"],[data-config="top_p"],[data-config="api_mode"],[data-config="max_tokens"],[data-config="service_tier"]')
  );
  if (hasModelAwareControls) {
    // Observe pointer activation before asynchronous capabilities arrive.
    modelPresentationState(panel);
    void ensureCatalogData(panel);
    panel._eocPendingModelPreparation?.resumeIfIdle?.();
  }

  bindConfigurationInputs(panel, {
    modelChanged: (control) => changeConfigurationModel(panel, control),
    reasoningChanged: () => {
      panel._configRestoreFocus = '[data-config="reasoning_effort"]';
      panel._render();
    },
    capabilityChanged: (control) => {
      panel._configRestoreFocus = `[data-config="${control.dataset.config}"]`;
      panel._render();
    },
  });
  return result;
}

export async function changeConfigurationModel(panel, control, {preserveReasoning = false, intent = null} = {}) {
  const field = control.dataset?.config || "chat_model";
  const model = control.value;
  const draft = panel._draft;
  const token = (panel._eocModelChangeToken || 0) + 1;
  panel._eocModelChangeToken = token;
  // Retries and handoffs start a new async operation for the same authored
  // model choice. Only another user model change creates a different intent.
  panel._eocModelIntent = intent || createModelIntent(panel, field, model);
  const preparation = ownModelPreparation(panel, field, model, token, preserveReasoning, panel._eocModelIntent);
  const current = preparation.current;
  try {
    const data = await lookupModelData(panel, String(draft.azure_model || draft.chat_model || ""), "lookup", current);
    if (!current()) return;
    // A later explicit effort choice (including a reset) belongs to the user,
    // even when navigation initiated another lookup for this same model.
    if (!preserveReasoning && (panel._eocReasoningChoiceRevision || 0) === preparation.reasoningRevision) {
      applyModelDefaults(panel, data);
      panel._eocModelIntent.recordReasoningDefault?.(panel);
      refreshReasoningEffortPresentation(panel);
    }
    preparation.defaultsPending = false;
    refreshApiModePresentation(panel);
    // Capability presentation still needs a refresh if request validation
    // rejects another draft field. The catalogue itself already applies.
    if (protectedModelFocus(panel.shadowRoot) && panel.shadowRoot.activeElement !== control) deferModelRender(panel, current);
    applyTargetedConfigDirty(panel, [field, "reasoning_effort"], control, false);
    const validation = await panel._call("configuration", "validate", {config: panel._draft});
    if (!current() || !validation.valid) return;
    if (panel._result) panel._result.model_capabilities = validation.model_capabilities;
    refreshApiModePresentation(panel);
    // A model change finishes after blur. Do not replace the next control or
    // move focus back while the user is already editing another field.
    const focused = panel.shadowRoot?.activeElement;
    if (focused && focused !== control) { deferModelRender(panel, current); return; }
    if (focused === control) panel._configRestoreFocus = `[data-config="${field}"]`;
    panel._render();
  } catch (err) {
    preparation.failed = true;
    if (current()) panel._toast?.(`Unable to inspect model options: ${err.message || String(err)}`, true);
  } finally {
    preparation.running = false;
    // A disconnected owner keeps only resumable intent. Completion must not
    // dirty its detached draft or prevent a later owner from preparing it.
    if (panel._eocPendingModelPreparation === preparation
        && (!preparation.intentCurrent() || !preparation.defaultsPending
          || (panel.isConnected !== false && !preparation.failed))) panel._eocPendingModelPreparation = null;
  }
}
