import {bindConfigurationInputs} from "./configuration-inputs.js";
import {applyTargetedConfigDirty} from "./management-state-safety.js";
import {bindConfiguration as bindBaseConfiguration} from "./agent-config-editor-base.js";
import {lookupModelData} from "./model-catalog.js";
import {refreshApiModePresentation} from "./agent-config-model-presentation.js";

function currentConfig(panel) {
  return panel?._draft || panel?._result?.config || {};
}

function currentCatalogData(panel, model) {
  const data = panel?._modelCatalogData;
  return data?.requested_model === String(model || "") ? data : null;
}

const protectedModelFocus = (root) => root?.activeElement?.matches?.("input,textarea,select,button,a[href],summary,[tabindex],[contenteditable='true']");

function deferModelRender(panel, isCurrent) {
  const root = panel.shadowRoot;
  if (!root?.addEventListener) return;
  const state = root.__eocModelPresentation ||= {pending:null, bound:false};
  if (!state.bound) {
    // One listener belongs to this shadow root, rather than each lookup or
    // document. A full configuration bind clears already-presented work.
    state.bound = true;
    root.addEventListener("focusout", () => queueMicrotask(() => {
      const pending = state.pending;
      if (!pending) return;
      if (!pending.current()) { state.pending = null; return; }
      if (protectedModelFocus(root)) return;
      state.pending = null;
      panel._render();
    }));
  }
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
  if (hasModelAwareControls) void ensureCatalogData(panel);

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

export async function changeConfigurationModel(panel, control) {
  const field = control.dataset?.config || "chat_model";
  const model = control.value;
  const agent = panel._agentId;
  const draft = panel._draft;
  const reasoningRevision = panel._eocReasoningChoiceRevision || 0;
  const token = (panel._eocModelChangeToken || 0) + 1;
  panel._eocModelChangeToken = token;
  const current = () => panel._agentId === agent && panel._draft === draft
    && panel._eocModelChangeToken === token && draft[field] === model;
  try {
    const data = await lookupModelData(panel, String(draft.azure_model || draft.chat_model || ""), "lookup", current);
    if (!current()) return;
    // A later explicit effort choice (including a reset) belongs to the user,
    // even when navigation initiated another lookup for this same model.
    if ((panel._eocReasoningChoiceRevision || 0) === reasoningRevision) applyModelDefaults(panel, data);
    refreshApiModePresentation(panel);
    // Capability presentation still needs a refresh if request validation
    // rejects another draft field. The catalogue itself already applies.
    if (protectedModelFocus(panel.shadowRoot) && panel.shadowRoot.activeElement !== control) deferModelRender(panel, current);
    applyTargetedConfigDirty(panel, [field, "reasoning_effort"], control, false);
    const validation = await panel._call("configuration", "validate", {config: panel._draft});
    if (!current() || !validation.valid) return;
    panel._result.model_capabilities = validation.model_capabilities;
    refreshApiModePresentation(panel);
    // A model change finishes after blur. Do not replace the next control or
    // move focus back while the user is already editing another field.
    const focused = panel.shadowRoot?.activeElement;
    if (focused && focused !== control) { deferModelRender(panel, current); return; }
    if (focused === control) panel._configRestoreFocus = `[data-config="${field}"]`;
    panel._render();
  } catch (err) {
    if (current()) panel._toast?.(`Unable to inspect model options: ${err.message || String(err)}`, true);
  }
}
