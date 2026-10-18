import {restoreScopeMarkup} from "./management-decision-guidance.js";
import {TRANSFER_SECTIONS} from "./backup-transfer-shell.js";
import {backupSummaryLines} from "./backup-summary.js";
export {backupSummaryLines};
export {TRANSFER_SECTIONS, renderBackupTransferPanel} from "./backup-transfer-shell.js";
export const WS_BACKUP_TRANSFER = "extended_openai_conversation_responses/management/backup_transfer";

const CREATE_ID = "create-backup-transfer";
const RESTORE_ID = "restore-backup-transfer";
const FILE_ID = "backup-file-transfer";
const CANCEL_ID = "restore-transfer-cancel";
const APPLY_ID = "restore-transfer-apply";
const EXPORT_MODE_ID = "transfer-export-mode";
const CUSTOM_OPTIONS_ID = "transfer-custom-options";
const RESTORE_SECTIONS_ID = "restore-transfer-sections";
const RESTORE_USER_MAPPINGS_ID = "restore-transfer-user-mappings";
const RESTORE_STATUS_ID = "restore-transfer-status";
const IMPORT_PREVIEW_DEBOUNCE_MS = 150;

const SECTION_LABELS = Object.freeze(Object.fromEntries(TRANSFER_SECTIONS));

function selectedAgent(panel) {
  const agent = panel?._selectedAgent?.();
  if (!agent?.entry_id || !agent?.subentry_id) throw new Error("Select a conversation agent first");
  return Object.freeze({entry_id: agent.entry_id, subentry_id: agent.subentry_id});
}

function transferSession(sessionId, owner) {
  return Object.freeze({session_id: sessionId, owner: Object.freeze({entry_id: owner.entry_id, subentry_id: owner.subentry_id})});
}

function ownsTransfer(panel, session) {
  const current = panel?._selectedAgent?.();
  return Boolean(session?.owner && current?.entry_id === session.owner.entry_id && current?.subentry_id === session.owner.subentry_id);
}

