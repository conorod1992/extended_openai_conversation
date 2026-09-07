const DEBUG_VIEW = "usage-maintenance/request-debug";
const MANAGEMENT_TAG = "extended-openai-management-panel";
const DEBUG_TAG = "extended-openai-debug-panel";
const DEBUG_PROVIDER_PAGE_LIMIT = 5;
let debugPanelPromise = null;

function ensureDebugPanel() {
  if (customElements.get(DEBUG_TAG)) {
    installDebugPresentation();
    return Promise.resolve(customElements.get(DEBUG_TAG));
  }
  if (!debugPanelPromise) {
    debugPanelPromise = import("./debug-panel.js")
      .then((module) => {
        // Do not let the management route render the newly-defined element until
        // its embedded presentation/agent-selection behavior is installed.
        installDebugPresentation();
        return module;
      })
      .finally(() => { debugPanelPromise = null; });
  }
  return debugPanelPromise;
}

function sessionLabel(run) {
  const mode = run.continuity_mode;
  if (run.continuity_resumed === true) {
    if (mode === "device") return "Restored by device";
    if (mode === "user") return "Restored by user";
    return "Restored";
  }
  if (mode === "ha_default") {
    return run.incoming_conversation_id ? "HA session" : "New HA session";
  }
  if (mode === "device") return "New device session";
  if (mode === "user") return "New user session";
  return run.resolved_conversation_id ? "New session" : "—";
}

function providerPageMeta(trace) {
  return trace?.management_projection?.provider_requests || {
    offset: 0,
    limit: DEBUG_PROVIDER_PAGE_LIMIT,
    returned: trace?.provider_requests?.length || 0,
    has_more: false,
    next_offset: null,
    total: trace?.provider_requests?.length || 0,
  };
}

function providerPageLabel(trace) {
  const meta = providerPageMeta(trace);
  const offset = Math.max(0, Number(meta.offset) || 0);
  const returned = Math.max(0, Number(meta.returned) || 0);
  const total = Math.max(0, Number(meta.total) || 0);
  if (!returned) return total ? `No provider requests on this page · ${total} total` : "No provider requests";
  return `Provider requests ${offset + 1}–${offset + returned} of ${total}`;
}

function debugPageText(trace) {
  return JSON.stringify(trace || {}, null, 2);
}

