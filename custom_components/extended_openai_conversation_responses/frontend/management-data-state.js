export function formatManagementTimestamp(value, timeZone) {
  if (!value) return "Unknown date";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  try { return date.toLocaleString(undefined, timeZone ? {timeZone} : undefined); }
  catch (_) { return date.toLocaleString(); }
}


export function browserState(panel) {
  if (!panel._managementBrowserState) {
    panel._managementBrowserState = {
      archiveQuery: "",
      memoryQuery: "",
      memorySearchTimer: null,
      memorySearchSequence: 0,
      projections: new Map(),
    };
  }
  return panel._managementBrowserState;
}


export function prepareMemoryBrowser(panel) {
  const state = browserState(panel);
  clearTimeout(state.memorySearchTimer);
  state.memorySearchSequence += 1;
  const view = panel._viewKey();
  if (view === "data-memory/memories" && panel._memoryKind === "persistent") state.memoryQuery = "";
  if (view === "data-memory/conversations") state.archiveQuery = "";
}


export function ensureTemporaryScope(panel) {
  const scopes = (panel._data?.scopes || []).filter((scope) => scope?.scope_type === "user" || scope?.scope_type === "shared");
  if (scopes.some((scope) => scope.scope_id === panel._scopeId)) return;
  const current = scopes.find((scope) => scope.is_current_user) || scopes[0];
  if (current) panel._scopeId = current.scope_id;
}

// DOM and async search ownership are local to one agent, user, scope and kind.
export function memoryCollectionIdentity(panel) {
  return JSON.stringify([panel._selectedAgent?.()?.entry_id, panel._agentId, panel._hass?.user?.id, panel._scopeId, panel._memoryKind]);
}


export function storeRuntimeGuidance(panel, result, agentId = panel._agentId) {
  if (!result?.configuration_guidance || panel._agentId !== agentId) return;
  panel._configurationGuidance = result.configuration_guidance;
  panel._configurationGuidanceAgentId = agentId;
  if (panel._result && typeof panel._result === "object") {
    panel._result.configuration_guidance = result.configuration_guidance;
  }
}
