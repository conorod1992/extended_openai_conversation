import {same} from "./unsaved-state.js";
import {reconcileSavedConfiguration} from "./configuration-save-dom.js";
import {requireMutationAcknowledgement, captureConfigurationDraftOwner, captureConfigurationOwner, captureConfigurationSubmission, configurationSubmissionIsCurrent, completeConfigurationAcknowledgement, reconcileConfigurationAcknowledgement, discardConfigurationDraft} from "./configuration-acknowledgement.js";
import {CONFIG_OWNER_BY_KEY} from "./management-config-owners.js";
import {SETTING_LOOKUP} from "./management-setting-lookup.js";
import {focusManagementSetting} from "./frontend-navigation.js";
const clone = (value) => JSON.parse(JSON.stringify(value));

function fieldErrorKey(key) {
  return key === "title" ? "__title" : key;
}

const baseErrorKey = key => fieldErrorKey(key.split(/\[|\./)[0]);

function showErrors(panel, errors = {}, submission = null) {
  panel._eocConfigurationErrors = {owner:captureConfigurationDraftOwner(panel), draft:clone(submission?.draft || panel._draft || {}), title:submission ? submission.title : panel._draftTitle, errors:{...errors}};
  refreshConfigurationErrors(panel);
}

export function refreshConfigurationErrors(panel) {
  const root = panel.shadowRoot;
  if (!root?.querySelectorAll) return;
  const state = panel._eocConfigurationErrors;
  const errors = state && panel._data?.is_admin !== false && configurationSubmissionIsCurrent(panel, state.owner) ? state.errors : {};
  for (const key of Object.keys(errors)) {
    const base = baseErrorKey(key);
    if (!same(base === "__title" ? panel._draftTitle : panel._draft?.[base], base === "__title" ? state.title : state.draft[base])) delete errors[key];
  }
  root.querySelectorAll("[data-eoc-server-error]").forEach(control => { control.removeAttribute("aria-invalid"); delete control.dataset.eocServerError; });
  root.querySelectorAll("[data-eoc-configuration-error]").forEach(item => { item.textContent = ""; delete item.dataset.eocConfigurationError; });
  Object.entries(errors).forEach(([key, message]) => {
    const target = root.querySelector(`[data-error="${CSS.escape(fieldErrorKey(key))}"]`) || root.querySelector(`[data-error="${CSS.escape(baseErrorKey(key))}"]`);
    if (target) { target.textContent = String(message); target.dataset.eocConfigurationError = ""; }
  });
  // Reuse existing field markup; connect both normal help and server errors to
  // the control even after route replacement. Do not erase native validity.
  root.querySelectorAll("[data-setting][data-field]").forEach(setting => {
    const key = setting.dataset.field;
    const control = root.getElementById?.(`config-${key}`);
    if (!control) return;
    const descriptions = new Set((control.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean));
    setting.querySelectorAll("small,.field-error").forEach((node, index) => {
      node.id ||= `config-${key}-description-${index}`;
      descriptions.add(node.id);
      if (node.matches(".field-error") && node.textContent) {
        control.setAttribute("aria-invalid", "true");
        control.dataset.eocServerError = "";
      }
    });
    if (descriptions.size) control.setAttribute("aria-describedby", [...descriptions].join(" "));
  });
  let summary = root.querySelector("#configuration-errors");
  if (!Object.keys(errors).length) { summary?.remove(); return; }
  const markup = `<strong>Some settings could not be saved</strong><ul>${Object.entries(errors).map(([key, message]) => {
    const label = SETTING_LOOKUP[baseErrorKey(key)]?.label || key;
    const text = `${label}: ${String(message)}`;
    return `<li>${CONFIG_OWNER_BY_KEY[baseErrorKey(key)] ? `<button type="button" class="secondary" data-config-error="${panel._e(key)}">${panel._e(text)}</button>` : panel._e(text)}</li>`;
  }).join("")}</ul>`;
  const host = root.querySelector("#save-config")?.closest(".save-bar") || root.querySelector("main");
  if (!host) return;
  if (!summary) {
    summary = document.createElement("div");
    summary.id = "configuration-errors";
    summary.className = "notice error";
    summary.tabIndex = -1;
    summary.setAttribute("role", "region");
    summary.setAttribute("aria-label", "Configuration errors");
    host.before(summary);
  }
  if (summary.innerHTML !== markup) summary.innerHTML = markup;
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
  const owner = captureConfigurationOwner(panel);
  try {
    return await panel._call("configuration", "save", payload);
  } catch (error) {
    if (!configurationSubmissionIsCurrent(panel, owner) || !String(error?.message || error).includes("Configuration changed in another tab") ||
        !baseline?.server_epoch || panel._configData !== baseline) throw error;
    const latest = await panel._call("configuration", "get");
    if (latest?.server_epoch === baseline.server_epoch || !latest?.server_epoch ||
        latest.title !== baseline.title || !same(latest.config, baseline.config) ||
        !configurationSubmissionIsCurrent(panel, owner) || !same(panel._draft, submitted) ||
        panel._draftTitle !== submittedTitle) throw error;
    // A new HA process lost the in-memory revision lineage. The persisted
    // baseline is unchanged, so the draft can safely use its fresh revision.
    return panel._call("configuration", "save", {...payload, revision: latest.revision});
  }
}

async function saveConfiguration(panel, button) {
  if (!panel._draft || !panel._selectedAgent?.() || panel._configurationSaving) return;
  if ((panel._eocAcknowledgedConfiguration || panel._eocRestoredConfiguration) && panel._configDataStale) {
    const owner = captureConfigurationOwner(panel), visit = panel._loadToken;
    try { await panel._loadConfigDraft({preserveResult:true}); }
    catch (error) {
      if (configurationSubmissionIsCurrent(panel, owner) && panel._loadToken === visit) panel._toast(`Changes already saved; refresh configuration before saving again: ${error.message || String(error)}`, true);
      return;
    }
    if (configurationSubmissionIsCurrent(panel, owner) && panel._loadToken === visit && !panel._configDataStale) {
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
  // Disabling Save can blur it before a network response arrives. Retain that
  // focus intent only until the next user interaction, including outside EOAI.
  let restoreErrorFocus = panel.shadowRoot.activeElement?.id === "save-config";
  const focusDocument = panel.ownerDocument;
  const relinquishErrorFocus = () => { restoreErrorFocus = false; };
  const focusEvents = restoreErrorFocus ? ["focusin", "pointerdown", "keydown"] : [];
  for (const type of focusEvents) focusDocument?.addEventListener(type, relinquishErrorFocus, true);
  panel._setSaving(button, true);
  try {
    const destructive = [["usage_request_retention_days", "request details"], ["usage_run_retention_days", "run details"]]
      .filter(([key]) => Object.hasOwn(changed, key) && Number(changed[key]) === 0 && Number(submission.baseline[key]) > 0)
      .map(([, label]) => label);
    if (destructive.length) {
      if (!await panel._confirm("Delete stored usage details?", `Saving Disabled retention deletes existing ${destructive.join(" and ")} for this assistant. Overall usage totals are kept.`, "Save and delete details")) return;
      if (!configurationSubmissionIsCurrent(panel, submission)) return;
      if (!same(panel._draft, submitted) || panel._draftTitle !== submittedTitle) {
        panel._toast("Settings changed while confirmation was open. Review your changes and save again.");
        return;
      }
    }
    const result = await saveConfigurationAcrossRestart(panel, {
      config: changed,
      ...(dirty.has("__title") ? {title: submittedTitle} : {}),
      revision: submission.revision,
    }, submitted, submittedTitle);
    if (!configurationSubmissionIsCurrent(panel, submission)) return;
    if (result?.valid === false) {
      showErrors(panel, result?.errors || {}, submission);
      const active = panel.shadowRoot.activeElement;
      if (panel._viewKey?.() === submission.view && (active?.id === "save-config"
          || restoreErrorFocus && !active && focusDocument?.hasFocus())) panel.shadowRoot.querySelector("#configuration-errors")?.focus();
      panel._toast("Some settings could not be saved. Review the configuration errors.", true);
      return;
    }

    requireMutationAcknowledgement(result?.valid === true && result?.config && typeof result.config === "object", "saving configuration");
    await completeConfigurationAcknowledgement(panel, result, submission, {
      message:"Changes saved",
      refresh:() => {
        showErrors(panel, result.errors || {});
        if (panel._viewKey?.() === submission.view) panel._result = panel._configData;
        reconcileSavedConfiguration(panel, owner);
        panel._render();
      },
    });
  } catch (err) {
    if (configurationSubmissionIsCurrent(panel, submission)) panel._toast(`Unable to save configuration: ${err.message || String(err)}`, true);
  } finally {
    for (const type of focusEvents) focusDocument?.removeEventListener(type, relinquishErrorFocus, true);
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
  root.addEventListener("eoc-config-dirty-changed", () => refreshConfigurationErrors(panel));
  root.addEventListener("click", async event => {
    const button = event.target?.closest?.("[data-config-error]");
    if (!button) return;
    const key = baseErrorKey(button.dataset.configError);
    const route = CONFIG_OWNER_BY_KEY[key];
    if (!route) return;
    const [page, section] = route.split("/");
    panel._pendingSettingFocus = `config-${key}`;
    await panel._navigate(page, section);
    if (panel._viewKey?.() === route && panel._pendingSettingFocus) {
      const control = root.getElementById(`config-${key}`);
      if (control) { panel._pendingSettingFocus = null; focusManagementSetting(control); }
    }
  });
  root.addEventListener("click", (event) => {
    if (event.target?.closest?.("#revert-config")) {
      event.preventDefault();
      event.stopImmediatePropagation();
      if (panel._configurationSaving || !panel._configData?.config) return;
      if (root.__eocConfigurationInputs) root.__eocConfigurationInputs.pendingCapabilityControl = null;
      discardConfigurationDraft(panel);
      // Save acknowledgements can insert this bar after a feature bound its
      // original controls. Delegation also covers those newly created buttons.
      panel._eocMainMarkup = null;
      panel._render();
      return;
    }
    let button = event.target?.closest?.("#save-config");
    if (!button) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (panel._configurationSaving) return;
    const inputs = root.__eocConfigurationInputs;
    const control = inputs?.pendingCapabilityControl;
    if (inputs) inputs.pendingCapabilityControl = null;
    // The click is admitted now. Refresh deferred capability presentation before
    // saving, including when validation later rejects the submitted draft.
    if (control?.isConnected && root.contains(control)) {
      inputs.capabilityChanged?.(control);
      button = root.querySelector("#save-config") || button;
    }
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
