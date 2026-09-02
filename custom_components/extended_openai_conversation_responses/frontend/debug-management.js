import "./debug-panel.js";

const DEBUG_VIEW = "usage-maintenance/request-debug";
const MANAGEMENT_TAG = "extended-openai-management-panel";
const DEBUG_TAG = "extended-openai-debug-panel";

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

  const originalContent = prototype._content;
  prototype._content = function(...args) {
    if (this._viewKey?.() !== DEBUG_VIEW) return originalContent.apply(this, args);
    if (this._data?.is_admin !== true) {
      return this._empty?.("Request debugging is available to administrators only.") || "";
    }
    return `<extended-openai-debug-panel embedded></extended-openai-debug-panel>`;
  };

  const originalRender = prototype._render;
  prototype._render = function(...args) {
    const result = originalRender.apply(this, args);
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
