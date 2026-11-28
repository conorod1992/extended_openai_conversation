import {usageRetentionDeletionNotice} from "./retention-settings-ui.js";
import {captureConfigurationOwner, captureConfigurationSubmission, configurationSubmissionIsCurrent, presentAcknowledgedMutation, processMutationResponse, requireMutationAcknowledgement, retireReplacedConfigurationDraft, recoverCommittedRetention} from "./configuration-acknowledgement.js";
import {beginOperation, invalidateOperation} from "./operation-ownership.js";
import {bindOwnedEvent} from "./management-owned-events.js";
import {captureCatalogSelectionIntent} from "./management-catalog-ownership.js";

export function invalidateImportPreview(panel, applyButton, summary) {
  invalidateOperation(panel, "import-preview");
  panel._importDocument = null;
  panel._importRevision = null;
  panel._importShortenedRetention = null;
  if (applyButton) applyButton.disabled = true;
  if (summary) summary.textContent = "Validate the document to preview it.";
}

// A lost import reply may already have created an assistant. Keep the source
// text, but require an authoritative catalogue read before another preview can
// authorize a new write. A new assistant affects the global catalogue, so this
// payload-free barrier survives draft, selection and authentication changes.
export async function refreshUncertainConfigurationImport(panel, {configuration = false} = {}) {
  const recovery = panel._eocImportRecovery;
  if (!recovery) return;
  const owner = captureConfigurationOwner(panel);
  const receipt = await panel._loadAgents(panel._agentId, owner, captureCatalogSelectionIntent(panel), {preserveAuthoringOnFailure:true});
  if (panel._error) throw new Error(panel._error);
  if (!receipt || receipt.sequence !== panel._eocCatalogReadSequence
      || receipt.userId !== owner.userId || !configurationSubmissionIsCurrent(panel, owner)) {
    throw new Error("The assistant list could not be refreshed. Keep the import source and validate again before another import.");
  }
  if (configuration) {
    panel._invalidateCleanConfiguration?.(owner.agentId);
    panel._configDataStale = true;
    await panel._loadConfigDraft({preserveResult:true, full:true});
    if (!configurationSubmissionIsCurrent(panel, owner) || panel._configDataStale) {
      throw new Error("Configuration could not be refreshed. Validate again before another import.");
    }
  }
  if (panel._eocImportRecovery === recovery) {
    panel._eocImportRecovery = null;
    return true;
  }
}

export function bindSavedConfigurationActions(panel, root) {
  bindOwnedEvent(root.querySelector("#duplicate-agent"), "click", "assistant-actions",
    event => duplicateSavedConfiguration(panel, event.currentTarget));
}

export async function duplicateSavedConfiguration(panel, button) {
  if (panel._configDirty || button.disabled || panel._eocAssistantDuplicatePending) return;
  const intent = captureCatalogSelectionIntent(panel);
  panel._eocAssistantDuplicatePending = true;
  button.disabled = true;
  try {
    const result = await panel._call("configuration", "duplicate");
    requireMutationAcknowledgement(result?.subentry_id && typeof result.title === "string", "creating the duplicate agent");
    // The catalogue has its own publication/selection guards. Refresh it even
    // when a later selection retired the source; never steal that selection.
    await processMutationResponse(result, () => panel._loadAgents(result.subentry_id, null, intent));
    let acknowledgementOwner = intent.owner;
    // Selecting the newly created agent deliberately retires the source draft.
    // Adopt only that destination, retaining the original authentication lease.
    if (panel._agentId === result.subentry_id) acknowledgementOwner = {...captureConfigurationOwner(panel), userId:intent.owner.userId, authenticationGeneration:intent.owner.authenticationGeneration};
    await presentAcknowledgedMutation(panel, acknowledgementOwner, `Created ${result.title}`, null, result);
  } catch (err) {
    if (configurationSubmissionIsCurrent(panel, intent.owner)) panel._toast(`Unable to duplicate agent: ${err.message || String(err)}`, true);
  } finally {
    panel._eocAssistantDuplicatePending = false;
    button.disabled = Boolean(panel._configDirty);
  }
}

