export const WS_BACKUP_TRANSFER = "extended_openai_conversation_responses/management/backup_transfer";

const CREATE_ID = "create-backup-transfer";
const RESTORE_ID = "restore-backup-transfer";
const FILE_ID = "backup-file-transfer";
const CANCEL_ID = "restore-transfer-cancel";
const APPLY_ID = "restore-transfer-apply";
const EXPORT_MODE_ID = "transfer-export-mode";
const CUSTOM_OPTIONS_ID = "transfer-custom-options";
const RESTORE_SECTIONS_ID = "restore-transfer-sections";
const RESTORE_STATUS_ID = "restore-transfer-status";

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

const SECTION_LABELS = Object.freeze(Object.fromEntries(TRANSFER_SECTIONS));

function selectedAgent(panel) {
  const agent = panel?._selectedAgent?.();
  if (!agent?.entry_id || !agent?.subentry_id) throw new Error("Select a conversation agent first");
  return agent;
}

export async function callBackupTransfer(panel, action, data = {}) {
  if (!panel?._hass?.callWS) throw new Error("Home Assistant connection is unavailable");
  const agent = selectedAgent(panel);
  return panel._hass.callWS({
    type: WS_BACKUP_TRANSFER,
    action,
    entry_id: agent.entry_id,
    subentry_id: agent.subentry_id,
    data,
  });
}

export function bytesToBase64(bytes) {
  const source = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let binary = "";
  const block = 0x8000;
  for (let offset = 0; offset < source.length; offset += block) {
    binary += String.fromCharCode(...source.subarray(offset, offset + block));
  }
  return globalThis.btoa(binary);
}

