const EXPORT_DESCRIPTIONS = Object.freeze({
  setup: "Supported settings for sharing; private histories, designated credentials and custom content are excluded by default.",
  full: "Back up private configuration, credentials and all supported saved assistant data.",
  custom: "Choose which private sections to back up.",
});

export function bindExportOptions(root) {
  const mode = root.querySelector("#transfer-export-mode");
  const custom = root.querySelector("#transfer-reviewed-custom");
  const sync = () => {
    const value = mode?.value || "setup";
    const description = root.querySelector("#transfer-export-description");
    if (description) description.textContent = EXPORT_DESCRIPTIONS[value];
    for (const [id, visible] of [
      ["transfer-sharing-options", value === "setup"],
      ["transfer-custom-options", value === "custom"],
      ["transfer-custom-warning", value === "setup" && custom?.checked],
      ["transfer-private-warning", value !== "setup"],
    ]) {
      const element = root.querySelector(`#${id}`);
      if (element) element.hidden = !visible;
    }
  };
  if (mode && !mode.__eocExportOptionsBound) {
    mode.__eocExportOptionsBound = true;
    mode.addEventListener("change", sync);
    custom?.addEventListener("change", sync);
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
    <div class="backup-panel transfer-panel" data-setting data-search="export backup import restore share setup custom memories knowledge usage archive request rules">
    <div class="section-heading"><div><h2 id="transfer-export-heading">Export type</h2><p>Choose what you'd like to export.</p></div></div>
    <select id="transfer-export-mode" aria-labelledby="transfer-export-heading" aria-describedby="transfer-export-description"><option value="setup">Shareable Setup</option><option value="full">Full Backup (private)</option><option value="custom">Custom (private)</option></select>
    <p id="transfer-export-description" aria-live="polite">${EXPORT_DESCRIPTIONS.setup}</p>
    <div id="transfer-sharing-options"><div class="config-toggle setting"><span class="setting-copy"><span class="setting-label-row"><label id="transfer-custom-label" for="transfer-reviewed-custom"><strong>Include custom content</strong></label></span><small id="transfer-custom-help">Include custom prompts, Function Tools and rules for review before download.</small></span><label class="switch-control" for="transfer-reviewed-custom"><input type="checkbox" role="switch" id="transfer-reviewed-custom" aria-labelledby="transfer-custom-label" aria-describedby="transfer-custom-help"><span class="switch-track" aria-hidden="true"></span></label></div></div>
    <p id="transfer-custom-warning" class="notice warning" role="status" hidden>Custom content may contain secrets. Review the export summary and file before sharing.</p>
    <div id="transfer-custom-options" class="setting-group" hidden><div class="subheading"><h3>Custom backup sections</h3><p>Selected sections are self-contained replacement sections when imported later.</p></div><div class="group-function-choices">${TRANSFER_SECTIONS.map(([key, label]) => `<label class="group-function-choice"><input type="checkbox" class="transfer-custom-section" value="${key}" checked><span><strong>${label}</strong></span></label>`).join("")}</div></div>
    <p id="transfer-private-warning" class="notice warning" role="status" hidden>Included credentials and private data are <strong>not encrypted</strong>. Provider credentials are included when configuration is selected. Keep the file private and store it securely.</p>
    <div class="backup-actions"><button type="button" id="create-backup-transfer">Create export</button></div>
    ${hasDraft ? "<small>Exports use saved configuration. Your unsaved changes are not included.</small>" : ""}
    <hr>
    <div class="subheading"><h2>Import / Restore</h2><p>Choose a file to validate and review before restoring.</p></div>
    <div class="backup-actions"><button type="button" class="secondary" id="restore-backup-transfer">Choose file</button><input id="backup-file-transfer" type="file" accept="application/zip,.zip,application/json,.json" hidden></div>
    <p>Selected sections <strong>replace rather than merge</strong> destination data.</p>
  </div></section>`;
}
