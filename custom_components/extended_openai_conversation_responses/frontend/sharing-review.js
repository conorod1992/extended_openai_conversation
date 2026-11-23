import {captureAuthentication} from "./authentication-presentation.js";
export const SHARING_CONTRACT = "New shareable exports include supported public settings and omit provider credentials, private data and custom content.";

export function sharingSummary(security = {}) {
  const count = (security.omitted || []).reduce((total, item) => total + (Number.isSafeInteger(item.count) && item.count > 0 ? item.count : 0), 0);
  const omitted = `${count} configuration items or custom units omitted.`;
  return security.review_required
    ? `${omitted} This older export can include custom content. Review it before restoring or sharing it again.`
    : omitted;
}

function modal(panel, markup, bind) {
  const current = captureAuthentication(panel);
  if (!current()) return Promise.resolve(false);
  const dialog = document.createElement("dialog");
  dialog.className = "editor-dialog";
  dialog.setAttribute("aria-label", "Review sharing export");
  dialog.innerHTML = markup;
  (panel.shadowRoot || document.body).append(dialog);
  return new Promise((resolve) => {
    let done = false;
    const finish = (value) => {
      if (done) return;
      done = true;
      panel.removeEventListener?.("eoc-authentication-retired", retired);
      dialog.close(); dialog.remove(); resolve(current() && value);
    };
    const retired = () => finish(false);
    panel.addEventListener?.("eoc-authentication-retired", retired);
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); finish(false); });
    dialog.addEventListener("close", () => finish(false));
    bind(dialog, finish);
    dialog.showModal();
  });
}

export function restrictedSharingSecurity(result) {
  const security = result.security || result.document?.security;
  if (security?.mode !== "restricted" || security.review_required) {
    throw new Error("The server did not return a restricted export. Refresh the panel and try again.");
  }
  return security;
}

export function reviewSharingExport(panel, result) {
  const security = restrictedSharingSecurity(result);
  return modal(panel, `<div class="dialog-header"><h2>Review sharing export</h2></div><div class="dialog-body"><p data-contract></p><p data-summary role="status"></p><label>Exact export contents<textarea data-artifact readonly spellcheck="false" style="width:100%;min-height:18rem"></textarea></label></div><div class="dialog-actions"><button type="button" class="secondary" data-cancel>Cancel</button><button type="button" data-download>Download export</button></div>`, (dialog, finish) => {
    dialog.querySelector("[data-contract]").textContent = SHARING_CONTRACT;
    dialog.querySelector("[data-summary]").textContent = sharingSummary(security);
    dialog.querySelector("[data-artifact]").value = result.json;
    const download = dialog.querySelector("[data-download]");
    dialog.querySelector("[data-cancel]").addEventListener("click", () => finish(false));
    download.addEventListener("click", () => finish(true));
  });
}

export async function downloadConfigurationSharing(panel) {
  const current = captureAuthentication(panel);
  if (!current()) return;
  const result = await panel._call("configuration", "export");
  if (!current() || !await reviewSharingExport(panel, result) || !current()) return;
  const url = URL.createObjectURL(new Blob([result.json], {type: "application/json"}));
  try {
    const link = document.createElement("a");
    link.href = url; link.download = "eoai-shareable-configuration.json"; link.click();
  } finally { setTimeout(() => URL.revokeObjectURL(url), 0); }
}
