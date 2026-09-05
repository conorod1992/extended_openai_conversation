const PATCHED = Symbol.for("extended-openai.management-provider-credentials");

export function providerCredentialScope(agent = {}) {
  const entryTitle = String(agent.entry_title || "").trim();
  if (entryTitle) {
    return `This credential belongs to “${entryTitle}” and is shared by its Conversation and AI Task agents.`;
  }
  return "This credential belongs to the parent provider connection and is shared by its Conversation and AI Task agents.";
}

export function diagnosticsAuthenticationRejected(result = {}) {
  if (result?.authentication_rejected === true) return true;
  const checks = Array.isArray(result.checks) ? result.checks : [];
  return checks.some((check) =>
    check?.name === "Model access"
    && check?.status === "Failed"
    && check?.message === "Authentication rejected"
  );
}

function providerLabel(value) {
  const normalized = String(value || "Provider").trim();
  if (normalized.toLowerCase() === "openai") return "OpenAI";
  if (normalized.toLowerCase() === "azure") return "Azure OpenAI";
  return normalized || "Provider";
}

function ensureStyles(panel) {
  const root = panel.shadowRoot;
  if (!root || root.querySelector("style[data-eoc-provider-credentials]")) return;
  const style = document.createElement("style");
  style.dataset.eocProviderCredentials = "";
  style.textContent = `
    .eoc-provider-credential-row{display:flex;align-items:center;justify-content:space-between;gap:18px;margin-top:14px;padding:13px 0;border-top:1px solid var(--divider-color)}
    .eoc-provider-credential-row span{display:grid;gap:3px;min-width:0}.eoc-provider-credential-row small,.eoc-provider-scope{color:var(--secondary-text-color);line-height:1.45}
    .eoc-provider-scope{margin:12px 0 0}.eoc-auth-recovery{margin-top:14px}.eoc-auth-recovery .section-actions{margin-top:10px}
    .eoc-api-key-input{width:100%;box-sizing:border-box}.eoc-api-key-dialog-copy{margin-top:0}.eoc-api-key-error:empty{display:none}
    @media (max-width:600px){.eoc-provider-credential-row{align-items:flex-start;flex-direction:column}.eoc-provider-credential-row button{width:100%}}
  `;
  root.append(style);
}

function dialogBusy(dialog) {
  return dialog?.dataset.eocBusy === "true";
}

function setDialogBusy(dialog, busy) {
  if (!dialog) return;
  if (busy) {
    dialog.dataset.eocBusy = "true";
    dialog.setAttribute("aria-busy", "true");
  } else {
    delete dialog.dataset.eocBusy;
    dialog.removeAttribute("aria-busy");
  }
  for (const id of ["#eoc-api-key-close", "#eoc-api-key-cancel", "#eoc-api-key-save"]) {
    const control = dialog.querySelector(id);
    if (control) control.disabled = busy;
  }
}

function openApiKeyDialog(panel) {
  const dialog = panel.shadowRoot?.querySelector("#eoc-api-key-dialog");
  const input = dialog?.querySelector("#eoc-new-api-key");
  const error = dialog?.querySelector("#eoc-api-key-error");
  if (!dialog || !input || !error || dialogBusy(dialog)) return;
  input.value = "";
  error.textContent = "";
  dialog.showModal();
  requestAnimationFrame(() => input.focus());
}

function closeApiKeyDialog(panel) {
  const dialog = panel.shadowRoot?.querySelector("#eoc-api-key-dialog");
  if (!dialog || dialogBusy(dialog)) return;
  const input = dialog.querySelector("#eoc-new-api-key");
  if (input) input.value = "";
  dialog.close();
}