export function base64ToBytes(value) {
  const binary = globalThis.atob(String(value || ""));
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

async function defaultSaveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  try {
    const link = document.createElement("a");
    link.href = url;
    link.download = filename || "conversation-agent-export";
    link.click();
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}

export async function downloadSetupExport(panel, saveBlob = defaultSaveBlob) {
  const result = await callBackupTransfer(panel, "setup_export");
  if (!result?.json || !result?.filename) throw new Error("The server returned an invalid setup export");
  const blob = new Blob([result.json], {type: "application/json"});
  await saveBlob(blob, result.filename, result);
  return result;
}

export async function downloadArchiveBackup(panel, {mode = "full", sections = null} = {}, saveBlob = defaultSaveBlob) {
  let sessionId = null;
  try {
    const data = {mode};
    if (sections != null) data.sections = sections;
    const manifest = await callBackupTransfer(panel, "export_start", data);
    sessionId = manifest.session_id;
    if (!sessionId || !Number.isInteger(manifest.chunk_count) || manifest.chunk_count < 1 || !Number.isInteger(manifest.size) || manifest.size < 1) {
      throw new Error("The backup server returned an invalid transfer manifest");
    }
    const parts = [];
    let received = 0;
    for (let index = 0; index < manifest.chunk_count; index += 1) {
      const chunk = await callBackupTransfer(panel, "export_chunk", {session_id: sessionId, index});
      if (chunk.session_id !== sessionId || chunk.index !== index || chunk.offset !== received || !Number.isInteger(chunk.bytes) || chunk.bytes < 1) {
        throw new Error("The backup server returned an out-of-order chunk");
      }
      const bytes = base64ToBytes(chunk.data);
      if (bytes.byteLength !== chunk.bytes || received + bytes.byteLength > manifest.size) {
        throw new Error("The backup server returned an incomplete or oversized chunk");
      }
      parts.push(bytes);
      received += bytes.byteLength;
    }
    if (received !== manifest.size) throw new Error("The backup download is incomplete");
    const blob = new Blob(parts, {type: manifest.content_type || "application/zip"});
    if (blob.size !== manifest.size) throw new Error("The backup download is incomplete");
    await saveBlob(blob, manifest.filename, manifest);
    return manifest;
  } finally {
    if (sessionId) {
      try {
        await callBackupTransfer(panel, "export_cancel", {session_id: sessionId});
      } catch (_err) {
        // The server expires abandoned sessions. Cleanup failure after a complete
        // browser download must not make the successful export appear to fail.
      }
    }
  }
}

export async function downloadFullBackup(panel, saveBlob = defaultSaveBlob) {
  return downloadArchiveBackup(panel, {mode: "full"}, saveBlob);
}

export async function downloadCustomBackup(panel, sections, saveBlob = defaultSaveBlob) {
  if (!Array.isArray(sections) || !sections.length) throw new Error("Select at least one section for the custom backup");
  return downloadArchiveBackup(panel, {mode: "custom", sections}, saveBlob);
}

export async function uploadFullBackup(panel, file) {
  let sessionId = null;
  try {
    const manifest = await callBackupTransfer(panel, "import_start", {
      filename: file.name || "backup",
      size: file.size,
    });
    sessionId = manifest.session_id;
    if (!sessionId || !Number.isInteger(manifest.chunk_size) || manifest.chunk_size < 1 || !Number.isInteger(manifest.chunk_count) || manifest.chunk_count < 1) {
      throw new Error("The backup server returned an invalid upload manifest");
    }
    let sent = 0;
    for (let index = 0; index < manifest.chunk_count; index += 1) {
      const end = Math.min(file.size, sent + manifest.chunk_size);
      const bytes = new Uint8Array(await file.slice(sent, end).arrayBuffer());
      if (!bytes.byteLength) throw new Error("The selected transfer file ended unexpectedly");
      const result = await callBackupTransfer(panel, "import_chunk", {
        session_id: sessionId,
        index,
        data: bytesToBase64(bytes),
      });
      sent += bytes.byteLength;
      if (result.session_id !== sessionId || result.next_index !== index + 1 || result.received !== sent) {
        throw new Error("The backup server rejected the upload sequence");
      }
    }
    if (sent !== file.size) throw new Error("The selected transfer file was not uploaded completely");
    const inspection = await callBackupTransfer(panel, "import_inspect", {session_id: sessionId});
    if (!inspection?.valid) throw new Error("The transfer could not be validated");
    return {session_id: sessionId, ...inspection};
  } catch (err) {
    if (sessionId) {
      try {
        await callBackupTransfer(panel, "import_cancel", {session_id: sessionId});
      } catch (_cleanupErr) {
        // Server-side expiry is the final cleanup fallback.
      }
    }
    throw err;
  }
}

export async function cancelBackupImport(panel, sessionId = panel?._backupTransferSession) {
  if (!sessionId) return;
  if (panel?._backupTransferSession === sessionId) panel._backupTransferSession = null;
  try {
    await callBackupTransfer(panel, "import_cancel", {session_id: sessionId});
  } catch (_err) {
    // Closing the dialog should remain reliable; the server expires the session.
  }
}

export function openBackupPicker(panel, input) {
  const previousSession = panel?._backupTransferSession;
  if (previousSession && panel) panel._backupTransferSession = null;
  input.value = "";
  // Keep the picker invocation in the original click task. Awaiting server cleanup
  // first can consume the browser's transient user-activation permission.
  input.click();
  if (previousSession) void cancelBackupImport(panel, previousSession);
}

function sectionChoices(panel, {className, checked = true, available = null} = {}) {
  const allowed = available ? new Set(available) : null;
  return TRANSFER_SECTIONS
    .filter(([key]) => !allowed || allowed.has(key))
    .map(([key, label]) => `<label class="group-function-choice"><input type="checkbox" class="${className}" value="${panel?._e ? panel._e(key) : key}" ${checked ? "checked" : ""}><span><strong>${panel?._e ? panel._e(label) : label}</strong></span></label>`)
    .join("");
}

function unifiedTransferPanel(disabled = false) {
  return `<div class="backup-panel transfer-panel" data-setting data-search="export backup import restore share setup custom memories knowledge usage archive request rules">
    <div class="subheading"><h3>Export / Backup</h3><p>Choose a shareable setup, a complete disaster-recovery backup, or only the sections you need.</p></div>
    <label class="setting"><span class="setting-copy"><strong>Export type</strong><small><strong>Shareable Setup</strong> includes reusable configuration, Function Tools and Request Rules but excludes private histories and real secret values. <strong>Full Backup</strong> includes all durable agent data. <strong>Custom</strong> lets you choose sections.</small></span><select id="${EXPORT_MODE_ID}" ${disabled ? "disabled" : ""}><option value="setup">Shareable Setup</option><option value="full">Full Backup</option><option value="custom">Custom</option></select></label>
    <div id="${CUSTOM_OPTIONS_ID}" class="setting-group" hidden><div class="subheading"><h3>Custom backup sections</h3><p>Selected sections are self-contained replacement sections when imported later.</p></div><div class="group-function-choices">${TRANSFER_SECTIONS.map(([key, label]) => `<label class="group-function-choice"><input type="checkbox" class="transfer-custom-section" value="${key}" checked><span><strong>${label}</strong></span></label>`).join("")}</div></div>
    <p class="privacy-warning"><strong>Privacy:</strong> Full and custom backups can contain private memories, Knowledge content, archived conversations and usage metadata. Secret-looking values in configuration and Request Rules are replaced by placeholders; review files before sharing them.</p>
    <div class="backup-actions"><button type="button" id="${CREATE_ID}" ${disabled ? "disabled" : ""}>Create export</button></div>
    ${disabled ? "<small>Save or revert configuration changes before exporting so the file matches the saved agent.</small>" : ""}
    <hr>
    <div class="subheading"><h3>Import / Restore</h3><p>Select any current or legacy Extended OpenAI setup export, custom backup or full backup. The file is validated before anything changes.</p></div>
    <div class="backup-actions"><button type="button" class="secondary" id="${RESTORE_ID}">Choose file</button><input id="${FILE_ID}" type="file" accept="application/zip,.zip,application/json,.json" hidden></div>
    <small>For backups with multiple sections, you can restore everything or choose individual sections. Selected sections replace the destination section; they are never silently merged.</small>
  </div>`;
}

function decorateBackupMarkupDom(html) {
  const template = document.createElement("template");
  template.innerHTML = html;
  const root = template.content;
  const oldCreate = root.querySelector("#create-backup");
  const disabled = Boolean(oldCreate?.disabled);
  const panel = root.querySelector("#config-backup .backup-panel");
  if (panel) panel.outerHTML = unifiedTransferPanel(disabled);
  const heading = root.querySelector("#config-backup .config-section-heading .eyebrow");
  const description = root.querySelector("#config-backup .config-section-heading p:last-child");
  if (heading) heading.textContent = "Export, Backup, Import & Restore";
  if (description) description.textContent = "Move reusable setup or durable agent data safely between agents and installations.";

  // The old toolbar actions are superseded by the unified workflow. Keeping the
  // backend commands means older clients remain compatible without presenting two
  // competing import/export interfaces to current users.
  root.querySelector("#import-agent")?.remove();
  root.querySelector("#export-agent")?.remove();
  return template.innerHTML;
}

export function decorateBackupMarkup(html) {
  if (typeof document !== "undefined" && typeof document.createElement === "function") return decorateBackupMarkupDom(html);
  return String(html)
    .replace('id="create-backup"', `id="${CREATE_ID}"`)
    .replace('id="restore-backup"', `id="${RESTORE_ID}"`)
    .replace('id="backup-file" type="file" accept="application/json,.json"', `id="${FILE_ID}" type="file" accept="application/zip,.zip,application/json,.json"`);
}

export function decorateRestoreDialog(html) {
  const dirtyWarning = /unsaved configuration changes/i.test(String(html))
    ? '<p class="inline-error">Your unsaved configuration changes will be discarded if configuration is one of the restored sections.</p>'
    : "";
  return `<dialog id="restore-dialog" class="editor-dialog" aria-labelledby="restore-dialog-title"><div class="dialog-header"><h2 id="restore-dialog-title">Import / Restore</h2></div><div class="dialog-body"><div><strong id="restore-backup-name"></strong><p id="restore-backup-meta" class="meta"></p></div><ul id="restore-summary" class="restore-summary"></ul><fieldset class="setting-group"><legend><strong>Sections to replace</strong></legend><p class="help">Only sections contained in this file are shown. Unselected destination sections remain unchanged.</p><div id="${RESTORE_SECTIONS_ID}" class="group-function-choices"></div></fieldset><div id="${RESTORE_STATUS_ID}" class="validation" role="status" aria-live="polite"></div><div class="notice"><strong>Replacement, not merge</strong><p>Every selected section replaces that section on the current agent. The combined target is validated first, including Request Rule references to Function Tools. A final confirmation is required before applying it.</p></div>${dirtyWarning}</div><div class="dialog-actions"><button type="button" class="secondary" id="${CANCEL_ID}">Cancel</button><button type="button" class="danger" id="${APPLY_ID}" disabled>Restore selected sections</button></div></dialog>`;
}

function selectedValues(root, selector) {
  return [...root.querySelectorAll(selector)].filter((item) => item.checked).map((item) => item.value);
}

function formatSourceKind(value) {
  return {
    portable_transfer: "Shareable Setup",
    custom_backup: "Custom Backup",
    full_backup: "Full Backup",
    legacy_setup: "Legacy setup export",
  }[value] || "Extended OpenAI transfer";
}

function transferSummaryLines(result, summaryFormatter) {
  const summary = result?.summary || {};
  const lines = [formatSourceKind(result?.source_kind)];
  const configured = summaryFormatter(summary).filter(Boolean);
  for (const line of configured) if (!lines.includes(line)) lines.push(line);
  return lines;
}

function updateSensitiveStatus(panel, preview) {
  const root = panel.shadowRoot;
  const status = root.querySelector(`#${RESTORE_STATUS_ID}`);
  if (!status) return;
  const preserved = Number(preview?.preserved_sensitive_field_count || 0);
  const missing = Number(preview?.missing_sensitive_field_count || 0);
  if (missing) {
    status.className = "validation error";
    status.textContent = `${missing} redacted secret field${missing === 1 ? "" : "s"} cannot be preserved from the destination and will need to be entered after restore.${preserved ? ` ${preserved} existing secret field${preserved === 1 ? " was" : "s were"} preserved.` : ""}`;
  } else if (preserved) {
    status.className = "validation success";
    status.textContent = `${preserved} redacted secret field${preserved === 1 ? " was" : "s were"} preserved from the current destination configuration.`;
  } else {
    status.className = "validation success";
    status.textContent = "The selected sections passed validation.";
  }
}

async function refreshImportPreview(panel) {
  const root = panel.shadowRoot;
  const sessionId = panel._backupTransferSession;
  if (!sessionId) return false;
  const sections = selectedValues(root, ".transfer-restore-section");
  const apply = root.querySelector(`#${APPLY_ID}`);
  const status = root.querySelector(`#${RESTORE_STATUS_ID}`);
  if (!sections.length) {
    apply.disabled = true;
    if (status) {
      status.className = "validation error";
      status.textContent = "Select at least one section to restore.";
    }
    return false;
  }
  const token = (panel._transferPreviewToken || 0) + 1;
  panel._transferPreviewToken = token;
  apply.disabled = true;
  if (status) {
    status.className = "validation";
    status.textContent = "Validating selected sections…";
  }
  try {
    const result = await callBackupTransfer(panel, "import_inspect", {session_id: sessionId, sections});
    if (panel._transferPreviewToken !== token || panel._backupTransferSession !== sessionId) return false;
    panel._backupTransferPreview = result.preview;
    updateSensitiveStatus(panel, result.preview);
    apply.disabled = false;
    return true;
  } catch (err) {
    if (panel._transferPreviewToken !== token) return false;
    panel._backupTransferPreview = null;
    if (status) {
      status.className = "validation error";
      status.textContent = err.message || String(err);
    }
    apply.disabled = true;
    return false;
  }
}

export function bindBackupTransfer(panel, summaryFormatter = () => []) {
  const root = panel?.shadowRoot;
  if (!root) return;

  const exportMode = root.querySelector(`#${EXPORT_MODE_ID}`);
  const customOptions = root.querySelector(`#${CUSTOM_OPTIONS_ID}`);
  const syncExportMode = () => {
    if (customOptions) customOptions.hidden = exportMode?.value !== "custom";
  };
  exportMode?.addEventListener("change", syncExportMode);
  syncExportMode();

  root.querySelector(`#${CREATE_ID}`)?.addEventListener("click", async () => {
    if (panel._configDirty) return;
    const button = root.querySelector(`#${CREATE_ID}`);
    const mode = exportMode?.value || "setup";
    const sections = selectedValues(root, ".transfer-custom-section");
    if (mode === "custom" && !sections.length) {
      panel._toast("Select at least one section for the custom backup", true);
      return;
    }
    panel._setSaving(button, true);
    try {
      if (mode === "setup") await downloadSetupExport(panel);
      else if (mode === "custom") await downloadCustomBackup(panel, sections);
      else await downloadFullBackup(panel);
      panel._toast(mode === "setup" ? "Shareable setup exported" : mode === "custom" ? "Custom backup created" : "Full backup created");
    } catch (err) {
      panel._toast(`Unable to create export: ${err.message || String(err)}`, true);
    } finally {
      panel._setSaving(button, false);
    }
  });

  root.querySelector(`#${RESTORE_ID}`)?.addEventListener("click", () => {
    openBackupPicker(panel, root.querySelector(`#${FILE_ID}`));
  });

  root.querySelector(`#${FILE_ID}`)?.addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const apply = root.querySelector(`#${APPLY_ID}`);
    apply.disabled = true;
    await cancelBackupImport(panel);
    try {
      const result = await uploadFullBackup(panel, file);
      panel._backupTransferSession = result.session_id;
      panel._backupTransferInspection = result;
      panel._backupTransferPreview = result.preview;
      root.querySelector("#restore-backup-name").textContent = result.title;
      const created = result.summary?.created_at ? new Date(result.summary.created_at).toLocaleString() : "legacy export";
      const version = result.summary?.integration_version ? ` · integration ${result.summary.integration_version}` : "";
      root.querySelector("#restore-backup-meta").textContent = `${formatSourceKind(result.source_kind)} · ${created}${version}`;
      root.querySelector("#restore-summary").innerHTML = transferSummaryLines(result, summaryFormatter).map((line) => `<li>${panel._e(line)}</li>`).join("");
      const sectionRoot = root.querySelector(`#${RESTORE_SECTIONS_ID}`);
      sectionRoot.innerHTML = sectionChoices(panel, {className: "transfer-restore-section", available: result.available_sections});
      sectionRoot.querySelectorAll(".transfer-restore-section").forEach((input) => input.addEventListener("change", () => void refreshImportPreview(panel)));
      updateSensitiveStatus(panel, result.preview);
      apply.disabled = false;
      root.querySelector("#restore-dialog").showModal();
    } catch (err) {
      panel._backupTransferSession = null;
      panel._backupTransferInspection = null;
      panel._backupTransferPreview = null;
      apply.disabled = true;
      panel._toast(err.message || String(err), true);
    }
  });

  root.querySelector(`#${CANCEL_ID}`)?.addEventListener("click", async () => {
    panel._transferPreviewToken = (panel._transferPreviewToken || 0) + 1;
    await cancelBackupImport(panel);
    panel._backupTransferInspection = null;
    panel._backupTransferPreview = null;
    root.querySelector("#restore-dialog").close();
  });

  root.querySelector(`#${APPLY_ID}`)?.addEventListener("click", async () => {
    const sessionId = panel._backupTransferSession;
    if (!sessionId) return;
    const sections = selectedValues(root, ".transfer-restore-section");
    if (!sections.length) return;
    const labels = sections.map((key) => SECTION_LABELS[key] || key).join(", ");
    if (typeof panel._confirm === "function") {
      const confirmed = await panel._confirm(
        "Restore selected sections?",
        `This will replace, not merge, the following sections on the current agent: ${labels}.`,
        "Restore",
      );
      if (!confirmed) return;
    }
    const button = root.querySelector(`#${APPLY_ID}`);
    panel._setSaving(button, true);
    try {
      await callBackupTransfer(panel, "import_restore", {session_id: sessionId, sections});
      panel._backupTransferSession = null;
      panel._backupTransferInspection = null;
      panel._backupTransferPreview = null;
      root.querySelector("#restore-dialog").close();
      panel._clearConfigDraft();
      await panel._loadAgents(panel._agentId);
      panel._toast("Selected sections restored");
    } catch (err) {
      panel._backupTransferSession = null;
      button.disabled = true;
      panel._toast(`Unable to restore: ${err.message || String(err)} Re-select the transfer file to retry.`, true);
    } finally {
      panel._setSaving(button, false);
    }
  });
}
