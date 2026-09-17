import {lookupModelData} from "./model-catalog.js";
import {ensureOverviewModule} from "./overview-page.js";

const PANEL_TAG = "extended-openai-management-panel";
const WS_TYPE = "extended_openai_conversation_responses/management";
const AGENT_KEY = "extended-openai-agent";
const ENTRY_KEY = "extended-openai-agent-entry";
const PATCHED = Symbol.for("extended-openai.management-overview-health-clarity");
const INITIAL_BOOTSTRAP_PATCHED = Symbol.for("extended-openai.management-initial-bootstrap");

const count = (root, state) => root?.querySelectorAll?.(`.setup-health-check-${state}`)?.length || 0;

export function clarifySetupHealthSummary(panel) {
  const root = panel?.shadowRoot;
  const summary = root?.querySelector?.(".setup-health-summary");
  if (!summary) return false;

  const errors = count(root, "error");
  const warnings = count(root, "warning");
  const unknown = count(root, "unknown");
  const issues = errors + warnings;
  const title = summary.querySelector("strong");
  const detail = summary.querySelector("span");

  if (title) {
    title.textContent = errors
      ? "Needs attention"
      : warnings
        ? "Review recommended"
        : unknown
          ? "Status incomplete"
          : "Ready";
  }

  if (detail) {
    if (issues && unknown) {
      detail.textContent = `${issues} ${issues === 1 ? "issue" : "issues"} to review · ${unknown} ${unknown === 1 ? "check" : "checks"} unavailable`;
    } else if (issues) {
      detail.textContent = `${issues} ${issues === 1 ? "issue" : "issues"} to review`;
    } else if (unknown) {
      detail.textContent = `${unknown} ${unknown === 1 ? "check" : "checks"} unavailable`;
    } else {
      detail.textContent = "Core setup looks ready";
    }
  }
  return true;
}

function modelDataCardState(panel) {
  const data = panel?._modelCatalogData;
  const error = panel?._eocOverviewModelDataError;
  if (error) {
    return {
      status: "Check failed",
      detail: "The current catalogue is still in use. Open Model Parameters to retry the check.",
      tone: "warning",
      action: "Review",
      mode: "navigate",
    };
  }
  if (!data) {
    return {
      status: "Loading…",
      detail: "Checking the current model capability data status.",
      tone: "neutral",
      action: "View",
      mode: "navigate",
    };
  }
  const current = Number(data.catalog_version);
  const available = Number(data.available_catalog_version);
  const currentLabel = Number.isInteger(current) ? `v${current}` : "current version";
  if (data.update_available) {
    const availableLabel = Number.isInteger(available) ? `v${available}` : "newer version";
    return {
      status: "Update available",
      detail: `${currentLabel} → ${availableLabel}. The newer catalogue is waiting for approval.`,
      tone: "warning",
      action: Number.isInteger(available) ? `Apply v${available}` : "Apply update",
      mode: "apply",
    };
  }
  if (data.last_error) {
    return {
      status: "Check failed",
      detail: data.last_error,
      tone: "warning",
      action: "Review",
      mode: "navigate",
    };
  }
  if (data.source === "bundled") {
    return {
      status: "Using bundled data",
      detail: `Catalogue ${currentLabel} · checked daily for newer data.`,
      tone: "neutral",
      action: "View",
      mode: "navigate",
    };
  }
  return {
    status: "Current",
    detail: `Catalogue ${currentLabel} · checked daily for newer data.`,
    tone: "positive",
    action: "View",
    mode: "navigate",
  };
}

