import {ensureOverviewModule} from "./overview-page.js";

const PANEL_TAG = "extended-openai-management-panel";
const WS_TYPE = "extended_openai_conversation_responses/management";
const AGENT_KEY = "extended-openai-agent";
const ENTRY_KEY = "extended-openai-agent-entry";
const PATCHED = Symbol.for("extended-openai.management-initial-bootstrap");

function showInitialLoading(panel) {
  if (panel?._data !== null) return false;
  const main = panel.shadowRoot?.querySelector?.("main");
  if (!main) return false;
  main.innerHTML = panel._loading?.() || '<div class="loading" role="status">Loading…</div>';
  main.setAttribute("aria-busy", "true");
  main.dataset.eocInitialLoading = "";
  return true;
}

function applyOverviewResult(panel, result) {
  const agent = panel._selectedAgent?.();
  if (!agent || !result) return false;
  if (result.agent) Object.assign(agent, result.agent);
  const {agent: _agent, ...overview} = result;
  panel._contentData = null;
  panel._result = overview;
  panel._error = null;
  panel._busy = false;
  panel._render();
  return true;
}

function startStoredOverviewPrefetch(panel, preferredSubentryId) {
  if (panel._viewKey?.() !== "overview") return null;
  const subentryId = preferredSubentryId || globalThis.localStorage?.getItem?.(AGENT_KEY);
  const entryId = globalThis.localStorage?.getItem?.(ENTRY_KEY);
  if (!subentryId || !entryId) return null;
  return {
    entryId,
    subentryId,
    promise: Promise.allSettled([
      ensureOverviewModule(),
      panel._hass.callWS({
        type: WS_TYPE,
        section: "overview",
        action: "summary",
        entry_id: entryId,
        subentry_id: subentryId,
      }),
    ]),
  };
}

async function loadAgentsWithOverviewPrefetch(panel, selectedId = null) {
  const previousAgentId = panel._agentId;
  const saved = globalThis.localStorage?.getItem?.(AGENT_KEY);
  const preferred = selectedId || saved;
  const prefetch = startStoredOverviewPrefetch(panel, preferred);

  panel._data = await panel._hass.callWS({type: WS_TYPE, action: "agents"});
  panel._baseScopes = panel._data.scopes || [];
  const agents = panel._data.agents || [];
  panel._agentId = agents.some((item) => item.subentry_id === preferred)
    ? preferred
    : agents[0]?.subentry_id;

  const selected = panel._selectedAgent?.();
  if (panel._agentId) globalThis.localStorage?.setItem?.(AGENT_KEY, panel._agentId);
  if (selected?.entry_id) globalThis.localStorage?.setItem?.(ENTRY_KEY, selected.entry_id);
  if (previousAgentId !== panel._agentId) panel._scopeId = null;
  panel._applyScopes(panel._scopeCatalogCache.get(panel._scopeCatalogKey()) || panel._baseScopes);

  if (
    prefetch
    && selected?.subentry_id === prefetch.subentryId
    && selected?.entry_id === prefetch.entryId
    && panel._viewKey?.() === "overview"
  ) {
    const [assetResult, overviewResult] = await prefetch.promise;
    if (assetResult.status === "fulfilled" && overviewResult.status === "fulfilled") {
      applyOverviewResult(panel, overviewResult.value);
      return;
    }
  }

  await panel._loadSection();
}

function installManagementInitialBootstrap(registry = globalThis.customElements) {
  if (!registry?.whenDefined) return Promise.resolve(false);
  return registry.whenDefined(PANEL_TAG).then(() => {
    const constructor = registry.get(PANEL_TAG);
    const prototype = constructor?.prototype;
    if (!prototype || prototype[PATCHED]) return false;

    prototype._loadAgents = async function(selectedId = null) {
      try {
        await loadAgentsWithOverviewPrefetch(this, selectedId);
      } catch (err) {
        this._error = err.message || String(err);
        this._render();
      }
    };

    const originalRender = prototype._render;
    prototype._render = function(...args) {
      const result = originalRender.apply(this, args);
      showInitialLoading(this);
      return result;
    };

    prototype[PATCHED] = true;
    return true;
  });
}

if (typeof document !== "undefined" && typeof customElements !== "undefined") {
  installManagementInitialBootstrap();
}

export {
  AGENT_KEY,
  ENTRY_KEY,
  applyOverviewResult,
  installManagementInitialBootstrap,
  loadAgentsWithOverviewPrefetch,
  showInitialLoading,
  startStoredOverviewPrefetch,
};
