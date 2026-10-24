export const SHARING_CONTRACT = "Shareable exports exclude designated credentials and unclassified custom content by default, and check included content for recognized secret patterns. Custom content you choose to include may still contain secrets. Review the summary and file before sharing, especially Function Tools, scripts and templates.";

export function sharingSummary(security = {}) {
  const count = (security.omitted || []).reduce((total, item) => total + (Number.isSafeInteger(item.count) && item.count > 0 ? item.count : 0), 0);
  return `${count} configuration items or custom units omitted. Omitted tools and rules are not executable in the imported setup. ${security.review_required ? "Included custom content requires your review; arbitrary secrets may remain." : "Restricted sharing excludes unreviewed custom content."}`;
}

function modal(panel, markup, bind) {
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
      dialog.close(); dialog.remove(); resolve(value);
    };
    dialog.addEventListener("cancel", (event) => { event.preventDefault(); finish(false); });
    dialog.addEventListener("close", () => finish(false));
    bind(dialog, finish);
    dialog.showModal();
  });
}

export function reviewSharingExport(panel, result) {
  return modal(panel, `<div class="dialog-header"><h2>Review sharing export</h2></div><div class="dialog-body"><p data-contract></p><p data-summary role="status"></p><label>Exact export contents<textarea data-artifact readonly spellcheck="false" style="width:100%;min-height:18rem"></textarea></label><label data-ack-row><input type="checkbox" data-ack> I reviewed included Function Tools, scripts, templates and free-form content for secrets.</label></div><div class="dialog-actions"><button type="button" class="secondary" data-cancel>Cancel</button><button type="button" data-download>Download export</button></div>`, (dialog, finish) => {
    const security = result.security || result.document?.security || {};
    dialog.querySelector("[data-contract]").textContent = SHARING_CONTRACT;
    dialog.querySelector("[data-summary]").textContent = sharingSummary(security);
    dialog.querySelector("[data-artifact]").value = result.json;
    const ack = dialog.querySelector("[data-ack]");
    const download = dialog.querySelector("[data-download]");
    dialog.querySelector("[data-ack-row]").hidden = !security.review_required;
    download.disabled = Boolean(security.review_required);
    ack.addEventListener("change", () => { download.disabled = Boolean(security.review_required) && !ack.checked; });
    dialog.querySelector("[data-cancel]").addEventListener("click", () => finish(false));
    download.addEventListener("click", () => { if (!download.disabled) finish(true); });
  });
}

export async function downloadConfigurationSharing(panel) {
  const choice = await modal(panel, `<div class="dialog-header"><h2>Export saved configuration</h2></div><div class="dialog-body"><p>Restricted sharing omits custom content. Reviewed sharing can include custom tools and templates; you must inspect the resulting file for secrets. Saved configuration is unchanged.</p></div><div class="dialog-actions"><button type="button" data-cancel>Cancel</button><button type="button" data-restricted>Restricted setup</button><button type="button" data-custom>Include custom content</button></div>`, (dialog, finish) => {
    dialog.querySelector("[data-cancel]").addEventListener("click", () => finish(false));
    dialog.querySelector("[data-restricted]").addEventListener("click", () => finish("restricted"));
    dialog.querySelector("[data-custom]").addEventListener("click", () => finish("custom"));
  });
  if (!choice) return;
  const result = await panel._call("configuration", "export", {reviewed_custom: choice === "custom"});
  if (!await reviewSharingExport(panel, result)) return;
  const url = URL.createObjectURL(new Blob([result.json], {type: "application/json"}));
  try {
    const link = document.createElement("a");
    link.href = url; link.download = "eoai-shareable-configuration.json"; link.click();
  } finally { setTimeout(() => URL.revokeObjectURL(url), 0); }
}
