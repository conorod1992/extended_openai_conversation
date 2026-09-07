export const WS_BACKUP_TRANSFER = "extended_openai_conversation_responses/management/backup_transfer";

const CREATE_ID = "create-backup-transfer";
const RESTORE_ID = "restore-backup-transfer";
const FILE_ID = "backup-file-transfer";
const CANCEL_ID = "restore-transfer-cancel";
const APPLY_ID = "restore-transfer-apply";

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
    link.download = filename || "conversation-agent-full-backup.zip";
    link.click();
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}

export async function downloadFullBackup(panel, saveBlob = defaultSaveBlob) {
  let sessionId = null;
  try {
    const manifest = await callBackupTransfer(panel, "export_start");
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
        // The server also expires abandoned sessions; a failed cleanup must not
        // invalidate a backup that the browser has already received completely.
      }
    }
  }
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
      if (!bytes.byteLength) throw new Error("The selected backup file ended unexpectedly");
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
    if (sent !== file.size) throw new Error("The selected backup file was not uploaded completely");
    const inspection = await callBackupTransfer(panel, "import_inspect", {session_id: sessionId});
    if (!inspection?.valid) throw new Error("The backup could not be validated");
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

function decorateBackupMarkupDom(html) {
  const template = document.createElement("template");
  template.innerHTML = html;
  const root = template.content;
  const create = root.querySelector("#create-backup");
  const restore = root.querySelector("#restore-backup");
  const file = root.querySelector("#backup-file");
  if (create) create.id = CREATE_ID;
  if (restore) restore.id = RESTORE_ID;
  if (file) {
    file.id = FILE_ID;
    file.setAttribute("accept", "application/zip,.zip,application/json,.json");
  }
  const actions = root.querySelector("#config-backup .backup-actions");
  if (actions && !root.querySelector(".backup-transfer-help")) {
    actions.insertAdjacentHTML("afterend", '<small class="backup-transfer-help">Full backups download as compressed ZIP archives. Existing JSON full backups remain supported for restore.</small>');
  }
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
  return String(html)
    .replace('id="restore-cancel"', `id="${CANCEL_ID}"`)
    .replace('id="restore-apply"', `id="${APPLY_ID}"`);
}

export function bindBackupTransfer(panel, summaryFormatter = () => []) {
  const root = panel?.shadowRoot;
  if (!root) return;

  root.querySelector(`#${CREATE_ID}`)?.addEventListener("click", async () => {
    if (panel._configDirty) return;
    const button = root.querySelector(`#${CREATE_ID}`);
    panel._setSaving(button, true);
    try {
      await downloadFullBackup(panel);
      panel._toast("Full backup created");
    } catch (err) {
      panel._toast(`Unable to create backup: ${err.message || String(err)}`, true);
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
      root.querySelector("#restore-backup-name").textContent = result.title;
      root.querySelector("#restore-backup-meta").textContent = `Created ${new Date(result.summary.created_at).toLocaleString()} with integration ${result.summary.integration_version}`;
      root.querySelector("#restore-summary").innerHTML = summaryFormatter(result.summary).map((line) => `<li>${panel._e(line)}</li>`).join("");
      apply.disabled = false;
      root.querySelector("#restore-dialog").showModal();
    } catch (err) {
      panel._backupTransferSession = null;
      apply.disabled = true;
      panel._toast(err.message || String(err), true);
    }
  });

  root.querySelector(`#${CANCEL_ID}`)?.addEventListener("click", async () => {
    await cancelBackupImport(panel);
    root.querySelector("#restore-dialog").close();
  });

  root.querySelector(`#${APPLY_ID}`)?.addEventListener("click", async () => {
    const sessionId = panel._backupTransferSession;
    if (!sessionId) return;
    const button = root.querySelector(`#${APPLY_ID}`);
    panel._setSaving(button, true);
    try {
      await callBackupTransfer(panel, "import_restore", {session_id: sessionId});
      panel._backupTransferSession = null;
      root.querySelector("#restore-dialog").close();
      panel._clearConfigDraft();
      await panel._loadAgents(panel._agentId);
      panel._toast("Full backup restored");
    } catch (err) {
      panel._backupTransferSession = null;
      button.disabled = true;
      panel._toast(`Unable to restore backup: ${err.message || String(err)} Re-select the backup file to retry.`, true);
    } finally {
      panel._setSaving(button, false);
    }
  });
}
