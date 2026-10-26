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

export function renderConfigurationActions(panel, sections = panel._configSections || []) {
  const cleanOnly = panel._configDirty ? "disabled title=\"Save or revert the shared draft first\"" : "";
  const includesBackup = sections.includes("backup");
  return `<details class="agent-actions-menu eoc-details-base"><summary aria-haspopup="menu">Assistant actions</summary><div role="menu"><button type="button" class="secondary" role="menuitem" id="duplicate-agent" ${cleanOnly}>Duplicate agent</button>${includesBackup ? "" : `<button type="button" class="secondary" role="menuitem" id="import-agent">Import configuration</button><button type="button" class="secondary" role="menuitem" id="export-agent" ${cleanOnly}>Export configuration</button>`}</div></details>${panel._configDirty ? `<p class="action-help">Duplicate and Export configuration use the saved configuration. Save or revert your settings changes to enable them.</p>` : ""}`;
}

export function bindSavedConfigurationActions(panel, root) {
  bindOwnedEvent(root.querySelector("#duplicate-agent"), "click", "assistant-actions", async (event) => { const button=event.currentTarget; if(panel._configDirty||button.disabled||panel._eocAssistantDuplicatePending)return; const intent=captureCatalogSelectionIntent(panel); panel._eocAssistantDuplicatePending=true; button.disabled=true; try { const result=await panel._call("configuration","duplicate"); await panel._loadAgents(result.subentry_id,null,intent); panel._toast(`Created ${result.title}`); } catch(err){panel._toast(`Unable to duplicate agent: ${err.message||String(err)}`,true);} finally {panel._eocAssistantDuplicatePending=false;button.disabled=false;} });
  bindOwnedEvent(root.querySelector("#export-agent"), "click", "assistant-actions", async () => { if(panel._configDirty)return; try { await downloadConfigurationSharing(panel); } catch(err) { panel._toast(`Unable to export: ${err.message||String(err)}`,true); } });
}
