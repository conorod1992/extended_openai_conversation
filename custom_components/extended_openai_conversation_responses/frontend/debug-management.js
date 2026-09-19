export function loadRequestDebug(panel, silent = false) {
  const view = panel._viewKey();
  const token = (panel._eocDebugLoadToken || 0) + 1;
  panel._eocDebugLoadToken = token;
  if (customElements.get(DEBUG_TAG)) {
    panel._busy = false;
    panel._error = null;
    panel._result = null;
    panel._render?.();
    return Promise.resolve();
  }
  if (!silent) {
    panel._busy = true;
    panel._render?.();
  }
  return ensureDebugPanel()
    .then(() => {
      if (panel._eocDebugLoadToken !== token || panel._viewKey?.() !== view) return;
      panel._busy = false;
      panel._error = null;
      panel._result = null;
      panel._render?.();
    })
    .catch((err) => {
      if (panel._eocDebugLoadToken !== token || panel._viewKey?.() !== view) return;
      panel._busy = false;
      panel._error = `Unable to load request debugging: ${err.message || String(err)}`;
      panel._render?.();
    });
}

const DEBUG_VIEW = "usage-maintenance/request-debug";
const DEBUG_TAG = "extended-openai-debug-panel";
const DEBUG_PROVIDER_PAGE_LIMIT = 5;
let debugPanelPromise = null;

function ensureDebugPanel() {
  if (customElements.get(DEBUG_TAG)) {
    return Promise.resolve(customElements.get(DEBUG_TAG));
  }
  if (!debugPanelPromise) {
    debugPanelPromise = import("./debug-panel.js")
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

export function renderManagementDebug(panel) {
  if (panel._data?.is_admin !== true) return panel._empty("Request debugging is available to administrators only.");
  return customElements.get(DEBUG_TAG)
    ? `<extended-openai-debug-panel embedded></extended-openai-debug-panel>`
    : panel._loading();
}

export function bindManagementDebug(panel) {
  if (panel._viewKey() !== DEBUG_VIEW || panel._data?.is_admin !== true) return;
  const debugPanel = panel.shadowRoot?.querySelector(DEBUG_TAG);
  if (debugPanel) {
    debugPanel._managementAgentId = panel._selectedAgent?.()?.subentry_id || null;
    debugPanel.hass = panel._hass;
  }
}

export {DEBUG_PROVIDER_PAGE_LIMIT, debugPageText, ensureDebugPanel, providerPageLabel, providerPageMeta, sessionLabel};