function ensureApiKeyDialog(panel, agent) {
  const root = panel.shadowRoot;
  if (!root || root.querySelector("#eoc-api-key-dialog")) return;
  const dialog = document.createElement("dialog");
  dialog.id = "eoc-api-key-dialog";
  dialog.className = "editor-dialog";
  dialog.setAttribute("aria-labelledby", "eoc-api-key-dialog-title");
  dialog.innerHTML = `
    <form id="eoc-api-key-form">
      <div class="dialog-header"><h2 id="eoc-api-key-dialog-title">Replace API key</h2><button type="button" class="icon" id="eoc-api-key-close" aria-label="Close">×</button></div>
      <div class="dialog-body">
        <p class="eoc-api-key-dialog-copy">Enter the replacement API key for ${panel._e(providerLabel(agent?.provider))}. Existing provider and endpoint settings are kept.</p>
        <p class="eoc-provider-scope"><strong>Affects this provider connection:</strong> ${panel._e(providerCredentialScope(agent))}</p>
        <label>New API key<input id="eoc-new-api-key" class="eoc-api-key-input" type="password" autocomplete="off" autocapitalize="none" autocorrect="off" spellcheck="false" required></label>
        <small>The saved key is never displayed here. Extended OpenAI validates the replacement before saving it unless this provider connection is explicitly configured to skip authentication.</small>
        <div id="eoc-api-key-error" class="inline-error eoc-api-key-error" role="alert"></div>
      </div>
      <div class="dialog-actions"><button type="button" class="secondary" id="eoc-api-key-cancel">Cancel</button><button type="submit" id="eoc-api-key-save">Validate & replace</button></div>
    </form>`;
  const dialogHost = root.querySelector("#eoc-dialog-host") || root;
  dialogHost.append(dialog);

  dialog.querySelector("#eoc-api-key-close")?.addEventListener("click", () => closeApiKeyDialog(panel));
  dialog.querySelector("#eoc-api-key-cancel")?.addEventListener("click", () => closeApiKeyDialog(panel));
  dialog.addEventListener("cancel", (event) => {
    event.preventDefault();
    closeApiKeyDialog(panel);
  });
  dialog.querySelector("#eoc-api-key-form")?.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (dialogBusy(dialog)) return;
    const input = dialog.querySelector("#eoc-new-api-key");
    const error = dialog.querySelector("#eoc-api-key-error");
    const save = dialog.querySelector("#eoc-api-key-save");
    if (!input || !error || !save) return;
    const apiKey = input.value || "";
    if (!apiKey.trim()) {
      error.textContent = "Enter a new API key.";
      input.focus();
      return;
    }
    error.textContent = "";
    const previousLabel = save.textContent;
    setDialogBusy(dialog, true);
    save.textContent = "Validating…";
    try {
      const result = await panel._call("diagnostics", "update_api_key", {api_key: apiKey});
      input.value = "";
      dialog.close();
      const validationNote = result?.validation_performed === false
        ? " Authentication validation is skipped for this provider connection."
        : "";
      if (result?.updated === false) {
        panel._toast(`API key unchanged.${validationNote}`);
      } else if (result?.reload_requested === false) {
        panel._toast(`API key updated.${validationNote} The provider connection was not reloaded automatically.`);
      } else {
        panel._toast(`API key updated.${validationNote} Extended OpenAI is reloading the provider connection.`);
      }
    } catch (err) {
      error.textContent = err.message || String(err);
      input.focus();
      input.select();
    } finally {
      setDialogBusy(dialog, false);
      save.textContent = previousLabel;
    }
  });
}

function ensureProviderCard(panel, agent) {
  const root = panel.shadowRoot;
  const testButton = root?.querySelector("#test-agent");
  const testCard = testButton?.closest(".content-card");
  if (!root || !testCard || root.querySelector("#eoc-provider-credentials")) return;
  const card = document.createElement("section");
  card.id = "eoc-provider-credentials";
  card.className = "content-card";
  card.innerHTML = `
    <div class="section-heading"><div><h2>Provider credentials</h2><p>Replace the API key used by this provider connection without changing the selected assistant's model or other configuration.</p></div></div>
    <div class="eoc-provider-credential-row"><span><strong>${panel._e(providerLabel(agent?.provider))}</strong><small>API key configured on the parent integration entry. The saved value is never returned to this page.</small></span><button type="button" class="secondary" id="eoc-change-api-key">Change API key</button></div>
    <p class="eoc-provider-scope">${panel._e(providerCredentialScope(agent))}</p>`;
  testCard.insertAdjacentElement("beforebegin", card);
  card.querySelector("#eoc-change-api-key")?.addEventListener("click", () => openApiKeyDialog(panel));
}

