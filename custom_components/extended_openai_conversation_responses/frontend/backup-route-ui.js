import {updateDialogs} from "./management-dialogs.js";
import {renderBackupTransferPanel} from "./backup-transfer-shell.js";

export {renderBackupTransferPanel};

export function renderRestoreTransferDialog(panel) {
  return panel._backupTransferModule?.renderRestoreTransferDialog(panel) || "";
}

async function activateTransfer(panel) {
  const agentId = panel._agentId;
  const transfer = await import("./backup-transfer-ui.js");
  if (panel._viewKey() !== "usage-maintenance/backup-restore" || panel._agentId !== agentId) return false;
  panel._backupTransferModule = transfer;
  panel._backupSummaryLines = transfer.backupSummaryLines;
  const markup = panel._dialogs();
  updateDialogs(panel, markup);
  panel._eocDialogMarkup = markup;
  transfer.bindBackupTransfer(panel, panel._backupSummaryLines);
  return true;
}

export function bindBackupTransfer(panel) {
  if (panel._backupTransferModule) {
    panel._backupTransferModule.bindBackupTransfer(panel, panel._backupSummaryLines);
    return;
  }
  const root = panel.shadowRoot;
  const exportMode = root.querySelector("#transfer-export-mode");
  const customOptions = root.querySelector("#transfer-custom-options");
  exportMode?.addEventListener("change", () => { customOptions.hidden = exportMode.value !== "custom"; });
  const create = root.querySelector("#create-backup-transfer");
  create?.addEventListener("click", async () => {
    if (panel._backupTransferModule || panel._backupTransferStarting) return;
    panel._backupTransferStarting = true;
    try {
      if (await activateTransfer(panel)) create.click();
    } catch (err) {
      panel._toast(`Unable to load backup controls: ${err.message || String(err)}`, true);
    } finally { panel._backupTransferStarting = false; }
  });
  const input = root.querySelector("#backup-file-transfer");
  root.querySelector("#restore-backup-transfer")?.addEventListener("click", () => {
    if (panel._backupTransferModule) return;
    input.value = "";
    input.click();
  });
  input?.addEventListener("change", async () => {
    if (panel._backupTransferModule || panel._backupTransferStarting || !input.files?.length) return;
    panel._backupTransferStarting = true;
    try {
      if (await activateTransfer(panel)) input.dispatchEvent(new Event("change", {bubbles: true}));
    } catch (err) {
      panel._toast(`Unable to load restore controls: ${err.message || String(err)}`, true);
    } finally { panel._backupTransferStarting = false; }
  });
}
