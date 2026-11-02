import {captureConfigurationOwner, captureConfigurationSubmission, configurationSubmissionIsCurrent, presentAcknowledgedMutation, processMutationResponse, requireMutationAcknowledgement, retireReplacedConfigurationDraft} from "./configuration-acknowledgement.js";
import {downloadConfigurationSharing} from "./sharing-review.js";
import {invalidateOperation} from "./operation-ownership.js";
import {bindOwnedEvent} from "./management-owned-events.js";
import {captureCatalogSelectionIntent} from "./management-catalog-ownership.js";

export function invalidateImportPreview(panel, applyButton, summary) {
  invalidateOperation(panel, "import-preview");
  panel._importDocument = null;
  panel._importRevision = null;
  if (applyButton) applyButton.disabled = true;
  if (summary) summary.textContent = "Validate the document to preview it.";
}

// A lost import reply may already have created an assistant. Keep the source
// text, but require an authoritative catalogue read before another preview can
// authorize a new write. A new assistant affects the global catalogue, so this
// payload-free barrier survives draft, selection and authentication changes.
export async function refreshUncertainConfigurationImport(panel) {
  const recovery = panel._eocImportRecovery;
  if (!recovery) return;
  const owner = captureConfigurationOwner(panel);
  const receipt = await panel._loadAgents(panel._agentId, owner, captureCatalogSelectionIntent(panel), {preserveAuthoringOnFailure:true});
  if (panel._error) throw new Error(panel._error);
  if (!receipt || receipt.sequence !== panel._eocCatalogReadSequence
      || receipt.userId !== owner.userId || !configurationSubmissionIsCurrent(panel, owner)) {
    throw new Error("The assistant list could not be refreshed. Keep the import source and validate again before another import.");
  }
  if (panel._eocImportRecovery === recovery) {
    panel._eocImportRecovery = null;
  }
}

export function renderConfigurationActions(panel, sections = panel._configSections || []) {
  const cleanOnly = panel._configDirty ? "disabled title=\"Save or revert the shared draft first\"" : "";
  const includesBackup = sections.includes("backup");
  return `<details class="agent-actions-menu eoc-details-base"><summary aria-haspopup="menu">Assistant actions</summary><div role="menu"><button type="button" class="secondary" role="menuitem" id="duplicate-agent" ${cleanOnly}>Duplicate agent</button>${includesBackup ? "" : `<button type="button" class="secondary" role="menuitem" id="import-agent">Import configuration</button><button type="button" class="secondary" role="menuitem" id="export-agent" ${cleanOnly}>Export configuration</button>`}</div></details>${panel._configDirty ? `<p class="action-help">Duplicate and Export configuration use the saved configuration. Save or revert your settings changes to enable them.</p>` : ""}`;
}

export function bindSavedConfigurationActions(panel, root) {
  bindOwnedEvent(root.querySelector("#duplicate-agent"), "click", "assistant-actions", async event => {
    const button = event.currentTarget;
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
      button.disabled = false;
    }
  });
  bindOwnedEvent(root.querySelector("#export-agent"), "click", "assistant-actions", async () => { if(panel._configDirty)return; try { await downloadConfigurationSharing(panel); } catch(err) { panel._toast(`Unable to export: ${err.message||String(err)}`,true); } });
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
  if (mode === "current" && !await panel._confirm("Overwrite this agent?", `The saved configuration will be replaced.${panel._configDirty ? " Your unsaved shared draft will be discarded." : ""} Retained history and parent-entry credentials are not affected.`, "Overwrite")) return;
  if (!matches()) return;
  const submittedDraft = captureConfigurationSubmission(panel);
  panel._eocImportPending = true;
  button.disabled = true;
  try {
    const result = await panel._call("configuration", "import", {document:source, mode, confirm:mode === "current", ...(mode === "current" ? {revision} : {})});
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
        loadIntent = captureCatalogSelectionIntent(panel);
      }
      root.querySelector("#import-dialog").close();
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
      panel._toast(`Unable to import: ${err.message || String(err)}`, true);
    }
  } finally {
    panel._eocImportPending = false;
    button.disabled = !panel._importDocument || Boolean(panel._eocImportRecovery);
  }
}