function installDebugPresentation() {
  const DebugPanel = customElements.get(DEBUG_TAG);
  if (!DebugPanel || DebugPanel.name !== "ExtendedOpenAIDebugPanel"
    || DebugPanel.prototype.__managementPresentationInstalled) return;
  const prototype = DebugPanel.prototype;
  prototype.__managementPresentationInstalled = true;

  const originalLoadAgents = prototype._loadAgents;
  prototype._loadAgents = async function(...args) {
    await originalLoadAgents.apply(this, args);
    const preferred = this._managementAgentId;
    if (preferred && this._agent?.subentry_id !== preferred) {
      const match = this._agents?.find((item) => item.subentry_id === preferred);
      if (match) await this._selectAgent(preferred);
    }
  };

  prototype._continuityLabel = sessionLabel;

  prototype._getRun = async function(debugId, providerOffset = 0) {
    return await this._call("get", {
      debug_id: debugId,
      provider_offset: Math.max(0, Number(providerOffset) || 0),
      provider_limit: DEBUG_PROVIDER_PAGE_LIMIT,
    });
  };

  prototype._viewRun = async function(debugId, providerOffset = 0) {
    const dialog = this.shadowRoot.querySelector("#debug-dialog");
    const title = this.shadowRoot.querySelector("#debug-dialog-title");
    const body = this.shadowRoot.querySelector("#debug-json");
    const copy = this.shadowRoot.querySelector("#copy-debug-log");
    const previous = this.shadowRoot.querySelector("#debug-provider-previous");
    const next = this.shadowRoot.querySelector("#debug-provider-next");
    const status = this.shadowRoot.querySelector("#debug-provider-status");
    const token = (this._eocDebugRunLoadToken || 0) + 1;
    this._eocDebugRunLoadToken = token;
    this._eocDebugId = debugId;
    title.textContent = "Loading debug run…";
    body.textContent = "Loading…";
    copy.disabled = true;
    copy.dataset.text = "";
    if (previous) previous.disabled = true;
    if (next) next.disabled = true;
    if (!dialog.open) dialog.showModal();
    try {
      const result = await this._getRun(debugId, providerOffset);
      if (this._eocDebugRunLoadToken !== token || !dialog.open) return;
      const trace = result.trace || {};
      const meta = providerPageMeta(trace);
      const text = debugPageText(trace);
      this._eocDebugProviderOffset = Math.max(0, Number(meta.offset) || 0);
      this._eocDebugProviderMeta = meta;
      title.textContent = `Debug run ${debugId.slice(0, 8)}`;
      body.textContent = text;
      copy.dataset.text = text;
      copy.disabled = false;
      if (status) {
        status.textContent = `${providerPageLabel(trace)}${trace.management_projection?.truncated ? " · bounded/truncated management view" : ""}`;
      }
      if (previous) {
        previous.disabled = this._eocDebugProviderOffset === 0;
        previous.dataset.offset = String(Math.max(0, this._eocDebugProviderOffset - DEBUG_PROVIDER_PAGE_LIMIT));
      }
      if (next) {
        next.disabled = !meta.has_more;
        next.dataset.offset = String(meta.next_offset ?? (this._eocDebugProviderOffset + (Number(meta.returned) || 0)));
      }
    } catch (err) {
      if (this._eocDebugRunLoadToken !== token || !dialog.open) return;
      title.textContent = "Unable to load debug run";
      body.textContent = err.message || String(err);
      if (status) status.textContent = "";
    }
  };

  prototype._copyRun = async function(debugId) {
    try {
      const result = await this._getRun(debugId, 0);
      await this._copyText(debugPageText(result.trace));
      this._toast("First debug page copied");
    } catch (err) {
      this._toast(`Unable to copy debug page: ${err.message || String(err)}`, true);
    }
  };

  const originalRender = prototype._render;
  prototype._render = function(...args) {
    const result = originalRender.apply(this, args);
    if (!this.hasAttribute("embedded")) return result;

    this.style.minHeight = "0";
    this.style.background = "transparent";
    const main = this.shadowRoot?.querySelector("main");
    if (main) {
      main.style.maxWidth = "none";
      main.style.padding = "0";
      main.style.minHeight = "0";
    }
    const agentSelect = this.shadowRoot?.querySelector("#agent");
    const agentLabel = agentSelect?.closest("label");
    if (agentLabel) agentLabel.hidden = true;

    const headers = this.shadowRoot?.querySelectorAll("thead th");
    if (headers?.[7]) headers[7].textContent = "Session handling";
    const recentHeading = [...(this.shadowRoot?.querySelectorAll("h2") || [])]
      .find((item) => item.textContent === "Recent debug runs");
    const explanation = recentHeading?.parentElement?.querySelector("p");
    if (explanation) {
      explanation.textContent = "Times are measured locally. First text is relative to provider request dispatch. Session handling describes how this run resolved conversation history. Prompt-cache hits can be shared across separate sessions and do not imply shared conversation history.";
    }

    this.shadowRoot?.querySelectorAll("[data-copy]").forEach((button) => {
      button.textContent = "Copy first page";
    });
    const dialogFoot = this.shadowRoot?.querySelector("#debug-dialog .dialog-foot");
    const originalCopy = this.shadowRoot?.querySelector("#copy-debug-log");
    if (dialogFoot && originalCopy) {
      const status = document.createElement("span");
      status.id = "debug-provider-status";
      status.className = "status";
      status.style.marginRight = "auto";
      dialogFoot.prepend(status);

      const previous = document.createElement("button");
      previous.id = "debug-provider-previous";
      previous.textContent = "Previous requests";
      previous.disabled = true;
      previous.addEventListener("click", () => {
        if (this._eocDebugId) void this._viewRun(this._eocDebugId, Number(previous.dataset.offset) || 0);
      });
      originalCopy.before(previous);

      const next = document.createElement("button");
      next.id = "debug-provider-next";
      next.textContent = "Next requests";
      next.disabled = true;
      next.addEventListener("click", () => {
        if (this._eocDebugId) void this._viewRun(this._eocDebugId, Number(next.dataset.offset) || 0);
      });
      originalCopy.before(next);

      // Clone away the base handler, whose legacy toast claimed the whole debug
      // run had been copied. Management now copies exactly the visible bounded page.
      const copy = originalCopy.cloneNode(true);
      copy.textContent = "Copy visible page";
      originalCopy.replaceWith(copy);
      copy.addEventListener("click", async () => {
        try {
          await this._copyText(copy.dataset.text || "");
          this._toast("Visible debug page copied");
        } catch (err) {
          this._toast(`Unable to copy debug page: ${err.message || String(err)}`, true);
        }
      });
    }
    return result;
  };
}

