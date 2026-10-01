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
  return `<section class="page-intro"><h2>Export, Backup, Import & Restore</h2><p>Back up, restore, or move your assistant setup and saved data.</p></section><section class="content-card backup-surface">
    <div class="backup-panel transfer-panel" data-setting data-search="export backup import restore share setup custom memories knowledge usage archive request rules">
    <div class="subheading"><h3>Export / Backup</h3><p>Choose a shareable setup, a full backup, or specific sections.</p></div>
    <label class="setting"><span class="setting-copy"><strong>Export type</strong><small><strong>Shareable Setup</strong> includes reusable configuration, Function Tools and Request Rules but excludes private histories and real secret values. <strong>Full Backup</strong> includes all durable agent data. <strong>Custom</strong> lets you choose sections.</small></span><select id="transfer-export-mode"><option value="setup">Shareable Setup</option><option value="full">Full Backup</option><option value="custom">Custom</option></select></label>
    <div id="transfer-custom-options" class="setting-group" hidden><div class="subheading"><h3>Custom backup sections</h3><p>Selected sections are self-contained replacement sections when imported later.</p></div><div class="group-function-choices">${TRANSFER_SECTIONS.map(([key, label]) => `<label class="group-function-choice"><input type="checkbox" class="transfer-custom-section" value="${key}" checked><span><strong>${label}</strong></span></label>`).join("")}</div></div>
    <p class="privacy-warning"><strong>Privacy:</strong> Full and custom backups can contain private memories, Knowledge content, archived conversations and usage metadata. Secret-looking values in configuration and Request Rules are replaced by placeholders; review files before sharing them.</p>
    <div class="backup-actions"><button type="button" id="create-backup-transfer">Create export</button></div>
    ${hasDraft ? "<small>Exports use saved configuration. Your unsaved changes are not included.</small>" : ""}
    <hr>
    <div class="subheading"><h3>Import / Restore</h3><p>Choose an Extended OpenAI export or backup file. It will be checked before anything is changed.</p></div>
    <div class="backup-actions"><button type="button" class="secondary" id="restore-backup-transfer">Choose file</button><input id="backup-file-transfer" type="file" accept="application/zip,.zip,application/json,.json" hidden></div>
    <small>For backups with multiple sections, you can restore everything or choose individual sections. Selected sections replace the destination section; they are never silently merged.</small>
  </div></section>`;
}