export function openConfigurationImport(panel) {
  panel._eocConfigurationImportDialog = renderConfigurationImportDialog;
  panel._ensureOwnedDialog("import-dialog");
  const root = panel.shadowRoot;
  const actionsMenu = root.querySelector(".agent-actions-menu");
  const dialog = root.querySelector("#import-dialog");
  if (!dialog) return;
  const importDocument = root.querySelector("#import-document"), importApply = root.querySelector("#import-apply"), importSummary = root.querySelector("#import-summary");
  bindOwnedEvent(importDocument, "input", "assistant-actions", () => invalidateImportPreview(panel, importApply, importSummary));
  root.querySelectorAll('input[name="import-mode"]').forEach(control => bindOwnedEvent(control, "change", "assistant-actions", () => invalidateImportPreview(panel, importApply, importSummary)));
  bindOwnedEvent(root.querySelector("#import-preview"), "click", "assistant-actions", async () => {
    invalidateImportPreview(panel, importApply, importSummary);
    const document = importDocument.value, mode = root.querySelector('input[name="import-mode"]:checked').value;
    let revision = panel._configData?.revision;
    const agent = panel._agentId;
    const ownsOperation = beginOperation(panel, "import-preview");
    const ownsInput = () => ownsOperation() && dialog?.open && importDocument.isConnected
      && importDocument.value === document && root.querySelector('input[name="import-mode"]:checked').value === mode && panel._agentId === agent;
    const current = () => ownsInput() && panel._configData?.revision === revision;
    try {
      if (await refreshUncertainConfigurationImport(panel, {configuration:mode === "current"})) revision = panel._configData?.revision;
      if (!ownsInput()) return;
      if (mode === "current" && (panel._draftAgentId !== agent || panel._configData?.projection === "retention"
          || panel._configDataStale || !revision)) {
        await panel._loadConfigDraft({preserveResult:true, full:true});
        if (!ownsInput()) return;
        revision = panel._configData?.revision;
        if (!revision || panel._configDataStale || panel._draftAgentId !== agent || panel._configData?.projection === "retention") {
          throw new Error("Unable to load the saved configuration for import.");
        }
      }
      if (!current()) return;
      const result = await panel._call("configuration", "import_preview", {document, mode});
      if (!current()) return;
      panel._importDocument = document;
      panel._importMode = mode;
      panel._importRevision = revision;
      panel._importShortenedRetention = result.shortened_usage_retention || null;
      const retentionNotice = usageRetentionDeletionNotice(panel._importShortenedRetention);
      importSummary.textContent = `${result.title} / ${result.summary.model} / ${result.summary.tools} tools / ${result.summary.function_groups} function groups / ${result.summary.speech_rules} speech rules${retentionNotice ? `. ${retentionNotice}` : ""}`;
      importApply.disabled = false;
    } catch (err) {
      if (!current()) return;
      panel._importDocument = null;
      importSummary.textContent = err.message || String(err);
      importApply.disabled = true;
    }
  });
  bindOwnedEvent(root.querySelector("#import-apply"), "click", "assistant-actions", () => applyConfigurationImport(panel, root));
  bindOwnedEvent(root.querySelector("#import-cancel"), "click", "assistant-actions",()=>{invalidateImportPreview(panel,importApply,importSummary);root.querySelector("#import-dialog").close();actionsMenu?.querySelector("summary")?.focus();});
  bindOwnedEvent(root.querySelector("#import-dialog"), "cancel", "assistant-actions",()=>{invalidateImportPreview(panel,importApply,importSummary);requestAnimationFrame(()=>actionsMenu?.querySelector("summary")?.focus());});
  invalidateImportPreview(panel, importApply, importSummary);
  if (!dialog.open) dialog.showModal();
}

function renderConfigurationImportDialog(panel) {
  return `<dialog id="import-dialog" class="editor-dialog wide" aria-labelledby="import-dialog-title"><div class="dialog-header"><h2 id="import-dialog-title">Import agent configuration</h2></div><div class="dialog-body">${panel._configDirty ? `<div class="notice warning"><strong>Unsaved shared draft</strong><p>Creating a new agent preserves this draft. Overwriting the current agent discards it after confirmation.</p></div>` : ""}<label>Exported JSON or YAML<textarea id="import-document" class="yaml-editor" spellcheck="false"></textarea></label><div class="mode-row"><label><input type="radio" name="import-mode" value="new" checked> Create a new agent</label><label><input type="radio" name="import-mode" value="current"> Overwrite current agent</label></div><div id="import-summary" class="validation" aria-live="polite">Validate the document to preview it.</div></div><div class="dialog-actions"><button type="button" class="secondary" id="import-cancel">Cancel</button><button type="button" class="secondary" id="import-preview">Validate & preview</button><button type="button" id="import-apply" disabled>Import</button></div></dialog>`;
}

