import {same} from "./unsaved-state.js";
import {reconcileSavedConfiguration} from "./configuration-save-dom.js";
import {captureConfigurationSubmission, configurationSubmissionIsCurrent, completeConfigurationAcknowledgement, reconcileConfigurationAcknowledgement} from "./configuration-acknowledgement.js";
const clone = (value) => JSON.parse(JSON.stringify(value));

function fieldErrorKey(key) {
  return key === "title" ? "__title" : key;
}

function showErrors(panel, errors = {}) {
  const root = panel.shadowRoot;
  root.querySelectorAll(".field-error").forEach((item) => { item.textContent = ""; });
  Object.entries(errors).forEach(([key, message]) => {
    const mappedKey = fieldErrorKey(key);
    const escaped = CSS.escape(mappedKey);
    const fallback = CSS.escape(fieldErrorKey(key.split("[")[0]));
    const target = root.querySelector(`[data-error="${escaped}"]`) || root.querySelector(`[data-error="${fallback}"]`);
    if (target) target.textContent = message;
  });
}

function normalizeGuestModeTimestamp(value) {
  if (typeof value !== "string" || !value) return value;
  if (/(?:z|[+-]\d{2}:\d{2})$/i.test(value)) return value;
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toISOString();
}

function validatedImportMatches(validatedDocument, currentDocument) {
  return typeof validatedDocument === "string" && validatedDocument === currentDocument;
}

function setControlPending(panel, control, pending) {
  if (!control) return;
  if (control.tagName === "BUTTON" && typeof panel._setSaving === "function") {
    panel._setSaving(control, pending);
    return;
  }
  control.disabled = pending;
}

async function runFrontendMutation(panel, control, label, operation) {
  if (!control || control.dataset?.eocMutationPending === "true") return false;
  control.dataset.eocMutationPending = "true";
  setControlPending(panel, control, true);
  try {
    await operation();
    return true;
  } catch (err) {
    panel._toast(`Unable to ${label}: ${err.message || String(err)}`, true);
    return false;
  } finally {
    delete control.dataset.eocMutationPending;
    setControlPending(panel, control, false);
  }
}

export async function saveConfigurationAcrossRestart(panel, payload, submitted, submittedTitle) {
  const baseline = panel._configData;
  const agentId = panel._agentId;
  try {
    return await panel._call("configuration", "save", payload);
  } catch (error) {
    if (!String(error?.message || error).includes("Configuration changed in another tab") ||
        !baseline?.server_epoch || panel._configData !== baseline) throw error;
    const latest = await panel._call("configuration", "get");
    if (latest?.server_epoch === baseline.server_epoch || !latest?.server_epoch ||
        latest.title !== baseline.title || !same(latest.config, baseline.config) ||
        panel._agentId !== agentId || !same(panel._draft, submitted) ||
        panel._draftTitle !== submittedTitle) throw error;
    // A new HA process lost the in-memory revision lineage. The persisted
    // baseline is unchanged, so the draft can safely use its fresh revision.
    return panel._call("configuration", "save", {...payload, revision: latest.revision});
  }
}

async function saveConfiguration(panel, button) {
  if (!panel._draft || !panel._selectedAgent?.() || panel._configurationSaving) return;
  if (panel._eocAcknowledgedConfiguration && panel._configDataStale) {
    const owner = captureConfigurationSubmission(panel), user = panel.hass?.user?.id, visit = panel._loadToken;
    try { await panel._loadConfigDraft({preserveResult:true}); }
    catch (error) {
      if (configurationSubmissionIsCurrent(panel, owner) && panel.hass?.user?.id === user && panel._loadToken === visit) panel._toast(`Changes already saved; refresh configuration before saving again: ${error.message || String(error)}`, true);
      return;
    }
    if (configurationSubmissionIsCurrent(panel, owner) && panel.hass?.user?.id === user && panel._loadToken === visit && !panel._configDataStale) {
      panel._render();
      panel._toast("Configuration refreshed. Review your changes before saving.");
    }
    return;
  }
  const submission = captureConfigurationSubmission(panel);
  const submitted = submission.draft, submittedTitle = submission.title;
  const owner = {...submission,
    view:panel._viewKey?.(), main:panel.shadowRoot.querySelector("main"),
    route:panel._eocRenderedRoute, markup:panel._eocMainMarkup, model:submitted.chat_model,
    voiceMappings:clone(submitted.voice_device_mappings || {}), voicePolicy:submitted.voice_scope_policy,
    voiceDefaultActive:submitted.voice_scope_policy === "default_user" || (submitted.voice_scope_policy === "device_mapping" && submitted.voice_unmapped_policy === "default_user")};
  panel._syncConfigDirty?.();
  const dirty = panel._eocDirtyConfigKeys || new Set();
  const changed = Object.fromEntries([...dirty].filter(key => key !== "__title" && Object.hasOwn(submitted, key)).map(key => [key, submitted[key]]));
  submission.fields = Object.keys(changed);
  submission.writesTitle = dirty.has("__title");
  panel._eocConfigurationSubmission = submission;
  panel._configurationSaving = true;
  panel._setSaving(button, true);
  try {
    const result = await saveConfigurationAcrossRestart(panel, {
      config: changed,
      ...(dirty.has("__title") ? {title: submittedTitle} : {}),
      revision: submission.revision,
    }, submitted, submittedTitle);
    if (!configurationSubmissionIsCurrent(panel, submission)) return;
    showErrors(panel, result.errors || {});
    if (!result.valid) {
      panel._toast("Fix the highlighted configuration errors", true);
      return;
    }

    await completeConfigurationAcknowledgement(panel, result, submission, {
      message:"Changes saved",
      refresh:() => {
        if (panel._viewKey?.() === submission.view) panel._result = panel._configData;
        reconcileSavedConfiguration(panel, owner);
        panel._render();
      },
    });
  } catch (err) {
    if (configurationSubmissionIsCurrent(panel, submission)) panel._toast(`Unable to save configuration: ${err.message || String(err)}`, true);
  } finally {
    if (panel._eocConfigurationSubmission === submission) panel._eocConfigurationSubmission = null;
    if (panel.isConnected !== false && !panel._configDataStale) panel._eocAcknowledgedConfiguration = null;
    panel._configurationSaving = false;
    panel._setSaving(panel.shadowRoot.querySelector("#save-config") || button, false);
    const discard = panel.shadowRoot.querySelector("#revert-config");
    if (discard) discard.disabled = false;
  }
}