export async function callBackupTransfer(panel, action, data = {}, owner = selectedAgent(panel)) {
  if (!panel?._hass?.callWS) throw new Error("Home Assistant connection is unavailable");
  const agent = owner;
  const result = await panel._hass.callWS({
    type: WS_BACKUP_TRANSFER,
    action,
    entry_id: agent.entry_id,
    subentry_id: agent.subentry_id,
    data,
  });
  if (action === "import_restore") {
    panel._invalidateAfterMutation?.(agent.subentry_id, "backup", "restore");
  }
  return result;
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
  const owner = selectedAgent(panel);
  let session = null;
  try {
    const data = {mode};
    if (sections != null) data.sections = sections;
    const manifest = await callBackupTransfer(panel, "export_start", data, owner);
    session = transferSession(manifest.session_id, owner);
    if (!session.session_id || !Number.isInteger(manifest.chunk_count) || manifest.chunk_count < 1 || !Number.isInteger(manifest.size) || manifest.size < 1) {
      throw new Error("The backup server returned an invalid transfer manifest");
    }
    const parts = [];
    let received = 0;
    for (let index = 0; index < manifest.chunk_count; index += 1) {
      const chunk = await callBackupTransfer(panel, "export_chunk", {session_id: session.session_id, index}, session.owner);
      if (chunk.session_id !== session.session_id || chunk.index !== index || chunk.offset !== received || !Number.isInteger(chunk.bytes) || chunk.bytes < 1) {
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
    if (session?.session_id) {
      try {
        await callBackupTransfer(panel, "export_cancel", {session_id: session.session_id}, session.owner);
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

export async function uploadFullBackup(panel, file, isCurrent = () => true, owner = selectedAgent(panel)) {
  owner = Object.freeze({entry_id: owner.entry_id, subentry_id: owner.subentry_id});
  let sessionId = null;
  try {
    const manifest = await callBackupTransfer(panel, "import_start", {
      filename: file.name || "backup",
      size: file.size,
    }, owner);
    sessionId = manifest.session_id;
    if (sessionId) {
      panel._backupTransferUploads ||= new Map();
      panel._backupTransferUploads.set(sessionId, transferSession(sessionId, owner));
    }
    if (!sessionId || !Number.isInteger(manifest.chunk_size) || manifest.chunk_size < 1 || !Number.isInteger(manifest.chunk_count) || manifest.chunk_count < 1) {
      throw new Error("The backup server returned an invalid upload manifest");
    }
    if (!isCurrent()) throw new Error("The selected backup was superseded");
    let sent = 0;
    for (let index = 0; index < manifest.chunk_count; index += 1) {
      if (!isCurrent()) throw new Error("The selected backup was superseded");
      const end = Math.min(file.size, sent + manifest.chunk_size);
      const bytes = new Uint8Array(await file.slice(sent, end).arrayBuffer());
      if (!bytes.byteLength) throw new Error("The selected transfer file ended unexpectedly");
      const result = await callBackupTransfer(panel, "import_chunk", {
        session_id: sessionId,
        index,
        data: bytesToBase64(bytes),
      }, owner);
      if (!isCurrent()) throw new Error("The selected backup was superseded");
      sent += bytes.byteLength;
      if (result.session_id !== sessionId || result.next_index !== index + 1 || result.received !== sent) {
        throw new Error("The backup server rejected the upload sequence");
      }
    }
    if (sent !== file.size) throw new Error("The selected transfer file was not uploaded completely");
    const inspection = await callBackupTransfer(panel, "import_inspect", {session_id: sessionId, inspect_only: true}, owner);
    if (!isCurrent()) throw new Error("The selected backup was superseded");
    if (!inspection?.valid) throw new Error("The transfer could not be validated");
    return {...inspection, session_id: sessionId, session: transferSession(sessionId, owner)};
  } catch (err) {
    if (sessionId) {
      try {
        await callBackupTransfer(panel, "import_cancel", {session_id: sessionId}, owner);
      } catch (_cleanupErr) {
        // Server-side expiry is the final cleanup fallback.
      }
    }
    throw err;
  } finally {
    if (sessionId) panel._backupTransferUploads?.delete(sessionId);
  }
}

function cancelUploadingBackups(panel) {
  for (const session of panel?._backupTransferUploads?.values() || []) {
    // The upload's own catch also attempts cleanup if its current request settles
    // after this cancellation. Both paths are safe if the server already removed it.
    void cancelBackupImportSession(panel, session);
  }
}

export async function cancelBackupImport(panel, session = panel?._backupTransferSession) {
  if (!session) return;
  if (panel?._backupTransferSession === session) {
    invalidateImportPreview(panel);
    panel._backupTransferSession = null;
  }
  await cancelBackupImportSession(panel, session);
}

async function cancelBackupImportSession(panel, session) {
  if (!session?.session_id) return;
  try {
    await callBackupTransfer(panel, "import_cancel", {session_id: session.session_id}, session.owner);
  } catch (_err) {
    // Closing the dialog should remain reliable; the server expires the session.
  }
}

export function openBackupPicker(panel, input) {
  if (panel) panel._backupImportGeneration = (panel._backupImportGeneration || 0) + 1;
  cancelUploadingBackups(panel);
  invalidateImportPreview(panel);
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

export function renderRestoreTransferDialog(panel) {
  const dirtyWarning = panel?._configDirty
    ? '<p class="inline-error">Your unsaved configuration changes will be discarded if configuration is one of the restored sections.</p>'
    : "";
  return `<dialog id="restore-dialog" class="editor-dialog" aria-labelledby="restore-dialog-title"><div class="dialog-header"><h2 id="restore-dialog-title">Import / Restore</h2></div><div class="dialog-body">${restoreScopeMarkup(panel)}<div><strong id="restore-backup-name"></strong><p id="restore-backup-meta" class="meta"></p></div><ul id="restore-summary" class="restore-summary"></ul><fieldset class="setting-group"><legend><strong>Sections to replace</strong></legend><p class="help">Only sections contained in this file are shown. Unselected destination sections remain unchanged.</p><div id="${RESTORE_SECTIONS_ID}" class="group-function-choices"></div></fieldset><fieldset class="setting-group" id="${RESTORE_USER_MAPPINGS_ID}" hidden><legend><strong>Map Home Assistant users</strong></legend><p class="help">This backup contains personal data owned by users that do not exist on this Home Assistant installation. Choose the destination owner for each source user before restoring.</p><div data-user-scope-mapping-list></div></fieldset><div id="${RESTORE_STATUS_ID}" class="validation" role="status" aria-live="polite"></div><div class="notice"><strong>Replacement, not merge</strong><p>Every selected section replaces that section on the current agent. The combined target is validated first, including Request Rule references to Function Tools. A final confirmation is required before applying it.</p></div>${dirtyWarning}</div><div class="dialog-actions"><button type="button" class="secondary" id="${CANCEL_ID}">Cancel</button><button type="button" class="danger" id="${APPLY_ID}" disabled>Restore selected sections</button></div></dialog>`;
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

export function restorePreviewSummaryLines(preview = {}) {
  const count = (key) => Number(preview[key] || 0);
  const sections = new Set(preview.selected_sections || []);
  const lines = [
    ["configuration", "Agent configuration and Function Tools"],
    ["request_rules", `${count("request_rules")} Request Rules`],
    ["persistent_memory", `${count("persistent_memories")} persistent memories`],
    ["temporary_memory", `${count("temporary_memories")} active temporary memories`],
    ["knowledge", `${count("knowledge_sources")} Knowledge sources`],
    ["conversation_archive", `${count("archive_sessions")} archived conversations (${count("archive_turns")} turns)`],
    ["usage", `Usage history (${count("usage_runs")} runs, ${count("usage_requests")} requests)`],
    ["guest_mode", `Guest Mode schedule ${preview.guest_mode_scheduled ? "included" : "inactive"}`],
  ];
  return lines.filter(([section]) => sections.has(section)).map(([, line]) => line);
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

function selectedUserScopeMappings(root) {
  const result = {};
  const host = root?.querySelector?.(`#${RESTORE_USER_MAPPINGS_ID}`);
  host?.querySelectorAll?.("[data-user-scope-source]").forEach((control) => {
    const source = control.dataset.userScopeSource;
    if (source && control.value) result[source] = control.value;
  });
  return result;
}

function updateUserScopeMappingUI(panel, preview) {
  const root = panel.shadowRoot;
  const fieldset = root?.querySelector(`#${RESTORE_USER_MAPPINGS_ID}`);
  const list = fieldset?.querySelector("[data-user-scope-mapping-list]");
  if (!fieldset || !list) return 0;
  const plan = preview?.user_scope_mapping || {};
  const required = Array.isArray(plan.required_source_user_ids) ? plan.required_source_user_ids : [];
  if (!required.length) {
    fieldset.hidden = true;
    list.innerHTML = "";
    return 0;
  }
  const previous = selectedUserScopeMappings(root);
  const resolved = plan.resolved && typeof plan.resolved === "object" ? plan.resolved : {};
  const users = Array.isArray(plan.destination_users) ? plan.destination_users : [];
  fieldset.hidden = false;
  list.innerHTML = required.map((source) => {
    const selected = resolved[source] || previous[source] || "";
    const options = users.map((user) => {
      const id = String(user?.user_id || "");
      const label = String(user?.name || id);
      return `<option value="${panel._e(id)}" ${id === selected ? "selected" : ""}>${panel._e(label)}</option>`;
    }).join("");
    return `<label class="setting"><span class="setting-copy"><strong>Source user ${panel._e(source)}</strong><small>Choose who should own this restored personal data.</small></span><select data-user-scope-source="${panel._e(source)}"><option value="">Choose a destination user…</option>${options}</select></label>`;
  }).join("");
  list.querySelectorAll("[data-user-scope-source]").forEach((control) => {
    control.addEventListener("change", () => void refreshImportPreview(panel));
  });
  return Array.isArray(plan.missing_source_user_ids) ? plan.missing_source_user_ids.length : required.filter((source) => !(resolved[source] || previous[source])).length;
}

function updateRestorePreviewStatus(panel, preview) {
  const missingUsers = updateUserScopeMappingUI(panel, preview);
  const status = panel.shadowRoot?.querySelector(`#${RESTORE_STATUS_ID}`);
  if (missingUsers) {
    if (status) {
      status.className = "validation error";
      status.textContent = `Map ${missingUsers} source Home Assistant user${missingUsers === 1 ? "" : "s"} before restoring.`;
    }
    return missingUsers;
  }
  updateSensitiveStatus(panel, preview);
  return 0;
}

function previewWork(panel) {
  return panel._backupTransferPreviewWork ||= {timer: null, inFlight: null, latest: null};
}

function retireBackupImports(panel) {
  panel._backupImportGeneration = (panel._backupImportGeneration || 0) + 1;
  cancelUploadingBackups(panel);
  invalidateImportPreview(panel);
  void cancelBackupImport(panel);
  panel._backupTransferInspection = null;
  const dialog = panel.shadowRoot?.querySelector("#restore-dialog");
  if (dialog) dialog.__eocBackupSession = null;
  if (dialog?.open) dialog.close();
}

function invalidateImportPreview(panel) {
  if (!panel) return 0;
  const work = previewWork(panel);
  if (work.timer !== null) clearTimeout(work.timer);
  work.timer = null;
  work.latest = null;
  panel._backupTransferPreview = null;
  panel._backupTransferPreviewToken = null;
  const summary = panel.shadowRoot?.querySelector("#restore-summary");
  if (summary) summary.innerHTML = "";
  const apply = panel.shadowRoot?.querySelector(`#${APPLY_ID}`);
  if (apply) apply.disabled = true;
  return panel._transferPreviewToken = (panel._transferPreviewToken || 0) + 1;
}

function sendLatestImportPreview(panel) {
  const work = previewWork(panel);
  if (work.timer !== null || !work.latest) return;
  if (work.inFlight?.session === work.latest.session) return;
  const request = work.latest;
  work.latest = null;
  const current = () => panel._backupTransferSession === request.session
    && ownsTransfer(panel, request.session) && panel._transferPreviewToken === request.token;
  if (!current()) return;
  work.inFlight = request;
  void (async () => {
    const status = panel.shadowRoot?.querySelector(`#${RESTORE_STATUS_ID}`);
    const apply = panel.shadowRoot?.querySelector(`#${APPLY_ID}`);
    try {
      const result = await callBackupTransfer(panel, "import_inspect", {session_id: request.session.session_id, sections: request.sections, user_scope_mappings: request.userScopeMappings}, request.session.owner);
      if (!current()) return;
      panel._backupTransferPreview = result.preview;
      panel._backupTransferPreviewToken = result.preview_token;
      const summary = panel.shadowRoot?.querySelector("#restore-summary");
      if (summary) {
        summary.innerHTML = restorePreviewSummaryLines(result.preview)
          .map((line) => `<li>${panel._e(line)}</li>`).join("");
      }
      const missingUsers = updateRestorePreviewStatus(panel, result.preview);
      if (apply) apply.disabled = result.preview_token === null || missingUsers > 0;
    } catch (err) {
      if (!current()) return;
      panel._backupTransferPreview = null;
      if (status) {
        status.className = "validation error";
        status.textContent = err.message || String(err);
      }
      if (apply) apply.disabled = true;
    } finally {
      if (work.inFlight === request) work.inFlight = null;
      sendLatestImportPreview(panel);
    }
  })();
}

export function refreshImportPreview(panel) {
  const root = panel.shadowRoot;
  const token = invalidateImportPreview(panel);
  const session = panel._backupTransferSession;
  if (!session) return false;
  if (!ownsTransfer(panel, session)) { void cancelBackupImport(panel, session); return false; }
  const sections = selectedValues(root, ".transfer-restore-section");
  const status = root.querySelector(`#${RESTORE_STATUS_ID}`);
  if (!sections.length) {
    if (status) {
      status.className = "validation error";
      status.textContent = "Select at least one section to restore.";
    }
    return false;
  }
  if (status) {
    status.className = "validation";
    status.textContent = "Validating selected sections…";
  }
  const work = previewWork(panel);
  work.latest = {session, sections, token, userScopeMappings: selectedUserScopeMappings(root)};
  work.timer = setTimeout(() => {
    work.timer = null;
    sendLatestImportPreview(panel);
  }, IMPORT_PREVIEW_DEBOUNCE_MS);
  return true;
}

export function bindBackupTransfer(panel) {
  const root = panel?.shadowRoot;
  if (!root) return;
  panel._eocBackupTransferCleanup = () => retireBackupImports(panel);
  const restoreDialog = root.querySelector("#restore-dialog");
  if (restoreDialog && !restoreDialog.__eocBackupBound) {
    restoreDialog.__eocBackupBound = true;
    const dismiss = () => {
      const session = restoreDialog.__eocBackupSession;
      restoreDialog.__eocBackupSession = null;
      if (session && panel._backupTransferSession === session) {
        panel._backupTransferInspection = null;
        void cancelBackupImport(panel, session);
      }
    };
    restoreDialog.addEventListener("cancel", dismiss);
    restoreDialog.addEventListener("close", () => { if (!restoreDialog.open) dismiss(); });
  }

  const exportMode = root.querySelector(`#${EXPORT_MODE_ID}`);
  const customOptions = root.querySelector(`#${CUSTOM_OPTIONS_ID}`);
  const syncExportMode = () => {
    if (customOptions) customOptions.hidden = exportMode?.value !== "custom";
  };
  exportMode?.addEventListener("change", syncExportMode);
  syncExportMode();

  root.querySelector(`#${CREATE_ID}`)?.addEventListener("click", async () => {
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
    const generation = (panel._backupImportGeneration || 0) + 1;
    panel._backupImportGeneration = generation;
    const owner = selectedAgent(panel);
    const ownerView = panel._viewKey?.();
    const isCurrent = () => panel._backupImportGeneration === generation
      && panel._selectedAgent?.()?.entry_id === owner.entry_id
      && panel._selectedAgent?.()?.subentry_id === owner.subentry_id
      && panel._viewKey?.() === ownerView;
    const apply = root.querySelector(`#${APPLY_ID}`);
    apply.disabled = true;
    const previousSession = panel._backupTransferSession;
    panel._backupTransferSession = null;
    invalidateImportPreview(panel);
    if (previousSession) void cancelBackupImport(panel, previousSession);
    cancelUploadingBackups(panel);
    try {
      const result = await uploadFullBackup(panel, file, isCurrent, owner);
      if (!isCurrent()) {
        await cancelBackupImportSession(panel, result.session);
        return;
      }
      panel._backupTransferSession = result.session;
      panel._backupTransferInspection = result;
      panel._backupTransferPreview = result.preview;
      panel._backupTransferPreviewToken = result.preview_token;
      root.querySelector("#restore-backup-name").textContent = result.title;
      const created = result.summary?.created_at ? new Date(result.summary.created_at).toLocaleString() : "legacy export";
      const version = result.summary?.integration_version ? ` · integration ${result.summary.integration_version}` : "";
      root.querySelector("#restore-backup-meta").textContent = `${formatSourceKind(result.source_kind)} · ${created}${version}`;
      // Header-only inspection has not decoded section contents yet. Do not
      // present its placeholder zeroes as the contents of a destructive restore.
      root.querySelector("#restore-summary").innerHTML = "";
      const sectionRoot = root.querySelector(`#${RESTORE_SECTIONS_ID}`);
      sectionRoot.innerHTML = sectionChoices(panel, {className: "transfer-restore-section", available: result.available_sections});
      sectionRoot.querySelectorAll(".transfer-restore-section").forEach((input) => input.addEventListener("change", () => void refreshImportPreview(panel)));
      const missingUsers = updateRestorePreviewStatus(panel, result.preview);
      apply.disabled = result.preview_token === null || missingUsers > 0;
      root.querySelector("#restore-dialog").__eocBackupSession = result.session;
      root.querySelector("#restore-dialog").showModal();
      refreshImportPreview(panel);
    } catch (err) {
      if (!isCurrent()) return;
      void cancelBackupImport(panel);
      panel._backupTransferSession = null;
      panel._backupTransferInspection = null;
      panel._backupTransferPreview = null;
      panel._backupTransferPreviewToken = null;
      apply.disabled = true;
      panel._toast(err.message || String(err), true);
    }
  });

  root.querySelector(`#${CANCEL_ID}`)?.addEventListener("click", () => retireBackupImports(panel));

  root.querySelector(`#${APPLY_ID}`)?.addEventListener("click", async () => {
    const session = panel._backupTransferSession;
    if (!session) return;
    if (!ownsTransfer(panel, session)) { void cancelBackupImport(panel, session); return; }
    const generation = panel._backupImportGeneration || 0;
    const ownerView = panel._viewKey?.();
    const ownsUI = () => ownsTransfer(panel, session)
      && (panel._backupImportGeneration || 0) === generation && panel._viewKey?.() === ownerView;
    const sections = selectedValues(root, ".transfer-restore-section");
    if (!sections.length) return;
    const labels = sections.map((key) => SECTION_LABELS[key] || key).join(", ");
    const previewToken = panel._transferPreviewToken;
    const serverPreviewToken = panel._backupTransferPreviewToken;
    if (!serverPreviewToken) return;
    if (typeof panel._confirm === "function") {
      const losesDraft = panel._configDirty && panel._draftAgentId === panel._agentId && sections.includes("configuration");
      const confirmed = await panel._confirm(
        "Restore selected sections?",
        `This will replace, not merge, the following sections on the current agent: ${labels}.${losesDraft ? " Your unsaved configuration changes will be replaced and lost." : ""}`,
        "Restore",
      );
      if (!confirmed) return;
    }
    if (panel._backupTransferSession !== session || !ownsUI() || panel._transferPreviewToken !== previewToken || root.querySelector(`#${APPLY_ID}`)?.disabled) return;
    const button = root.querySelector(`#${APPLY_ID}`);
    let mustReselectFile = false;
    panel._setSaving(button, true);
    try {
      await callBackupTransfer(panel, "import_restore", {session_id: session.session_id, sections, preview_token: serverPreviewToken}, session.owner);
      if (panel._backupTransferSession !== session || !ownsUI()) return;
      panel._backupTransferSession = null;
      panel._backupTransferInspection = null;
      panel._backupTransferPreview = null;
      panel._backupTransferPreviewToken = null;
      root.querySelector("#restore-dialog").close();
      if (sections.includes("configuration")) panel._clearConfigDraft();
      await panel._loadAgents(panel._agentId);
      if (ownsUI()) panel._toast("Selected sections restored");
    } catch (err) {
      if (panel._backupTransferSession !== session || !ownsUI()) return;
      void cancelBackupImportSession(panel, session);
      panel._backupTransferSession = null;
      mustReselectFile = true;
      button.disabled = true;
      panel._toast(`Unable to restore: ${err.message || String(err)} Re-select the transfer file to retry.`, true);
    } finally {
      if (ownsUI() && mustReselectFile) {
        button.disabled = true;
        button.textContent = button.dataset.label || "Restore selected sections";
      } else if (ownsUI()) {
        panel._setSaving(button, false);
      }
    }
  });
}