export function renderOverviewModelDataCard(panel) {
  const grid = panel?.shadowRoot?.querySelector?.(".dashboard-grid");
  if (!grid || panel?._data?.is_admin === false) return false;
  grid.querySelector("[data-eoc-model-data-card]")?.remove();
  const state = modelDataCardState(panel);
  const article = document.createElement("article");
  article.className = `dashboard-card dashboard-tone-${state.tone}`;
  article.dataset.eocModelDataCard = "";
  article.innerHTML = `<div class="dashboard-card-main"><span class="dashboard-icon" aria-hidden="true"><ha-icon icon="mdi:database-sync-outline"></ha-icon></span><div><h2>Model Data</h2><strong>${panel._e(state.status)}</strong><p>${panel._e(state.detail)}</p></div></div><button type="button" class="secondary dashboard-action" data-eoc-model-data-action="${state.mode}">${panel._e(state.action)}</button>`;
  grid.append(article);

  article.querySelector('[data-eoc-model-data-action="navigate"]')?.addEventListener("click", () => {
    panel._navigate("assistant", "basics");
  });
  article.querySelector('[data-eoc-model-data-action="apply"]')?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    try {
      const model = panel._selectedAgent?.()?.model || "";
      await lookupModelData(panel, model, "apply");
      panel._eocOverviewModelDataError = null;
      panel._toast?.("Model data update applied");
      renderOverviewModelDataCard(panel);
    } catch (err) {
      panel._toast?.(`Unable to apply model data update: ${err.message || String(err)}`, true);
      button.disabled = false;
    }
  });
  return true;
}

function ensureOverviewModelData(panel) {
  if (panel?._data?.is_admin === false || panel?._page !== "overview") return;
  const model = panel._selectedAgent?.()?.model || "";
  if (panel._modelCatalogData?.requested_model === model) return;
  if (panel._eocOverviewModelDataPromise) return;
  panel._eocOverviewModelDataPromise = lookupModelData(panel, model)
    .then(() => { panel._eocOverviewModelDataError = null; })
    .catch((err) => { panel._eocOverviewModelDataError = err.message || String(err); })
    .finally(() => {
      panel._eocOverviewModelDataPromise = null;
      if (panel._page === "overview") renderOverviewModelDataCard(panel);
    });
}

export function showInitialLoading(panel) {
  if (panel?._data !== null) return false;
  const main = panel.shadowRoot?.querySelector?.("main");
  if (!main) return false;
  main.innerHTML = panel._loading?.() || '<div class="loading" role="status">Loading…</div>';
  main.setAttribute("aria-busy", "true");
  main.dataset.eocInitialLoading = "";
  return true;
}

export function applyOverviewResult(panel, result) {
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

export function startStoredOverviewPrefetch(panel, preferredSubentryId) {
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

export async function loadAgentsWithOverviewPrefetch(panel, selectedId = null) {
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

function installInitialBootstrap(prototype) {
  if (!prototype || prototype[INITIAL_BOOTSTRAP_PATCHED]) return false;

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

  prototype[INITIAL_BOOTSTRAP_PATCHED] = true;
  return true;
}

export function installManagementOverviewHealthClarity(registry = globalThis.customElements) {
  if (!registry?.whenDefined) return Promise.resolve(false);
  return registry.whenDefined(PANEL_TAG).then(() => {
    const constructor = registry.get(PANEL_TAG);
    const prototype = constructor?.prototype;
    if (!prototype) return false;

    installInitialBootstrap(prototype);
    if (prototype[PATCHED]) return false;

    // The optimized Overview loader keeps only its historical usage/conversation
    // fields when rebuilding _result. Preserve the backend setup-health payload
    // across that projection so health cards describe the real selected agent.
    const originalCall = prototype._call;
    prototype._call = async function(section, action, extra = {}) {
      const result = await originalCall.call(this, section, action, extra);
      if (section === "overview" && action === "summary" && result?.setup_health) {
        this._eocOverviewSetupHealth = result.setup_health;
        this._eocOverviewSetupHealthAgentId = this._agentId;
      }
      return result;
    };

    const originalRender = prototype._render;
    prototype._render = function(...args) {
      if (
        this._page === "overview"
        && this._result
        && !this._result.setup_health
        && this._eocOverviewSetupHealth
        && this._eocOverviewSetupHealthAgentId === this._agentId
      ) {
        this._result = {
          ...this._result,
          setup_health: this._eocOverviewSetupHealth,
        };
      }
      const result = originalRender.apply(this, args);
      if (this._page === "overview") {
        queueMicrotask(() => {
          clarifySetupHealthSummary(this);
          renderOverviewModelDataCard(this);
          ensureOverviewModelData(this);
        });
      }
      return result;
    };

    prototype[PATCHED] = true;
    return true;
  });
}

if (typeof document !== "undefined" && typeof customElements !== "undefined") {
  installManagementOverviewHealthClarity();
}

export {AGENT_KEY, ENTRY_KEY};