export function bindSingleRequestSave(panel) {
  const root = panel.shadowRoot;
  if (root.__eocSingleRequestSaveBound) return;
  root.__eocSingleRequestSaveBound = true;
  root.addEventListener("click", (event) => {
    if (event.target?.closest?.("#revert-config") && panel._configurationSaving) { event.preventDefault(); event.stopImmediatePropagation(); return; }
    const button = event.target?.closest?.("#save-config");
    if (!button) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    const scope = panel._unsavedState?.scopes.get("configuration");
    void (scope ? scope.save() : saveConfiguration(panel, button));
  }, true);
}

export function bindFrontendCorrectness(panel) {
  const root = panel.shadowRoot;
  if (root.__eocFrontendCorrectnessBound) return;
  root.__eocFrontendCorrectnessBound = true;

  root.addEventListener("input", (event) => {
    const input = event.target;
    if (input?.id !== "import-document") return;
    const hadPreview = typeof panel._importDocument === "string";
    panel._importDocument = null;
    const apply = root.querySelector("#import-apply");
    if (apply) apply.disabled = true;
    if (hadPreview) {
      const summary = root.querySelector("#import-summary");
      if (summary) summary.textContent = "Document changed. Validate & preview again before importing.";
    }
  }, true);

  root.addEventListener("click", (event) => {
    const button = event.target?.closest?.("button");
    if (!button) return;

    if (button.id === "import-preview") {
      panel._importDocument = null;
      const apply = root.querySelector("#import-apply");
      if (apply) apply.disabled = true;
      return;
    }

    if (button.id === "import-apply") {
      const current = root.querySelector("#import-document")?.value ?? "";
      if (validatedImportMatches(panel._importDocument, current)) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      button.disabled = true;
      panel._toast("Validate & preview the current import document before importing it", true);
      return;
    }




  }, true);



  root.addEventListener("submit", (event) => {
    if (event.target?.id !== "rule-form" || !panel._eocRuleSavePromise) return;
    event.preventDefault();
    event.stopImmediatePropagation();
  }, true);
}


export {fieldErrorKey, normalizeGuestModeTimestamp, runFrontendMutation, validatedImportMatches, setControlPending, saveConfiguration};


export {isAgentMutation} from "./management-mutation-effects.js";

export function syncAgentPicker(panel) {
  for (const id of ["guest-now", "guest-update", "guest-disable"]) {
    const button = panel.shadowRoot?.querySelector?.(`#${id}`);
    if (button) button.disabled = Boolean(panel._guestOperation);
  }
  const picker = panel.shadowRoot?.querySelector?.("#agent");
  if (!picker) return;
  const pending = Number(panel._eocAgentMutations || 0);
  picker.disabled = pending > 0;
  if (pending > 0) picker.setAttribute("aria-busy", "true");
  else picker.removeAttribute("aria-busy");
}

// Independently saved fields must advance the shared baseline without adopting
// unrelated persisted values over the assistant's pending settings or title.
export function synchronizePersistedConfigurationFields(panel, saved, fields, submission = captureConfigurationSubmission(panel)) {
  return reconcileConfigurationAcknowledgement(panel, saved, submission, {fields});
}
