import {SHARING_CONTRACT} from "./sharing-review.js";

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
    <div class="subheading"><h2>Export / Backup</h2><p>Choose a shareable setup, a full backup, or specific sections.</p></div>
    <label class="setting"><span class="setting-copy"><strong>Export type</strong><small><strong>Shareable Setup</strong> includes supported public settings and excludes private histories. Custom units may be omitted. <strong>Full Backup</strong> preserves private configuration, credentials and saved assistant data. <strong>Custom</strong> lets you choose private backup sections.</small></span><select id="transfer-export-mode"><option value="setup">Shareable Setup</option><option value="full">Full Backup (private)</option><option value="custom">Custom (private)</option></select></label>
    <div id="transfer-sharing-options"><p class="privacy-warning">${SHARING_CONTRACT}</p><label><input type="checkbox" id="transfer-reviewed-custom"> Include custom content for manual review before download</label><p class="help">Omitted tools and rules must be added or completed separately after import. The saved source is unchanged.</p></div>
    <div id="transfer-custom-options" class="setting-group" hidden><div class="subheading"><h3>Custom backup sections</h3><p>Selected sections are self-contained replacement sections when imported later.</p></div><div class="group-function-choices">${TRANSFER_SECTIONS.map(([key, label]) => `<label class="group-function-choice"><input type="checkbox" class="transfer-custom-section" value="${key}" checked><span><strong>${label}</strong></span></label>`).join("")}</div></div>
    <p class="privacy-warning"><strong>Private backups:</strong> Full and custom backups preserve credentials exactly, including provider credentials when configuration is included. They are not encrypted. Keep them private and store them securely. Older redacted backups retain their original restore behavior.</p>
    <div class="backup-actions"><button type="button" id="create-backup-transfer">Create export</button></div>
    ${hasDraft ? "<small>Exports use saved configuration. Your unsaved changes are not included.</small>" : ""}
    <hr>
    <div class="subheading"><h2>Import / Restore</h2><p>Choose an Extended OpenAI export or backup file. It will be checked before anything is changed.</p></div>
    <div class="backup-actions"><button type="button" class="secondary" id="restore-backup-transfer">Choose file</button><input id="backup-file-transfer" type="file" accept="application/zip,.zip,application/json,.json" hidden></div>
    <small>For backups with multiple sections, you can restore everything or choose individual sections. Selected sections replace the destination section; they are never silently merged.</small>
  </div></section>`;
}
