import {ensureGuideModule} from "./guide-page.js";
import {ensureOverviewModule} from "./overview-page.js";

const PATCHED = Symbol.for("extended-openai.management-loading-performance");
const SCOPE_CACHE_TTL_MS = 30_000;

const clone = (value) => JSON.parse(JSON.stringify(value));

function viewAssetPromise(view) {
  if (view === "overview") return ensureOverviewModule();
  if (view === "guide") return ensureGuideModule();
  if (view === "usage-maintenance/request-debug") return import("./debug-panel.js");
  return null;
}

function showErrors(panel, errors = {}) {
  const root = panel.shadowRoot;
  root.querySelectorAll(".field-error").forEach((item) => { item.textContent = ""; });
  Object.entries(errors).forEach(([key, message]) => {
    const escaped = CSS.escape(key);
    const fallback = CSS.escape(key.split("[")[0]);
    const target = root.querySelector(`[data-error="${escaped}"]`) || root.querySelector(`[data-error="${fallback}"]`);
    if (target) target.textContent = message;
  });
}

async function saveConfiguration(panel, button) {
  if (!panel._draft || !panel._selectedAgent?.()) return;
  panel._setSaving(button, true);
  try {
    const result = await panel._call("configuration", "save", {
      config: clone(panel._draft),
      title: panel._draftTitle,
    });
    showErrors(panel, result.errors || {});
    if (!result.valid) {
      panel._toast("Fix the highlighted configuration errors", true);
      return;
    }

    const {valid: _valid, errors: _errors, agent, ...saved} = result;
    panel._configData = {...panel._configData, ...saved};
    panel._result = panel._configData;
    panel._draft = clone(saved.config);
    panel._draftTitle = saved.title;
    panel._draftAgentId = panel._agentId;
    if (agent) Object.assign(panel._selectedAgent(), agent);
    panel._setConfigDirty(false);
    panel._toast("Configuration saved");
    panel._render();
  } catch (err) {
    panel._toast(`Unable to save configuration: ${err.message || String(err)}`, true);
  } finally {
    panel._setSaving(button, false);
  }
}

function bindSingleRequestSave(panel) {
  const root = panel.shadowRoot;
  if (root.__eocSingleRequestSaveBound) return;
  root.__eocSingleRequestSaveBound = true;
  root.addEventListener("click", (event) => {
    const button = event.target?.closest?.("#save-config");
    if (!button) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    void saveConfiguration(panel, button);
  }, true);
}

async function loadOverview(panel, silent = false) {
  const agent = panel._selectedAgent();
  if (!agent) return panel._render();
  const loadToken = ++panel._loadToken;
  if (!silent) {
    panel._busy = true;
    panel._render();
  }
  try {
    const result = await panel._call("overview", "summary");
    if (loadToken !== panel._loadToken) return;
    if (result.agent) Object.assign(agent, result.agent);
    panel._contentData = null;
    panel._result = {
      usage: result.usage || {},
      conversations: result.conversations || {},
      load_errors: result.load_errors || [],
    };
    panel._error = null;
  } catch (err) {
    if (loadToken === panel._loadToken) panel._error = err.message || String(err);
  } finally {
    if (loadToken === panel._loadToken) {
      panel._busy = false;
      panel._render();
    }
  }
}

function isCurrentLazyLoad(panel, view, loadToken) {
  return panel._viewKey() === view && panel._loadToken === loadToken;
}

function loadSectionAfterAsset(
  panel,
  silent,
  originalLoadSection,
  view,
  assetPromise,
  loadToken,
) {
  return assetPromise
    .then(() => {
      if (!isCurrentLazyLoad(panel, view, loadToken)) return undefined;
      if (view === "overview") return loadOverview(panel, silent);
      return originalLoadSection.call(panel, silent);
    })
    .catch((err) => {
      if (!isCurrentLazyLoad(panel, view, loadToken)) return undefined;
      panel._busy = false;
      panel._error = `Unable to load this frontend section: ${err.message || String(err)}`;
      panel._render();
      return undefined;
    });
}

function install() {
  const Panel = customElements.get("extended-openai-management-panel");
  if (!Panel || Panel.prototype[PATCHED]) return;
  const prototype = Panel.prototype;
  prototype[PATCHED] = true;

  const originalCanAccessView = prototype._canAccessView;
  prototype._canAccessView = function(page, subsection = null) {
    if (page === "usage-maintenance" && subsection === "request-debug") {
      return this._data?.is_admin === true;
    }
    return originalCanAccessView.call(this, page, subsection);
  };

  const originalPrepareScopeCatalogVisit = prototype._prepareScopeCatalogVisit;
  prototype._prepareScopeCatalogVisit = function(view) {
    const key = this._scopeCatalogKey(view);
    this._scopeCatalogVisitKey = key;
    return key;
  };

  const originalLoadScopes = prototype._loadScopes;
  prototype._loadScopes = async function(scopeCatalogKey) {
    this._eocScopeCatalogTimes ||= new Map();
    const loadedAt = this._eocScopeCatalogTimes.get(scopeCatalogKey);
    if (
      scopeCatalogKey
      && this._scopeCatalogCache.has(scopeCatalogKey)
      && (!loadedAt || Date.now() - loadedAt > SCOPE_CACHE_TTL_MS)
    ) {
      this._scopeCatalogCache.delete(scopeCatalogKey);
      this._eocScopeCatalogTimes.delete(scopeCatalogKey);
    }
    await originalLoadScopes.call(this, scopeCatalogKey);
    if (scopeCatalogKey && this._scopeCatalogCache.has(scopeCatalogKey)) {
      this._eocScopeCatalogTimes.set(scopeCatalogKey, Date.now());
    }
  };

  const originalInvalidateAfterMutation = prototype._invalidateAfterMutation;
  prototype._invalidateAfterMutation = function(...args) {
    const result = originalInvalidateAfterMutation.apply(this, args);
    if (this._eocScopeCatalogTimes) {
      for (const key of this._eocScopeCatalogTimes.keys()) {
        if (!this._scopeCatalogCache.has(key)) this._eocScopeCatalogTimes.delete(key);
      }
    }
    return result;
  };

  const originalLoadSection = prototype._loadSection;
  prototype._loadSection = function(silent = false) {
    const view = this._viewKey();
    const assetPromise = viewAssetPromise(view);
    if (!assetPromise) return originalLoadSection.call(this, silent);
    const loadToken = ++this._loadToken;
    return loadSectionAfterAsset(
      this,
      silent,
      originalLoadSection,
      view,
      assetPromise,
      loadToken,
    );
  };

  const originalRender = prototype._render;
  prototype._render = function(...args) {
    const result = originalRender.apply(this, args);
    bindSingleRequestSave(this);
    return result;
  };

  // Retained only to document that the old implementation deliberately cleared
  // scope caches on every page transition. The replacement above keeps isolated
  // per-agent/per-view entries for a short TTL instead.
  void originalPrepareScopeCatalogVisit;
}

if (typeof customElements !== "undefined") {
  customElements.whenDefined("extended-openai-management-panel").then(install);
}

export {SCOPE_CACHE_TTL_MS, loadSectionAfterAsset};