function showAuthenticationRecovery(panel) {
  const root = panel.shadowRoot;
  const output = root?.querySelector("#test-result");
  const testCard = output?.closest(".content-card");
  if (!root || !testCard || root.querySelector("#eoc-auth-recovery")) return;
  const notice = document.createElement("div");
  notice.id = "eoc-auth-recovery";
  notice.className = "notice eoc-auth-recovery";
  notice.innerHTML = `<strong>Provider authentication was rejected</strong><p>Replace the shared API key here, or complete Home Assistant's reauthentication flow if it is offered.</p><div class="section-actions"><button type="button" class="secondary" id="eoc-auth-replace-api-key">Replace API key</button></div>`;
  testCard.append(notice);
  notice.querySelector("#eoc-auth-replace-api-key")?.addEventListener("click", () => openApiKeyDialog(panel));
}

function syncAuthenticationRecovery(panel, result) {
  if (diagnosticsAuthenticationRejected(result)) {
    showAuthenticationRecovery(panel);
    return;
  }
  panel.shadowRoot?.querySelector("#eoc-auth-recovery")?.remove();
}

function stopDiagnosticsWatch(panel) {
  panel._eocProviderCredentialObserver?.disconnect?.();
  panel._eocProviderCredentialObserver = null;
}

function watchDiagnosticsResult(panel) {
  const output = panel.shadowRoot?.querySelector("#test-result");
  if (!output) return;
  stopDiagnosticsWatch(panel);
  let observer = null;
  const inspect = () => {
    if (output !== panel.shadowRoot?.querySelector("#test-result")) {
      observer?.disconnect();
      if (panel._eocProviderCredentialObserver === observer) {
        panel._eocProviderCredentialObserver = null;
      }
      return;
    }
    try {
      const result = JSON.parse(output.textContent || "");
      syncAuthenticationRecovery(panel, result);
    } catch (_) {
      // "Testing…" and plain error messages are not structured diagnostic results.
    }
  };
  observer = new MutationObserver(inspect);
  panel._eocProviderCredentialObserver = observer;
  observer.observe(output, {childList: true, characterData: true, subtree: true});
  inspect();
}

function enhanceDiagnostics(panel) {
  if (
    panel._data?.is_admin === false
    || panel._page !== "usage-maintenance"
    || panel._subsection !== "diagnostics"
  ) return;
  const agent = panel._selectedAgent?.();
  if (!agent || !panel.shadowRoot) return;
  ensureStyles(panel);
  ensureApiKeyDialog(panel, agent);
  ensureProviderCard(panel, agent);
  watchDiagnosticsResult(panel);
}

export function installManagementProviderCredentials(registry = globalThis.customElements) {
  if (typeof document === "undefined" || !registry?.whenDefined) return Promise.resolve(false);
  return registry.whenDefined("extended-openai-management-panel").then(() => {
    const constructor = registry.get("extended-openai-management-panel");
    const prototype = constructor?.prototype;
    if (!prototype || prototype[PATCHED]) return false;

    const originalRender = prototype._render;
    prototype._render = function(...args) {
      stopDiagnosticsWatch(this);
      const result = originalRender.apply(this, args);
      enhanceDiagnostics(this);
      return result;
    };

    const originalDisconnected = prototype.disconnectedCallback;
    prototype.disconnectedCallback = function(...args) {
      stopDiagnosticsWatch(this);
      return originalDisconnected?.apply(this, args);
    };

    prototype[PATCHED] = true;
    return true;
  });
}

if (typeof document !== "undefined" && typeof customElements !== "undefined") {
  void installManagementProviderCredentials();
}