function installManagementSection() {
  const ManagementPanel = customElements.get(MANAGEMENT_TAG);
  if (!ManagementPanel || ManagementPanel.name !== "ExtendedOpenAIManagementPanel"
    || ManagementPanel.prototype.__requestDebugSectionInstalled) return;
  const prototype = ManagementPanel.prototype;
  prototype.__requestDebugSectionInstalled = true;

  const originalCanAccessView = prototype._canAccessView;
  prototype._canAccessView = function(page, subsection = null) {
    if (page === "usage-maintenance" && subsection === "request-debug") {
      return this._data?.is_admin === true;
    }
    return originalCanAccessView.call(this, page, subsection);
  };

  // Request debugging owns its own small websocket surface. Do not run the generic
  // management section loader only to receive a null result; load the debug module
  // on demand and let the embedded panel fetch exactly the data it needs.
  const originalLoadSection = prototype._loadSection;
  prototype._loadSection = function(silent = false) {
    if (this._viewKey?.() !== DEBUG_VIEW) return originalLoadSection.call(this, silent);
    const view = this._viewKey();
    const token = (this._eocDebugLoadToken || 0) + 1;
    this._eocDebugLoadToken = token;
    if (customElements.get(DEBUG_TAG)) {
      installDebugPresentation();
      this._busy = false;
      this._error = null;
      this._result = null;
      this._render?.();
      return Promise.resolve();
    }
    if (!silent) {
      this._busy = true;
      this._render?.();
    }
    return ensureDebugPanel()
      .then(() => {
        if (this._eocDebugLoadToken !== token || this._viewKey?.() !== view) return;
        this._busy = false;
        this._error = null;
        this._result = null;
        this._render?.();
      })
      .catch((err) => {
        if (this._eocDebugLoadToken !== token || this._viewKey?.() !== view) return;
        this._busy = false;
        this._error = `Unable to load request debugging: ${err.message || String(err)}`;
        this._render?.();
      });
  };

  const originalContent = prototype._content;
  prototype._content = function(...args) {
    if (this._viewKey?.() !== DEBUG_VIEW) return originalContent.apply(this, args);
    if (this._data?.is_admin !== true) {
      return this._empty?.("Request debugging is available to administrators only.") || "";
    }
    if (!customElements.get(DEBUG_TAG)) {
      return this._loading?.() || '<div class="loading">Loading request debugging…</div>';
    }
    return `<extended-openai-debug-panel embedded></extended-openai-debug-panel>`;
  };

  const originalManagementRender = prototype._render;
  prototype._render = function(...args) {
    const result = originalManagementRender.apply(this, args);
    if (this._viewKey?.() !== DEBUG_VIEW) return result;
    const debugPanel = this.shadowRoot?.querySelector(DEBUG_TAG);
    if (debugPanel) {
      debugPanel._managementAgentId = this._selectedAgent?.()?.subentry_id || null;
      debugPanel.hass = this._hass;
    }
    return result;
  };
}

customElements.whenDefined(DEBUG_TAG).then(installDebugPresentation);
customElements.whenDefined(MANAGEMENT_TAG).then(installManagementSection);

export {
  DEBUG_PROVIDER_PAGE_LIMIT,
  debugPageText,
  ensureDebugPanel,
  installManagementSection,
  providerPageLabel,
  sessionLabel,
};