// Preview admission and server acknowledgement are separate from catalogue/DOM
// publication. Consume a successful preview before either can fail.
export async function applyConfigurationImport(panel, root) {
  const input = root.querySelector("#import-document");
  const button = root.querySelector("#import-apply");
  if (panel._eocImportPending) return;
  const mode = root.querySelector('input[name="import-mode"]:checked').value;
  const source = input.value, revision = panel._importRevision;
  const intent = captureCatalogSelectionIntent(panel);
  const matches = () => !panel._eocImportRecovery && configurationSubmissionIsCurrent(panel, intent.owner)
    && panel._importDocument === source && input.value === source
    && panel._importMode === mode && panel._configData?.revision === revision;
  if (!source || !matches()) {
    invalidateImportPreview(panel, button, root.querySelector("#import-summary"));
    return;
  }
  const retentionNotice = usageRetentionDeletionNotice(panel._importShortenedRetention);
  if (mode === "current" && !await panel._confirm(
    "Overwrite this agent?",
    `The saved configuration will be replaced.${panel._configDirty ? " Your unsaved shared draft will be discarded." : ""}${retentionNotice ? ` ${retentionNotice}` : ""}`,
    retentionNotice ? "Overwrite and delete details" : "Overwrite",
  )) return;
  if (!matches()) return;
  const submittedDraft = captureConfigurationSubmission(panel);
  panel._eocImportPending = true;
  button.disabled = true;
  try {
    const result = await panel._call("configuration", "import", {document:source, mode, confirm:mode === "current", ...(mode === "current" ? {revision, ...(retentionNotice ? {confirm_retention:true} : {})} : {})});
    requireMutationAcknowledgement(result?.subentry_id && result.status === (mode === "current" ? "updated" : "created"), "importing the configuration");
    if (!configurationSubmissionIsCurrent(panel, intent.owner)) {
      // A confirmed create still changes the catalogue when its former editor
      // can no longer consume the preview or display the new identity.
      panel._eocImportRecovery = {};
      return;
    }
    panel._importDocument = null;
    panel._importRevision = null;
    let acknowledgementOwner = intent.owner;
    await presentAcknowledgedMutation(panel, () => configurationSubmissionIsCurrent(panel, acknowledgementOwner), mode === "current" ? "Configuration imported" : "Agent created from import; your current draft is preserved", async () => {
      let loadIntent = intent;
      if (mode === "current") {
        retireReplacedConfigurationDraft(panel, submittedDraft);
        acknowledgementOwner = captureConfigurationOwner(panel);
      }
      root.querySelector("#import-dialog").close();
      if (mode === "current") loadIntent = captureCatalogSelectionIntent(panel);
      await panel._loadAgents(panel._agentId, acknowledgementOwner, loadIntent);
    }, result);
  } catch (err) {
    const requiresRecovery = ["uncertain", "committed"].includes(err?.mutationOutcome);
    // The effect outlives its presentation owner. Retain no old source or
    // authority, only the requirement for a new owned catalogue read.
    if (requiresRecovery) panel._eocImportRecovery = {};
    if (configurationSubmissionIsCurrent(panel, intent.owner)) {
      if (requiresRecovery) {
        if (panel._importDocument === source && panel._importRevision === revision) {
          invalidateImportPreview(panel, button, root.querySelector("#import-summary"));
        }
        const summary = root.querySelector("#import-summary");
        if (summary && input.value === source) summary.textContent = "The import may already have been applied. Validate & preview again to refresh the assistant list before another import.";
      }
      if (mode === "current" && err?.mutationOutcome === "committed") {
        // The overwrite committed. Retire its submitted draft, preserving only
        // later edits and a recoverable baseline if the authoritative read fails.
        retireReplacedConfigurationDraft(panel, submittedDraft, {retainUntilRead:true});
        await recoverCommittedRetention(panel, intent.owner);
      }
      if (!configurationSubmissionIsCurrent(panel, intent.owner)) return;
      panel._toast(`Unable to import: ${err.message || String(err)}`, true);
    }
  } finally {
    panel._eocImportPending = false;
    button.disabled = !panel._importDocument || Boolean(panel._eocImportRecovery);
  }
}
