const EXPORT_DESCRIPTIONS = Object.freeze({
  full: "Back up private configuration, credentials and all supported saved assistant data.",
  custom: "Choose which private sections to back up.",
});

export function bindExportOptions(root) {
  const mode = root.querySelector("#transfer-export-mode");
  const sync = () => {
    const value = mode?.value || "full";
    const description = root.querySelector("#transfer-export-description");
    if (description) description.textContent = EXPORT_DESCRIPTIONS[value];
    const customOptions = root.querySelector("#transfer-custom-options");
    if (customOptions) customOptions.hidden = value !== "custom";
  };
  if (mode && !mode.__eocExportOptionsBound) {
    mode.__eocExportOptionsBound = true;
    mode.addEventListener("change", sync);
  }
  sync();
}

export const TRANSFER_SECTIONS = Object.freeze([
  ["configuration", "Agent configuration and Function Tools"],
  ["request_rules", "Request Rules"],
  ["persistent_memory", "Persistent memories"],
  ["temporary_memory", "Active temporary memories"],
  ["knowledge", "Knowledge sources"],
  ["conversation_archive", "Conversation archive"],
  ["usage", "Usage history"],
  ["guest_mode", "Guest Mode schedule"],
]);

export function renderBackupTransferPanel(hasDraft = false) {
  return `<section class="page-intro"><h1>Export, Backup, Import & Restore</h1><p>Back up, restore, or move your assistant setup and saved data.</p></section><section class="content-card backup-surface">
    <div class="backup-panel transfer-panel" data-setting data-search="export backup import restore custom memories knowledge usage archive request rules">
    <div class="section-heading"><div><h2 id="transfer-export-heading">Export type</h2><p>Choose what you'd like to export.</p></div></div>
    <select id="transfer-export-mode" aria-labelledby="transfer-export-heading" aria-describedby="transfer-export-description"><option value="full">Full Backup (private)</option><option value="custom">Custom (private)</option></select>
    <p id="transfer-export-description" aria-live="polite">${EXPORT_DESCRIPTIONS.full}</p>
    <div id="transfer-custom-options" class="setting-group" hidden><div class="subheading"><h3>Custom backup sections</h3><p>Selected sections are self-contained replacement sections when imported later.</p></div><div class="group-function-choices">${TRANSFER_SECTIONS.map(([key, label]) => `<label class="group-function-choice"><input type="checkbox" class="transfer-custom-section" value="${key}" checked><span><strong>${label}</strong></span></label>`).join("")}</div></div>
    <p id="transfer-private-warning" class="notice warning" role="status">Included credentials and private data are <strong>not encrypted</strong>. Provider credentials are included when configuration is selected. Keep the file private and store it securely.</p>
    <div class="backup-actions"><button type="button" id="create-backup-transfer">Create export</button></div>
    ${hasDraft ? "<small>Exports use saved configuration. Your unsaved changes are not included.</small>" : ""}
    <hr>
    <div class="subheading"><h2>Import / Restore</h2><p>Choose a file to validate and review before restoring.</p></div>
    <div class="backup-actions"><button type="button" class="secondary" id="restore-backup-transfer">Choose file</button><input id="backup-file-transfer" type="file" accept="application/zip,.zip,application/json,.json" hidden></div>
    <p>Selected sections <strong>replace rather than merge</strong> destination data.</p>
  </div></section>`;
}
