import {bindConfigurationClarity, enhanceConfigurationClarity} from "./management-draft-navigation.js";
import {formatManagementTimestamp, prepareMemoryBrowser, ensureTemporaryScope, storeRuntimeGuidance} from "./management-data-state.js";
import {enhanceConfirmationScope} from "./management-confirmation-scope.js";
import {configurationDestinations} from "./management-config-destinations.js";
import {
  bindPageDrafts,
  initializePageDraft,
  refreshPageSaveBar,
  savePageChanges,
} from "./management-page-drafts.js";
import {readSectionCache, writeSectionCache, pruneCacheTimes, SCOPE_CACHE_TTL_MS} from "./management-cache.js";
import {bindPanelDialogs, knowledgeSourceAvailabilityControl} from "./management-dialogs.js";
import {renderManagement} from "./management-renderer.js";
import {bindSingleRequestSave, bindFrontendCorrectness, normalizeGuestModeTimestamp, setControlPending, isAgentMutation, syncAgentPicker} from "./management-actions.js";
import {loadAgentsWithOverviewPrefetch, loadRoute, bindRequestRuleSearch, applyRequestRuleSearch, warmRouteAsset} from "./management-route.js";
import {getConfigurationEditor, getConfigurationTools, getRouteFeature, routeAssetKind, routeFeaturesReady, isRestrictedManagementView, nonAdminOverviewKnowledgeSnapshot} from "./management-route.js";
import {NAVIGATION, pageMetadata, routeFromPath, routePath} from "./frontend-navigation.js";
import {bindGuide, renderGuide} from "./guide-page.js";
import {bindOverview, renderOverview, enhanceOverviewHealthClarity} from "./overview-page.js";
import {formatUsageNumber} from "./usage-format.js";
import {
  bindStateSafety,
  cleanupStateSafety,
  confirmDialogClose,
  confirmStateSafeNavigation,
  openDialogBaseline,
  rebuildConfigDirtyKeys,
} from "./management-state-safety.js";

const WS_TYPE = "extended_openai_conversation_responses/management";
const TOOL_MUTATIONS = new Set(["save", "set_enabled", "delete", "save_group", "delete_group", "ha_add"]);
const REQUEST_RULE_CACHE_KEY = "capabilities/request-rules";

const KNOWLEDGE_TITLE_LIMIT = 120;
const KNOWLEDGE_DESCRIPTION_LIMIT = 500;
const KNOWLEDGE_LIMIT = 100000;
const NAVIGATION_MARK_PREFIX = "extended-openai:navigation";
const LOAD_MARK_PREFIX = "extended-openai:load-section";
const RENDER_MARK_PREFIX = "extended-openai:render";
const MAX_MEASURE_ENTRIES = 100;
const COLD_MARK_PREFIX = "extended-openai:cold";
// Performance entries are global to the document, not to a panel instance.
let performanceSequence = 0;
let navigationSearchModule = null;
let navigationSearchPromise = null;

function settingsSearchShellMarkup(panel) {
  const query = panel._settingsSearchQuery || "";
  return `<div class="global-search eoc-global-search"><label><span class="search-label">Find a setting</span><input id="settings-search" type="search" value="${panel._e(query)}" placeholder="Search settings by name or purpose" aria-label="Search all settings" autocomplete="off"></label><div class="search-results" role="listbox" aria-label="Settings search results" ${query ? "" : "hidden"}></div></div>`;
}

function ensureNavigationSearchModule(panel) {
  if (navigationSearchModule) {
    navigationSearchModule.enhanceNavigationSearch(panel);
    return Promise.resolve(navigationSearchModule);
  }
  if (!navigationSearchPromise) {
    navigationSearchPromise = import("./management-navigation-search.js")
      .then((module) => {
        navigationSearchModule = module;
        return module;
      })
      .finally(() => { navigationSearchPromise = null; });
  }
  return navigationSearchPromise.then((module) => {
    module.enhanceNavigationSearch(panel);
    return module;
  });
}

try {
  globalThis.performance?.mark?.(`${COLD_MARK_PREFIX}:module-evaluated`);
} catch (_err) {
  // Cold-start instrumentation must never affect panel startup.
}
const MANAGEMENT_STYLESHEET_URL = new URL("./management.css", import.meta.url).href;
// Keep this deliberately small and geometry-identical to management.css: it exists
// only to prevent FOUC/layout shift while the external stylesheet is still pending.
const CRITICAL_STYLE = `
  :host{display:block;min-height:100%;padding:28px;color:var(--primary-text-color);font-family:var(--paper-font-body1_-_font-family,system-ui);font-size:14px;line-height:1.45;box-sizing:border-box;background:color-mix(in srgb,var(--secondary-background-color) 42%,var(--primary-background-color))}
  *{box-sizing:border-box}
  [hidden]{display:none!important}
  .page-shell{max-width:1380px;margin:auto}
  header{display:flex;justify-content:space-between;gap:36px;align-items:end;margin-bottom:28px}
  .page-heading h1{margin:0;font-size:30px;font-weight:500}
  .page-heading p{margin:6px 0 0;color:var(--secondary-text-color);line-height:1.5}
  header .global-search.eoc-global-search{width:min(380px,100%);max-width:100%;min-width:280px;margin:0;align-self:end;position:relative}
  header .eoc-global-search>label{display:block}
  header .eoc-global-search .search-label{display:none}
  label{display:grid;gap:7px;font-size:14px;color:var(--secondary-text-color)}
  input,select,textarea,button{font:inherit}
  input,select,textarea{width:100%;min-height:42px;color:var(--primary-text-color);background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:9px;padding:10px 12px}
  button{min-height:42px;border:0;border-radius:9px;padding:9px 16px;cursor:pointer;background:var(--primary-color);color:var(--text-primary-color)}
  .mobile-nav{display:none}
  .eoc-agent-context-row{display:flex;align-items:end;gap:12px;margin:0 0 14px}
  .eoc-agent-context-row .agent-picker{width:min(390px,100%);min-width:0;margin:0}
  .eoc-agent-context-row .agent-picker.eoc-agent-context{min-width:0;padding:0;border:0;border-radius:0;background:transparent;box-shadow:none}
  .eoc-agent-actions{display:grid;justify-items:end;gap:5px;margin-left:auto}
  nav{display:flex;overflow:auto;border-bottom:1px solid var(--divider-color);margin-bottom:28px}
  .top-nav{overflow:visible}
  nav button{background:transparent;color:var(--secondary-text-color);border-radius:0;padding:13px 18px;white-space:nowrap}
  nav button.active{color:var(--primary-color);border-bottom:3px solid var(--primary-color)}
  .subsection-nav{display:flex;gap:8px;flex-wrap:wrap;overflow:visible;border:0;margin:0 0 12px;padding:0}
  .subsection-nav button{min-height:38px;padding:8px 13px;border:1px solid var(--divider-color);border-radius:999px;background:var(--card-background-color);color:var(--secondary-text-color)}
  .subsection-nav button.active{border-color:var(--primary-color);background:color-mix(in srgb,var(--primary-color) 10%,var(--card-background-color));color:var(--primary-color)}
  .section-layout{display:block}
  .section-selector{display:grid;grid-template-columns:minmax(240px,420px) minmax(0,620px);align-items:end;gap:18px;margin:0 0 28px}
  .section-selector p{margin:0 0 10px;color:var(--secondary-text-color);line-height:1.45}
  main{display:grid;gap:30px}
  .page-intro{display:grid;gap:7px;max-width:780px}
  .page-intro h1,.page-intro p{margin:0}
  .page-intro p{color:var(--secondary-text-color);line-height:1.5}
  .dashboard-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:18px}
  .dashboard-card{display:flex;justify-content:space-between;align-items:end;gap:18px;padding:22px;background:var(--card-background-color);border:1px solid var(--divider-color);border-radius:13px}
  .dashboard-card h2,.dashboard-card p{margin:0}
  .dashboard-card strong{display:block;margin-top:12px;font-size:18px}
  .dashboard-card p{margin-top:7px;color:var(--secondary-text-color);line-height:1.45}
  @media (min-width:680px) and (max-width:1100px){.dashboard-grid{grid-template-columns:1fr}}
  .loading{display:flex;align-items:center;justify-content:center;gap:10px;min-height:130px;color:var(--secondary-text-color)}
  .spinner{width:20px;height:20px;border:2px solid var(--divider-color);border-top-color:var(--primary-color);border-radius:50%}
  @media (min-width:801px){.section-selector>label{display:none}.section-selector{margin-top:0}}
  @media (max-width:800px){
    header{flex-direction:column;align-items:stretch;gap:18px}
    header .global-search.eoc-global-search{width:100%;min-width:0;align-self:stretch}
    .eoc-agent-context-row{display:grid;grid-template-columns:1fr;align-items:stretch;gap:10px;margin-bottom:18px}
    .eoc-agent-context-row .agent-picker{width:100%}
    .eoc-agent-actions{width:100%;margin-left:0;justify-items:stretch}
    .subsection-nav{display:none}
  }
  @media (max-width:760px){
    .dashboard-grid{grid-template-columns:1fr}
    .dashboard-card{align-items:stretch;flex-direction:column}
    .dashboard-card button{width:100%}
    .top-nav{display:none}
    .mobile-nav{display:grid;margin-bottom:18px}
  }
`;


function performanceApi() {
  const api = globalThis.performance;
  return api && typeof api.mark === "function" && typeof api.measure === "function" ? api : null;
}

function startMeasure(panel, prefix) {
  const api = performanceApi();
  if (!api) return null;
  const id = `${prefix}:${++performanceSequence}`;
  const start = `${id}:start`;
  try {
    api.mark(start);
    return {api, id, start, panel};
  } catch (_err) {
    return null;
  }
}

function finishMeasure(measure, detail = null) {
  if (!measure) return;
  const {api, id, start, panel} = measure;
  const end = `${id}:end`;
  try {
    api.mark(end);
    try {
      api.measure(id, {start, end, detail});
    } catch (_err) {
      api.measure(id, start, end);
    }
    panel._eocPerformanceMeasureIds ||= [];
    panel._eocPerformanceMeasureIds.push(id);
    while (panel._eocPerformanceMeasureIds.length > MAX_MEASURE_ENTRIES) {
      api.clearMeasures?.(panel._eocPerformanceMeasureIds.shift());
    }
  } catch (_err) {
    // Optional profiling must never turn a successful load into a UI error or
    // replace the original failure, even if the host cleared an active mark.
  } finally {
    for (const name of [start, end]) {
      try { api.clearMarks?.(name); } catch (_err) { /* Best-effort cleanup. */ }
    }
  }
}

function trackAsync(panel, prefix, operation, navigation = false) {
  const view = panel._viewKey?.() || null;
  const measure = startMeasure(panel, prefix);
  if (navigation) panel._eocNavigationDepth = (panel._eocNavigationDepth || 0) + 1;
  const finish = (status) => {
    if (navigation) panel._eocNavigationDepth = Math.max(0, (panel._eocNavigationDepth || 1) - 1);
    finishMeasure(measure, {view, status});
  };
  let result;
  try {
    result = operation();
  } catch (err) {
    finish("threw");
    throw err;
  }
  if (!result || typeof result.finally !== "function") {
    finish("sync");
    return result;
  }
  return result.finally(() => finish("settled"));
}


function settledSectionResult(entries, settled) {
  const result = {};
  const load_errors = [];
  entries.forEach(([key, label], index) => {
    const item = settled[index];
    if (item.status === "fulfilled") result[key] = item.value;
    else load_errors.push({key, label, message: item.reason?.message || String(item.reason || "Unknown error")});
  });
  return {...result, load_errors};
}

export class ExtendedOpenAIManagementPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._eocColdLifecycleMarks = new Set();
    this._markColdLifecycle("constructed");
    const route = routeFromPath(window.location.pathname);
    this._page = route.page;
    this._subsection = route.section;
    this._data = null;
    this._result = null;
    this._busy = false;
    this._query = "";
    this._memoryKind = "persistent";
    this._showEmptyScopes = false;
    this._confirmResolver = null;
    this._configDirty = false;
    this._configData = null;
    this._draft = null;
    this._draftTitle = null;
    this._draftAgentId = null;
    this._sectionCache = new Map();
    this._eocSectionCacheTimes = new Map();
    this._scopeCatalogCache = new Map();
    this._scopeCatalogVisitKey = null;
    this._baseScopes = [];
    this._serviceCatalog = null;
    this._serviceCatalogPromise = null;
    this._loadToken = 0;
    this._cacheGeneration = 0;
    this._eocScopeCatalogTimes = new Map();
    this._eocInPlaceRequestRuleSearch = true;
    this._configSearchQuery = "";
    this._settingsSearchQuery = "";
    this._guideQuery = "";

  }

  _markColdLifecycle(name, detail = null) {
    const api = performanceApi();
    if (!api || !name || this._eocColdLifecycleMarks?.has(name)) return false;
    this._eocColdLifecycleMarks ||= new Set();
    this._eocColdLifecycleMarks.add(name);
    try {
      const mark = `${COLD_MARK_PREFIX}:${name}`;
      if (detail == null) api.mark(mark);
      else api.mark(mark, {detail});
      return true;
    } catch (_err) {
      return false;
    }
  }

  set hass(value) {
    const first = !this._hass;
    this._hass = value;
    if (first) this._loadAgents();
  }

  set route(value) {
    this._route = value;
    const route = routeFromPath(window.location.pathname);
    if (route.page !== this._page || route.section !== this._subsection) this._handleRouteChange(route);
    else if (!this.shadowRoot.hasChildNodes()) this._render();
  }

  connectedCallback() {
    this._markColdLifecycle("connected");
    // Home Assistant can assign properties before custom-element upgrade. Replay
    // own properties so the class setters receive the values after definition.
    for (const name of ["hass", "route"]) {
      if (!Object.prototype.hasOwnProperty.call(this, name)) continue;
      const value = this[name];
      delete this[name];
      this[name] = value;
    }
    bindStateSafety(this);
    bindPanelDialogs(this);
    bindSingleRequestSave(this);
    bindFrontendCorrectness(this);
    bindPageDrafts(this);
    bindConfigurationClarity(this);
    this._bindSettingsSearchLazyLoad();
    this._bindRouteAssetWarmup();
    if (this._viewKey() === "usage-maintenance/diagnostics") {
      getRouteFeature("usage-maintenance/diagnostics")?.enhanceDiagnostics(this);
    }
  }

  disconnectedCallback() {
    getRouteFeature("usage-maintenance/diagnostics")?.stopDiagnosticsWatch(this);
    cleanupStateSafety(this);
  }

  _warmNavigationTarget(target) {
    const page = target?.dataset?.page || this._page;
    const subsection = target?.dataset?.subsection
      || (target?.dataset?.page ? this._visibleSubsections(page)[0]?.id || null : null);
    const view = this._viewKey(page, subsection);
    warmRouteAsset(view);
    void this._loadConfigurationLiveMetadata(view);
  }

  _bindSettingsSearchLazyLoad() {
    const root = this.shadowRoot;
    if (!root || root.__eocSettingsSearchLazyBound) return;
    root.__eocSettingsSearchLazyBound = true;
    const load = (event) => {
      const input = event.target?.closest?.("#settings-search");
      if (!input) return;
      if (event.type === "input") {
        this._settingsSearchQuery = input.value;
        if (navigationSearchModule || navigationSearchPromise) return;
      }
      void ensureNavigationSearchModule(this);
    };
    root.addEventListener("focusin", load, true);
    root.addEventListener("input", load, true);
  }

  _bindRouteAssetWarmup() {
    const root = this.shadowRoot;
    if (!root || root.__eocRouteAssetWarmupBound) return;
    root.__eocRouteAssetWarmupBound = true;
    let hoverTimer = null;
    let hoverTarget = null;
    const cancelHover = () => {
      if (hoverTimer !== null) clearTimeout(hoverTimer);
      hoverTimer = null;
      hoverTarget = null;
    };
    root.addEventListener("pointerover", (event) => {
      const target = event.target?.closest?.("[data-page],[data-subsection]");
      if (!target || target === hoverTarget) return;
      cancelHover();
      hoverTarget = target;
      hoverTimer = setTimeout(() => {
        hoverTimer = null;
        if (hoverTarget === target) this._warmNavigationTarget(target);
      }, 100);
    });
    root.addEventListener("pointerout", (event) => {
      const target = event.target?.closest?.("[data-page],[data-subsection]");
      if (target && target === hoverTarget && !target.contains(event.relatedTarget)) cancelHover();
    });
    root.addEventListener("focusin", (event) => {
      const target = event.target?.closest?.("[data-page],[data-subsection]");
      if (target) this._warmNavigationTarget(target);
    });
    root.addEventListener("pointerdown", (event) => {
      const target = event.target?.closest?.("[data-page],[data-subsection]");
      if (target) {
        cancelHover();
        this._warmNavigationTarget(target);
      }
    });
  }

  _setConfigDirty(value) {
    if (!value) {
      this._eocDirtyConfigKeys = new Set();
      this._configDirty = false;
      return;
    }
    this._configDirty = true;
    if (this._eocDirtyConfigKeys instanceof Set) {
      queueMicrotask(() => {
        if (!(this._eocDirtyConfigKeys instanceof Set) || this._eocDirtyConfigKeys.size) return;
        const changed = rebuildConfigDirtyKeys(this);
        const dirty = changed.size > 0;
        const wasDirty = Boolean(this._configDirty);
        this._configDirty = dirty;
        if (wasDirty && !dirty) this._render();
      });
    }
  }

  _clearConfigDraft() {
    this._setConfigDirty(false);
    this._eocLiveMetadataEpoch = (this._eocLiveMetadataEpoch || 0) + 1;
    this._eocLiveMetadataCache = new Map();
    this._eocLiveMetadataPending = new Map();
    this._configData = null;
    this._draft = null;
    this._draftTitle = null;
    this._draftAgentId = null;
    this._configSearchQuery = "";
    this._settingsSearchConfig = null;
    this._settingsSearchConfigAgentId = null;
    this._settingsSearchConfigError = null;
    this._settingsSearchConfigErrorAgentId = null;
  }

  _configurationDirtyDestinations() {
    return configurationDestinations(this);
  }

  _syncConfigDirty() {
    const changed = rebuildConfigDirtyKeys(this);
    this._configDirty = changed.size > 0;
    return this._configDirty;
  }

  _captureDialogBaseline(dialog) {
    openDialogBaseline(this, dialog);
  }

  _confirmEditorClose(dialog) {
    return confirmDialogClose(this, dialog);
  }

  _confirmUnsavedNavigation(destination) {
    return confirmStateSafeNavigation(this, destination);
  }

  async _handleRouteChange(route) {
    const destination = route.section ? `${route.page}/${route.section}` : route.page;
    if (!await confirmStateSafeNavigation(this, destination)) {
      history.pushState({}, "", routePath(this._page, this._subsection));
      return;
    }
    if (this._isDraftView() && !this._isDraftView(route.page, route.section)) {
      this._clearConfigDraft();
    }
    this._page = route.page;
    this._subsection = route.section;
    this._query = "";
    this._result = null;
    await this._loadSection();
  }

  _viewKey(page = this._page, subsection = this._subsection) {
    return subsection ? `${page}/${subsection}` : page;
  }

  _isDraftView(page = this._page, subsection = this._subsection) {
    if ((page === "data-memory" && subsection === "memory-settings") || (page === "capabilities" && subsection === "web-skills")) return this._data?.is_admin !== false;
    if (this._data && !this._data.is_admin) return false;
    return page === "assistant" ||
      (page === "capabilities" && ["home-assistant", "request-rules", "functions"].includes(subsection)) ||
      (page === "data-memory" && subsection === "conversations") ||
      (page === "usage-maintenance" && ["backup-restore", "retention"].includes(subsection));
  }

  _configSectionsForView() {
    return {
      "capabilities/home-assistant": ["local"],
      "capabilities/web-skills": ["capabilities"],
      "assistant/basics": ["general"],
      "assistant/model-responses": ["model"],
      "assistant/conversation": ["conversation", "context"],
      "assistant/prompt-context": ["prompt"],
      "assistant/voice": ["voice"],
      "assistant/speech": ["speech"],
      "assistant/advanced": ["capabilities"],
      "data-memory/conversations": ["archive"],
      "usage-maintenance/backup-restore": ["backup"],
      "usage-maintenance/retention": ["retention"],
    }[this._viewKey()] || [];
  }

  _configurationActions() {
    const sections = this._configSectionsForView();
    if (!sections.length) return "";
    if (this._viewKey() === "data-memory/conversations" && !this._data?.is_admin) return "";
    return getConfigurationEditor()?.renderConfigurationActions?.(this, sections) || "";
  }

  _canAccessView(page, subsection = null) {
    if (page === "data-memory" && subsection === "memory-settings" && this._data?.is_admin === false) return false;
    if (this._data?.is_admin === false && isRestrictedManagementView(page, subsection)) return false;
    if (page === "usage-maintenance" && subsection === "request-debug") return this._data?.is_admin === true;
    if (this._data?.is_admin !== false) return true;
    if (page === "assistant") return false;
    if (page === "capabilities" && subsection && subsection !== "guest-mode") return false;
    if (page === "usage-maintenance" && ["backup-restore", "retention"].includes(subsection)) return false;
    return true;
  }

  _enhanceSubsectionNavigation() {
    const root = this.shadowRoot;
    if (!root) return;
    const local = this._visibleSubsections();
    const topNav = root.querySelector(".top-nav");
    let nav = root.querySelector(".subsection-nav");
    const markup = local.length > 1
      ? local.map((item) => `<button type="button" data-subsection="${this._e(item.id)}" class="${item.id === this._subsection ? "active" : ""}" ${item.id === this._subsection ? 'aria-current="page"' : ""}>${this._e(item.label)}</button>`).join("")
      : "";
    if (!nav && topNav) {
      nav = document.createElement("nav");
      nav.className = "subsection-nav";
      topNav.after(nav);
    }
    if (nav && nav._eocMarkup !== markup) {
      nav.innerHTML = markup;
      nav._eocMarkup = markup;
      this._eocNavigationRevision = (this._eocNavigationRevision || 0) + 1;
      nav.hidden = !markup;
      nav.setAttribute("aria-label", `${pageMetadata(this._page).label} sections`);
    }
  }

  _visibleSubsections(page = this._page) {
    return pageMetadata(page).sections.filter((item) => this._canAccessView(page, item.id));
  }

  async _call(section, action, extra = {}) {
    if (section === "usage" && action === "daily" && !extra.start_date && !extra.end_date) {
      const {loadUsageDaily} = await import("./usage-data.js");
      return loadUsageDaily(this, extra);
    }
    const guidanceCall = section === "configuration"
      && ["get", "validate", "update", "save"].includes(action);
    const guidanceAgentId = this._agentId;
    const guidanceRevision = guidanceCall
      ? (this._eocGuidanceCallRevision = (this._eocGuidanceCallRevision || 0) + 1)
      : null;

    let result;
    if (
      this._data?.is_admin === false
      && this._viewKey() === "overview"
      && section === "knowledge"
      && action === "list"
    ) {
      result = nonAdminOverviewKnowledgeSnapshot(this);
    } else {
      let payload = extra;
      if (
        section === "tools"
        && TOOL_MUTATIONS.has(action)
        && extra.revision === undefined
        && typeof this._configData?.revision === "string"
      ) {
        payload = {revision: this._configData.revision, ...extra};
      }
      result = await this._callWithMutationSafety(section, action, payload);
      this._applyToolRevision(section, action, result);
    }

    if (
      guidanceCall
      && guidanceRevision === this._eocGuidanceCallRevision
      && this._agentId === guidanceAgentId
    ) {
      storeRuntimeGuidance(this, result, guidanceAgentId);
    }
    return result;
  }

  _callWithMutationSafety(section, action, extra = {}) {
    if (!isAgentMutation(section, action)) return this._callCore(section, action, extra);

    this._pendingMutations ||= new Map();
    const key = JSON.stringify([section, action, extra]);
    if (this._pendingMutations.has(key)) return this._pendingMutations.get(key);

    const previous = section === "tools"
      ? (this._eocFunctionMutationTail || Promise.resolve())
      : Promise.resolve();
    const pending = previous.catch(() => {}).then(
      () => this._runAgentMutation(section, action, extra),
    );
    const tracked = pending.finally(() => {
      this._pendingMutations.delete(key);
      if (this._eocFunctionMutationTail === tracked) this._eocFunctionMutationTail = null;
    });
    if (section === "tools") this._eocFunctionMutationTail = tracked;
    this._pendingMutations.set(key, tracked);
    return tracked;
  }

  async _runAgentMutation(section, action, extra) {
    this._eocAgentMutations = Number(this._eocAgentMutations || 0) + 1;
    syncAgentPicker(this);
    try {
      const result = await this._callCore(section, action, extra);
      this._applyToolRevision(section, action, result);
      return result;
    } finally {
      this._eocAgentMutations = Math.max(0, Number(this._eocAgentMutations || 1) - 1);
      syncAgentPicker(this);
    }
  }

  _applyToolRevision(section, action, result) {
    if (
      section === "tools"
      && TOOL_MUTATIONS.has(action)
      && typeof result?.revision === "string"
      && this._configData
    ) {
      this._configData = {...this._configData, revision: result.revision};
    }
  }

  _callCore(section, action, extra = {}) {
    // Configuration remains editable even when persisted Function Tools need repair.
    const issue = this._selectedAgent()?.configuration_issue;
    if (section === "configuration" && issue?.field === "functions" && issue.repairable === true) {
      const repairAction = {get:"configuration_get", validate:"configuration_validate", save:"configuration_save", update:"configuration_save"}[action];
      if (repairAction) return this._request("function_repair", repairAction, extra);
    }
    let payload = extra;
    if (section === "guest_mode" && action === "update") {
      payload = {...extra};
      for (const key of ["active_from", "active_until"]) {
        if (payload[key]) payload[key] = normalizeGuestModeTimestamp(payload[key]);
      }
    }
    const ruleSave = section === "request_rules" && ["create", "update"].includes(action)
      && this.shadowRoot?.querySelector?.("#rule-dialog")?.open;
    if (!ruleSave) return this._request(section, action, payload);
    if (this._eocRuleSavePromise) return this._eocRuleSavePromise;
    const button = this.shadowRoot.querySelector("#rule-save");
    setControlPending(this, button, true);
    const request = Promise.resolve().then(() => this._request(section, action, payload));
    const tracked = request.finally(() => {
      setControlPending(this, button, false);
      if (this._eocRuleSavePromise === tracked) this._eocRuleSavePromise = null;
    });
    this._eocRuleSavePromise = tracked;
    return tracked;
  }

  async _request(section, action, extra = {}) {
    if (!this._hass) return null;
    const agent = this._selectedAgent();
    const result = await this._hass.callWS({
      type: WS_TYPE,
      section,
      action,
      ...(agent ? { entry_id: agent.entry_id, subentry_id: agent.subentry_id } : {}),
      ...extra,
    });
    this._invalidateAfterMutation(agent?.subentry_id, section, action);
    pruneCacheTimes(this);
    return result;
  }

  _invalidateAfterMutation(agentId, section, action) {
    if (agentId && section === "backup" && action === "restore") {
      this._cacheGeneration += 1;
      const prefix = `${agentId}|`;
      for (const key of this._sectionCache.keys()) if (key.startsWith(prefix)) this._sectionCache.delete(key);
      for (const key of this._scopeCatalogCache.keys()) if (key.startsWith(prefix)) this._scopeCatalogCache.delete(key);
      if (this._scopeCatalogVisitKey?.startsWith(prefix)) this._scopeCatalogVisitKey = null;
    } else {
      const mutations = {
        request_rules: new Set(["defaults", "wording_groups", "create", "update", "delete", "duplicate"]),
        knowledge: new Set(["create", "update", "delete"]),
        memories: new Set(["add", "update", "delete", "temporary_delete", "reassign_legacy"]),
        conversations: new Set(["delete"]),
      };
      if (agentId && mutations[section]?.has(action)) {
        this._cacheGeneration += 1;
        const prefix = `${agentId}|`;
        const view = {request_rules:"capabilities/request-rules", knowledge:"data-memory/knowledge"}[section];
        if (view) this._sectionCache.delete(`${prefix}${view}`);
        if (["memories", "conversations"].includes(section)) {
          for (const key of this._scopeCatalogCache.keys()) {
            if (key.startsWith(prefix)) this._scopeCatalogCache.delete(key);
          }
          if (this._scopeCatalogVisitKey?.startsWith(prefix)) this._scopeCatalogVisitKey = null;
        }
      }
    }

    const affectsRequestRules = agentId && (
      (section === "tools" && TOOL_MUTATIONS.has(action))
      || (section === "request_rules" && action === "move")
    );
    if (affectsRequestRules) {
      const key = `${agentId}|${REQUEST_RULE_CACHE_KEY}`;
      this._sectionCache?.delete(key);
      this._eocSectionCacheTimes?.delete(key);
    }
  }

  _sectionCacheKey(view = this._viewKey()) {
    const agentId = this._agentId;
    if (!agentId) return null;
    if (["capabilities/request-rules", "data-memory/knowledge"].includes(view)) return `${agentId}|${view}`;
    return null;
  }

  _scopeCatalogKey(view = this._viewKey(), agentId = this._agentId) {
    if (!agentId || !["data-memory/memories", "data-memory/conversations"].includes(view)) return null;
    return `${agentId}|scopes`;
  }

  _prepareScopeCatalogVisit(view) {
    const key = this._scopeCatalogKey(view);
    this._scopeCatalogVisitKey = key;
    return key;
  }

  _applyScopes(scopes) {
    this._data.scopes = scopes || [];
    const current = this._data.scopes.find((scope) => scope.is_current_user);
    if (!this._data.scopes.some((scope) => scope.scope_id === this._scopeId)) {
      this._scopeId = current?.scope_id || this._data.scopes[0]?.scope_id;
    }
  }

  async _loadServiceCatalog() {
    if (this._serviceCatalog) return this._serviceCatalog;
    if (!this._serviceCatalogPromise) {
      this._serviceCatalogPromise = this._call("service_catalog", "get")
        .then((response) => {
          this._serviceCatalog = response?.services || {};
          return this._serviceCatalog;
        })
        .finally(() => { this._serviceCatalogPromise = null; });
    }
    return this._serviceCatalogPromise;
  }

  async _loadAgents(selectedId = null) {
    this._markColdLifecycle("agents-start");
    try {
      await loadAgentsWithOverviewPrefetch(this, selectedId);
      this._markColdLifecycle("agents-complete", {status: "fulfilled"});
    } catch (err) {
      this._markColdLifecycle("agents-complete", {status: "rejected"});
      this._error = err.message || String(err);
      this._render();
    }
  }

  async _loadScopes(scopeCatalogKey) {
    if (!this._selectedAgent() || !scopeCatalogKey) return;
    const agentId = this._agentId;
    const loadedAt = this._eocScopeCatalogTimes.get(scopeCatalogKey);
    if (!loadedAt || Date.now() - loadedAt > SCOPE_CACHE_TTL_MS) {
      this._scopeCatalogCache.delete(scopeCatalogKey);
      this._eocScopeCatalogTimes.delete(scopeCatalogKey);
    }
    if (this._scopeCatalogCache.has(scopeCatalogKey)) {
      this._applyScopes(this._scopeCatalogCache.get(scopeCatalogKey));
      return;
    }
    const generation = this._cacheGeneration;
    const loadToken = this._loadToken;
    const response = await this._call("scopes", "catalog");
    const scopes = response.scopes || [];
    if (scopeCatalogKey !== this._scopeCatalogVisitKey || agentId !== this._agentId
        || generation !== this._cacheGeneration || loadToken !== this._loadToken) return;
    this._scopeCatalogCache.set(scopeCatalogKey, scopes);
    this._eocScopeCatalogTimes.set(scopeCatalogKey, Date.now());
    this._applyScopes(scopes);
  }

  _selectedAgent() {
    return this._data?.agents?.find((item) => item.subentry_id === this._agentId);
  }

  async _loadSection(silent = false) {
    // Same-scope Memory refreshes keep their read-only collection on screen.
    // Scope/agent/kind changes still take the normal loading/replacement path.
    if (this._viewKey() === "data-memory/memories" && getRouteFeature("data-memory/memories")?.hasMemoryCollection(this)) silent = true;
    if (this._viewKey() === "data-memory/memories" && this._memoryKind === "temporary") ensureTemporaryScope(this);
    prepareMemoryBrowser(this);
    const value = await trackAsync(this, LOAD_MARK_PREFIX, () => loadRoute(this, silent));
    await getRouteFeature("memory-browser")?.finishMemoryBrowserLoad(this);
    return value;
  }

  async _loadSectionData(silent = false) {
    if (!this._selectedAgent()) return this._render();
    const view = this._viewKey();
    const loadToken = ++this._loadToken;
    const cacheGeneration = this._cacheGeneration;
    const scopeCatalogKey = this._prepareScopeCatalogVisit(view);
    const configOnly = this._isDraftView() && view !== "data-memory/conversations" && !["capabilities/request-rules"].includes(view);
    if (configOnly && this._configData && this._draftAgentId === this._agentId) {
      this._applyConfigurationLiveMetadata(view);
      this._contentData = null;
      this._result = this._configData;
      this._error = null;
      this._busy = false;
      this._render();
      void this._loadConfigurationLiveMetadata(view);
      return;
    }
    const needsScopes = ["data-memory/memories", "data-memory/conversations"].includes(view);
    const cache = readSectionCache(this, view);
    const cacheKey = cache.key;
    const showCached = cache.result !== undefined && (cache.fresh || view === "data-memory/knowledge");
    if (showCached) {
      this._contentData = null;
      this._result = cache.result;
      this._error = null;
      this._busy = false;
      this._render();
      if (cache.fresh) return;
      // Knowledge records are fetched again when opened for editing. Show the
      // expired read-only list immediately while refreshing its current revision.
      silent = true;
    }
    if (!silent) {
      this._busy = true;
      this._render();
    }
    try {
      const configPromise = view === "data-memory/conversations" && this._data?.is_admin
        ? this._loadConfigDraft() : Promise.resolve();
      const initialScopeId = needsScopes ? this._scopeId : null;
      const cachedScopeLoadedAt = scopeCatalogKey ? this._eocScopeCatalogTimes.get(scopeCatalogKey) : null;
      const cachedScopes = cachedScopeLoadedAt && Date.now() - cachedScopeLoadedAt <= SCOPE_CACHE_TTL_MS
        ? this._scopeCatalogCache.get(scopeCatalogKey) : null;
      const knownScopes = cachedScopes || this._baseScopes || [];
      const canPrefetchScopedCollection = Boolean(
        initialScopeId && knownScopes.some((scope) => scope.scope_id === initialScopeId),
      );
      const loadScopedCollection = (scopeId) => view === "data-memory/conversations"
        ? this._call("conversations", "list", { scope_id: scopeId, limit: 50 })
        : this._call("memories", this._memoryKind === "temporary" ? "temporary_list" : "list", { scope_id: scopeId, limit: 100 });
      const scopePromise = needsScopes ? this._loadScopes(scopeCatalogKey) : Promise.resolve();
      const prefetchedScopedCollection = canPrefetchScopedCollection
        ? loadScopedCollection(initialScopeId).then(
          (value) => ({status: "fulfilled", value}),
          (reason) => ({status: "rejected", reason}),
        )
        : null;
      const activeConversationsPromise = view === "data-memory/conversations" && this._data?.is_admin
        ? this._call("conversations", "active") : Promise.resolve({active: []});
      // Attach rejection handlers immediately so independent History work can run
      // while the selected archive scope is still resolving.
      const prerequisites = Promise.allSettled([
        scopePromise, configPromise, activeConversationsPromise,
      ]);
      if (needsScopes) await scopePromise;
      if (loadToken !== this._loadToken) return;
      const scopedCollection = async () => {
        if (prefetchedScopedCollection && initialScopeId === this._scopeId) {
          const settled = await prefetchedScopedCollection;
          if (settled.status === "rejected") throw settled.reason;
          return settled.value;
        }
        return loadScopedCollection(this._scopeId);
      };
      let result;
      let contentData = null;
      let usageSecondary = null;
      if (view === "overview") {
        this._markColdLifecycle("overview-summary-start");
        const summary = await this._call("overview", "summary");
        this._markColdLifecycle("overview-summary-complete");
        if (loadToken !== this._loadToken) return;
        const {agent, ...overview} = summary;
        if (agent) Object.assign(this._selectedAgent(), agent);
        result = overview;
      } else if (view === "usage-maintenance/usage") {
        const summaryPromise = this._call("usage", "summary");
        const daysPromise = this._call("usage", "daily");
        const settle = (promise) => promise.then(
          (value) => ({status: "fulfilled", value}),
          (reason) => ({status: "rejected", reason}),
        );
        const runsPromise = settle(this._call("usage", "runs", { limit: 30 }));
        const retentionPromise = settle(this._call("usage", "retention"));
        const primary = await Promise.allSettled([summaryPromise, daysPromise]);
        result = {
          ...settledSectionResult([["summary", "Usage summary"], ["days", "Daily usage"]], primary),
          loading: {runs: true, retention: true},
        };
        usageSecondary = [
          ["runs", "Recent runs", runsPromise],
          ["retention", "Usage retention", retentionPromise],
        ];
      } else if (view === "data-memory/conversations") {
        const [sessions, prerequisiteResults] = await Promise.all([
          scopedCollection(),
          prerequisites,
        ]);
        if (prerequisiteResults[1].status === "rejected") {
          throw prerequisiteResults[1].reason;
        }
        if (prerequisiteResults[2].status === "rejected") {
          throw prerequisiteResults[2].reason;
        }
        const active = prerequisiteResults[2].value;
        contentData = { sessions, active };
        if (this._data?.is_admin) result = this._configData;
        else result = contentData;
      } else if (view === "data-memory/memories") {
        result = await scopedCollection();
      } else if (view === "data-memory/knowledge") {
        result = await this._call("knowledge", "list");
      } else if (view === "capabilities/guest-mode") {
        result = await this._call("guest_mode", "get");
        if (this._unsavedState?.scopes.get("capabilities/guest-mode")?.agent !== this._agentId) this._guestDraft = JSON.parse(JSON.stringify(result.config || {}));
        if (!result.legacy_policy) {
          this._guestMigrationReview = false;
          this._guestStartingFresh = false;
        }
      } else if (view === "capabilities/request-rules") {
        result = await this._call("request_rules", "list");
      } else if (this._isDraftView()) {
        await this._loadConfigDraft();
        result = this._configData;
      } else {
        result = null;
      }
      if (loadToken !== this._loadToken) return;
      if (cacheGeneration !== this._cacheGeneration) return;
      this._contentData = contentData;
      if (showCached && JSON.stringify(result) === JSON.stringify(cache.result)) result = cache.result;
      this._result = result;
      writeSectionCache(this, cacheKey, result);
      this._error = null;
      if (usageSecondary) {
        for (const [key, label, pending] of usageSecondary) {
          void pending.then((settled) => {
            if (loadToken !== this._loadToken || cacheGeneration !== this._cacheGeneration
                || this._viewKey() !== "usage-maintenance/usage") return;
            const errors = (this._result?.load_errors || []).filter((issue) => issue.key !== key);
            const loading = {...(this._result?.loading || {}), [key]: false};
            if (settled.status === "fulfilled") {
              this._result = {...(this._result || {}), [key]: settled.value, load_errors: errors, loading};
            } else {
              this._result = {
                ...(this._result || {}),
                load_errors: [...errors, {
                  key,
                  label,
                  message: settled.reason?.message || String(settled.reason || "Unknown error"),
                }],
                loading,
              };
            }
            this._render();
          });
        }
      }
    } catch (err) {
      if (loadToken === this._loadToken) this._error = err.message || String(err);
    } finally {
      if (loadToken === this._loadToken) {
        this._busy = false;
        this._render();
        if (this._isDraftView() && this._configData && this._draftAgentId === this._agentId) {
          void this._loadConfigurationLiveMetadata(view);
        }
      }
    }
  }

  _configurationLiveMetadataKeys(view = this._viewKey()) {
    return {
      "capabilities/home-assistant": ["local_handling"],
      "assistant/prompt-context": ["exposed_attribute_catalog"],
    }[view] || [];
  }

  _applyConfigurationLiveMetadata(view = this._viewKey()) {
    const cache = this._eocLiveMetadataCache;
    if (!cache || !this._configData) return false;
    let changed = false;
    for (const key of this._configurationLiveMetadataKeys(view)) {
      const record = cache.get(key);
      if (!record || record.agentId !== this._agentId
          || record.epoch !== (this._eocLiveMetadataEpoch || 0)
          || record.revision !== this._configData.revision
          || this._configData[key] !== undefined) continue;
      this._configData = {...this._configData, [key]: record.value};
      changed = true;
    }
    return changed;
  }

  async _loadConfigurationLiveMetadata(view = this._viewKey()) {
    if (!this._configData || this._draftAgentId !== this._agentId) return;
    const requested = this._configurationLiveMetadataKeys(view);
    if (!requested.length) return;
    this._eocLiveMetadataCache ||= new Map();
    this._eocLiveMetadataPending ||= new Map();
    const agentId = this._agentId;
    const epoch = this._eocLiveMetadataEpoch || 0;
    const revision = this._configData.revision;
    const activeToken = this._viewKey() === view ? this._loadToken : null;
    const pending = requested.filter(key => this._configData[key] === undefined).map(key => {
      const cached = this._eocLiveMetadataCache.get(key);
      if (cached?.agentId === agentId && cached.epoch === epoch && cached.revision === revision) return null;
      const existing = this._eocLiveMetadataPending.get(key);
      if (existing?.agentId === agentId && existing.epoch === epoch && existing.revision === revision) {
        if (activeToken !== null) existing.activeToken = activeToken;
        return existing.promise;
      }
      const record = {agentId, epoch, revision, activeToken, promise: null};
      record.promise = this._call("configuration", "live_metadata", {metadata_keys: [key]})
        .then(metadata => {
          if (agentId !== this._agentId || epoch !== (this._eocLiveMetadataEpoch || 0)
              || revision !== this._configData?.revision || this._draftAgentId !== agentId
              || !Object.hasOwn(metadata, key)) return;
          this._eocLiveMetadataCache.set(key, {agentId, epoch, revision, value: metadata[key]});
          if (this._viewKey() !== view || record.activeToken !== this._loadToken) return;
          const previous = this._configData;
          if (this._applyConfigurationLiveMetadata(view) && this._result === previous) {
            this._result = this._configData;
            this._render();
          }
        })
        .catch(() => {})
        .finally(() => {
          if (this._eocLiveMetadataPending.get(key) === record) this._eocLiveMetadataPending.delete(key);
        });
      this._eocLiveMetadataPending.set(key, record);
      return record.promise;
    }).filter(Boolean);
    await Promise.all(pending);
  }

  async _loadConfigDraft() {
    if (!this._configData || this._draftAgentId !== this._agentId) {
      const agentId = this._agentId;
      const loadToken = this._loadToken;
      const configData = await this._call("configuration", "get");
      if (agentId !== this._agentId || loadToken !== this._loadToken) return;
      this._configData = configData;
      this._eocLiveMetadataEpoch = (this._eocLiveMetadataEpoch || 0) + 1;
      this._eocLiveMetadataCache = new Map();
      this._eocLiveMetadataPending = new Map();
      this._draft = JSON.parse(JSON.stringify(configData.config));
      this._draftTitle = configData.title;
      this._draftAgentId = agentId;
      this._setConfigDirty(false);
    }
    this._result = this._configData;
  }

  async _navigate(page, subsection = null) {
    const targetSubsection = subsection || this._visibleSubsections(page)[0]?.id || null;
    const destination = targetSubsection ? `${page}/${targetSubsection}` : page;
    if (!await confirmStateSafeNavigation(this, destination)) {
      const local = this.shadowRoot?.querySelector?.("#local-section");
      const top = this.shadowRoot?.querySelector?.("#top-section-mobile");
      if (local) local.value = this._subsection;
      if (top) top.value = this._page;
      return;
    }
    return trackAsync(this, NAVIGATION_MARK_PREFIX, async () => {
      const metadata = pageMetadata(page);
      const resolvedSubsection = subsection || this._visibleSubsections(page)[0]?.id || metadata.sections[0]?.id || null;
      if (this._isDraftView() && !this._isDraftView(page, resolvedSubsection)) {
        this._clearConfigDraft();
      }
      this._page = page;
      this._subsection = resolvedSubsection;
      this._query = "";
      history.pushState({}, "", routePath(page, resolvedSubsection));
      this._result = null;
      await this._loadSection();
    }, true);
  }

  _render(...args) {
    this._markColdLifecycle("first-render-start");
    getRouteFeature("usage-maintenance/diagnostics")?.stopDiagnosticsWatch(this);
    const view = this._viewKey?.() || null;
    const busy = Boolean(this._busy);
    const measure = startMeasure(this, RENDER_MARK_PREFIX);
    try {
      const root = this.shadowRoot;
      const main = root?.querySelector?.("[data-eoc-main]") || root?.querySelector?.("main");
      const preserve = Boolean(
        this._busy
        && this._eocNavigationDepth > 0
        && main
        && main.childNodes.length
        && !main.querySelector?.(".loading")
      );
      let result;
      if (preserve) {
        main.setAttribute("aria-busy", "true");
        main.inert = true;
        main.classList.add("eoc-loading-in-background");
      } else {
        result = this._renderContent(...args);
        if (view === "data-memory/conversations") {
          getRouteFeature(view)?.decorateConversationPager(this);
        }
      }
      if (view === "capabilities/functions") getRouteFeature(view)?.bindFunctionRepair(this);
      if (view === "usage-maintenance/request-debug") getRouteFeature(view)?.bindManagementDebug(this);
      if (view === "usage-maintenance/diagnostics") getRouteFeature(view)?.enhanceDiagnostics(this);
      if (preserve) return undefined;
      return result;
    } finally {
      const main = this.shadowRoot?.querySelector?.("[data-eoc-main]")
        || this.shadowRoot?.querySelector?.("main");
      if (main && !this._busy) {
        main.removeAttribute("aria-busy");
        main.inert = false;
        main.classList.remove("eoc-loading-in-background");
      }
      syncAgentPicker(this);
      finishMeasure(measure, {view, busy});
      this._markColdLifecycle("first-render-complete");
    }
  }

  _renderContent() {
    const view = this._viewKey();
    const ownsPageDraft = ["capabilities/guest-mode", "capabilities/quiet-hours", "capabilities/request-rules"].includes(view);
    if (ownsPageDraft) initializePageDraft(this);
    renderManagement(this);
    const main = this.shadowRoot?.querySelector?.("[data-eoc-main]") || this.shadowRoot?.querySelector?.("main");
    const routeTitle = main?.querySelector?.(".page-intro h1");
    if (routeTitle && this._markColdLifecycle("route-title-present", {view: this._viewKey()})) {
      requestAnimationFrame(() => this._markColdLifecycle("route-title-next-frame", {view: this._viewKey()}));
    }
    if (this._viewKey() === "overview" && main?.querySelector?.(".dashboard-grid")
        && this._markColdLifecycle("overview-content-present")) {
      requestAnimationFrame(() => this._markColdLifecycle("overview-content-next-frame"));
    }
    if (view === "capabilities/request-rules") {
      bindRequestRuleSearch(this);
      applyRequestRuleSearch(this);
    }
    if (ownsPageDraft) refreshPageSaveBar(this);

    // Keep the former decorator ordering explicit without mutating class methods at runtime.
    this._enhanceSubsectionNavigation();
    navigationSearchModule?.enhanceNavigationSearch(this);
    enhanceConfigurationClarity(this);
    const ownsConfigurationGuidance = (
      (routeAssetKind(view) === "agent-config"
        && view !== "capabilities/functions"
        && (view !== "data-memory/conversations" || this._data?.is_admin))
      || view === "data-memory/memory-settings"
    );
    if (ownsConfigurationGuidance) {
      getRouteFeature("configuration")?.enhanceConfigurationGuidance(this);
    }
    if (this._page === "overview") queueMicrotask(() => enhanceOverviewHealthClarity(this));
  }

  _renderShell() {
    this._markColdLifecycle("shell-start");
    this._eocShellRevision = (this._eocShellRevision || 0) + 1;
    const agent = this._selectedAgent();
    const navigation = NAVIGATION.filter((item) => this._canAccessView(item.id));
    const local = this._visibleSubsections();
    const currentSection = local.find((item) => item.id === this._subsection);
    this._eocMainMarkup = !agent ? this._empty("No conversation agents configured.") : this._busy ? this._loadingContent(agent) : this._error ? `<div class="error" role="alert">${this._e(this._error)}</div>` : this._content(agent);
    const configurationActions = agent && !this._busy && !this._error ? this._configurationActions() : "";
    this._eocDialogMarkup = this._dialogs();
    this._eocRenderedRoute = `${this._agentId}|${this._viewKey()}`;
    this.shadowRoot.innerHTML = `
      <style data-eoc-critical-styles>${CRITICAL_STYLE}</style>
      <link rel="stylesheet" href="${MANAGEMENT_STYLESHEET_URL}" data-eoc-persistent-styles>
      <div class="page-shell" data-eoc-persistent-shell>
        <header>
          <div class="page-heading"><h1>Extended OpenAI</h1><p>Configure your assistant, capabilities, retained data, and maintenance.</p></div>
          ${settingsSearchShellMarkup(this)}
        </header>
        <label class="mobile-nav"><span>Page</span><select id="top-section-mobile">${navigation.map((item) => `<option value="${item.id}" ${item.id === this._page ? "selected" : ""}>${item.label}</option>`).join("")}</select></label>
        <div class="eoc-agent-context-row" aria-label="Assistant context">
          <label class="agent-picker"><span>Conversation agent</span><select id="agent">${(this._data?.agents || []).map((a) => `<option value="${this._e(a.subentry_id)}" ${a.subentry_id === this._agentId ? "selected" : ""}>${this._e(a.title)}</option>`).join("")}</select>${agent ? `<small>${this._e(agent.provider)} · ${this._e(agent.model)}</small>` : ""}</label>
          <div id="eoc-agent-actions-host" class="eoc-agent-actions" ${configurationActions ? "" : "hidden"}>${configurationActions}</div>
        </div>
        <nav class="top-nav" aria-label="Management sections">${navigation.map((item) => `<button type="button" data-page="${item.id}" class="${item.id === this._page ? "active" : ""}" ${item.id === this._page ? 'aria-current="page"' : ""}>${item.label}</button>`).join("")}</nav>
        <nav class="subsection-nav" aria-label="${this._e(pageMetadata(this._page).label)} sections" ${local.length > 1 ? "" : "hidden"}>${local.length > 1 ? local.map((item) => `<button type="button" data-subsection="${this._e(item.id)}" class="${item.id === this._subsection ? "active" : ""}" ${item.id === this._subsection ? 'aria-current="page"' : ""}>${this._e(item.label)}</button>`).join("") : ""}</nav>
        <div id="eoc-scope-host">${["data-memory/conversations", "data-memory/memories"].includes(this._viewKey()) ? this._scopePicker() : ""}</div>
        <div id="eoc-section-host">${local.length > 1 ? `<div class="section-selector"><label><span>${this._e(pageMetadata(this._page).label)} section</span><select id="local-section">${local.map((item) => `<option value="${item.id}" ${item.id === this._subsection ? "selected" : ""}>${item.label}</option>`).join("")}</select></label><p>${this._e(currentSection?.description || "")}</p></div>` : ""}</div>
        <div class="section-layout">
          <main data-eoc-main ${this._page === "guide" ? 'data-eoc-guide-layout=""' : ""}>${this._eocMainMarkup}</main>
        </div>
      </div>
      <div id="eoc-dialog-host">${this._eocDialogMarkup}</div>
      <div id="toast" class="toast" role="status" aria-live="polite"></div>`;
    this._bindActions();
    this._markColdLifecycle("shell-complete");
    if (this.shadowRoot.querySelector(".page-heading h1")
        && this._markColdLifecycle("shell-title-present")) {
      requestAnimationFrame(() => this._markColdLifecycle("shell-next-frame"));
    }
  }

  _reconcileCollectionView() {
    const view = this._viewKey();
    if (view === "data-memory/knowledge") return getRouteFeature(view)?.reconcileKnowledge(this) || false;
    if (view === "data-memory/memories") return getRouteFeature(view)?.reconcileMemories(this) || false;
    if (view === "capabilities/request-rules") return getRouteFeature(view)?.reconcileRequestRules?.(this) || false;
    if (view !== "capabilities/functions") return false;
    const repair = getRouteFeature(view);
    if (repair?.repairIssue(this) && repair.repairMetadata(this)?.isolatable === false) return false;
    return getConfigurationTools()?.reconcileTools?.(this, {repairCards: repair?.renderFunctionRepairCards(this) || ""}) || false;
  }

  _loadingContent(agent) {
    if (this._viewKey() === "capabilities/home-assistant") {
      return `${this._homeAssistantIntro()}${this._loading()}`;
    }
    return this._loading();
  }

  _content(agent) {
    const view = this._viewKey();
    if (!routeFeaturesReady(view)) return this._loadingContent(agent);
    if (view === "data-memory/memory-settings") return getRouteFeature(view)?.renderMemorySettings(this) || this._loading();
    if (view === "capabilities/home-assistant") {
      this._configSections = ["local"];
      return `${this._homeAssistant(agent)}${(getConfigurationEditor()?.renderConfiguration(this) || this._loading())}`;
    }
    if (view === "capabilities/web-skills") {
      this._configSections = ["capabilities"];
      return (getConfigurationEditor()?.renderConfiguration(this) || this._loading());
    }
    if (!this._canAccessView(this._page, this._subsection)) return this._empty("Administrator permission is required for this section.");
    if (view === "overview") return renderOverview(this, agent);
    if (view === "guide") return renderGuide(this);
    if (this._page === "assistant") {
      this._configSections = this._configSectionsForView();
      const voiceIdentity = view === "assistant/voice" ? getRouteFeature(view)?.renderVoiceIdentityCore : null;
      const specialized = getRouteFeature(view);
      return (getConfigurationEditor()?.renderConfiguration(this, {
        voiceIdentity,
        renderExposedAttributes: view === "assistant/prompt-context" ? () => `<div class="exposed-attribute-settings" data-exposed-feature style="min-height:96px;padding:16px 0 4px;border-top:1px solid var(--divider-color)"><h3>Additional entity attributes</h3><p class="help">Loading Assist-exposed entity choices…</p></div>` : specialized?.renderExposedAttributeSettings,
      }) || this._loading());
    }
    if (view === "capabilities/request-rules") return getRouteFeature("capabilities/request-rules")?.renderRequestRules(this) || this._loading();
    if (view === "capabilities/functions") {
      const repair = getRouteFeature(view);
      const issue = repair?.repairIssue(this);
      if (issue && repair.repairMetadata(this)?.isolatable === false) return repair.renderFallbackRepair(this, issue);
      const repairCards = issue ? repair.renderFunctionRepairCards(this) : "";
      return `<button type="button" class="guide-topic-link guide-link" data-guide-topic="functions">What are Function Groups?</button>${(getConfigurationTools()?.renderTools(this, {repairCards}) || this._loading())}`;
    }
    if (view === "capabilities/quiet-hours") return getRouteFeature(view)?.renderQuietHours(this) || this._loading();
    if (view === "usage-maintenance/request-debug") return getRouteFeature(view)?.renderManagementDebug(this) || this._loading();
    if (view === "capabilities/guest-mode") return this._guestMode();
    if (view === "data-memory/memories") return `<button type="button" class="guide-topic-link guide-link" data-guide-topic="memory">Learn about memory</button>${this._memories()}`;
    if (view === "data-memory/knowledge") return `${getRouteFeature("capabilities")?.knowledgeAvailabilityMarkup(this)}<button type="button" class="guide-topic-link guide-link" data-guide-topic="knowledge">Learn about Knowledge</button>${this._knowledge()}`;
    if (view === "data-memory/conversations") { this._configSections = ["archive"]; return `${this._conversations()}${this._data?.is_admin ? ((getConfigurationEditor()?.renderConfiguration(this) || this._loading())) : ""}`; }
    if (view === "usage-maintenance/usage") return this._usage();
    if (view === "usage-maintenance/diagnostics") return this._diagnostics(agent);
    if (["usage-maintenance/backup-restore", "usage-maintenance/retention"].includes(view)) {
      this._configSections = this._configSectionsForView();
      const specialized = getRouteFeature(view);
      return (getConfigurationEditor()?.renderConfiguration(this, {
        renderBackup: specialized?.renderBackupTransferPanel,
      }) || this._loading());
    }
    return this._empty("This section is not available.");
  }

  _homeAssistantIntro() {
    return `<section class="page-intro"><h1>Home Assistant access</h1><p>Home Assistant controls which entities this assistant is allowed to access through Assist. Extended OpenAI can also automatically include exposed entity names and current states in the context sent to the model.</p></section>`;
  }

  _homeAssistant(agent) {
    const contextIncluded = this._draft?.exposed_entities_enabled === true;
    return `${this._homeAssistantIntro()}<section class="content-card access-explainer"><div><h2>Entity access</h2><p>Home Assistant's Assist exposure settings decide which entities may be used by the assistant. Manage exposure in Home Assistant's voice assistant settings.</p></div><div class="compact-status"><span><strong>Include exposed entity states in the prompt</strong><small>Adds exposed entity names and current states to the context sent with each request. Turning this off does not necessarily prevent the assistant from using exposed entities through Home Assistant tools.</small></span><strong class="status-value ${contextIncluded ? "on" : ""}">${contextIncluded ? "On" : "Off"}</strong></div><button type="button" class="secondary inline-route" data-page="assistant" data-subsection="prompt-context">Configure exposed entity context</button></section><section class="notice"><strong>Guest Mode adds another boundary</strong><p>Guest Mode applies additional restrictions to the assistant's normal Home Assistant access.</p><button type="button" class="secondary inline-route" data-page="capabilities" data-subsection="guest-mode">Configure Guest Mode</button></section>`;
  }

  _usage() {
    const usage = getRouteFeature("usage-maintenance/usage");
    return usage ? usage.renderUsagePage(this, this._result || {}) : this._loading();
  }

  _conversations() {
    const result = this._contentData || this._result || {};
    const active = result.active?.active || [];
    const content = `${this._data?.is_admin ? `<p class="help">Recent context lets conversations continue; saved history is the archive you can review or search.</p>` : ""}${this._data?.is_admin && active.length ? `<section class="content-card"><div class="section-heading"><div><h2>Active conversations</h2><p>Recent conversations that can continue when the same user or voice device speaks again.</p></div></div><div class="list">${active.map((item) => `<article class="list-card"><div class="card-main"><h3>${this._e(item.label)}</h3><p class="meta">Last active ${this._e(this._formatDate(item.last_active))} · Expires ${this._e(this._formatDate(item.expires_at))}</p></div><div class="actions"><button type="button" class="danger end-active" data-key="${this._e(item.key)}">Start fresh next time</button></div></article>`).join("")}</div></section>` : ""}
      <section class="content-card"><div class="section-heading"><div><h2>Retained conversations</h2><p>Search and review conversations for the selected scope.</p></div></div><div class="search-row"><input id="archive-query" type="search" placeholder="Search retained discussions" aria-label="Search retained discussions"><button type="button" id="archive-search">Search</button></div><div class="list">${(result.sessions?.sessions || []).map((item) => `<article class="list-card"><div class="card-main clickable open-session" tabindex="0" role="button" data-id="${this._e(item.session_id)}"><h3>${this._e(item.title || "Untitled conversation")}</h3><p class="meta">${this._e(this._formatDate(item.last_message_at))} · ${this._e(String(item.turn_count))} turns · ${this._e(item.scope_source)}</p></div><div class="actions"><button type="button" class="secondary view-session" data-id="${this._e(item.session_id)}">View</button><button type="button" class="danger delete-session" data-id="${this._e(item.session_id)}">Delete</button></div></article>`).join("") || this._empty("No retained conversations in this scope.")}</div></section>`;
    return getRouteFeature("memory-browser")?.decorateConversations(this, content);
  }

  _memories() {
    if (this._memoryKind === "temporary") return getRouteFeature("data-memory/memories")?.renderTemporaryMemories(this);
    return getRouteFeature("memory-browser")?.renderPersistentMemories(this);
  }

  _knowledge() {
    return getRouteFeature("data-memory/knowledge")?.renderKnowledge(this) || this._loading();
  }

  _guestMode() {
    return getRouteFeature("capabilities/guest-mode")?.guestMode(this) || this._loading();
  }

  _guestPolicyView(status, policy, state) {
    return getRouteFeature("capabilities/guest-mode")?.guestPolicyView(this, status, policy, state) || this._loading();
  }

  _setupGuestSelectors() {
    const config = this._guestDraft || {};
    const result = this._result || {};
    const select = (items, value, label) => ({select: {multiple: true, custom_value: false, options: items.map((item) => ({value: value(item), label: label(item)}))}});
    this.shadowRoot.querySelectorAll("ha-selector[data-guest-key]").forEach((element) => {
      const type = element.dataset.guestSelector;
      element.hass = this.hass;
      element.value = config[element.dataset.guestKey] || [];
      element.selector = type === "entity" ? {entity: {multiple: true}} : type === "area" ? {area: {multiple: true}} : type === "label" ? {label: {multiple: true}} : type === "domain" ? select(result.domains || [], (item) => item, (item) => item) : type === "knowledge" ? select(result.knowledge_sources || [], (item) => item.source_id, (item) => `${item.title} — ${item.description || "No description"}`) : type === "group" ? select(result.function_groups || [], (item) => item.id, (item) => `${item.name} — ${item.description}`) : select((result.functions || []).filter((item) => !item.unsafe_in_guest_mode), (item) => item.name, (item) => `${item.name}${item.enabled ? "" : " (disabled)"} — ${item.description || "No description"}`);
      element.addEventListener("value-changed", (event) => {
        config[element.dataset.guestKey] = event.detail.value || [];
        queueMicrotask(() => refreshPageSaveBar(this));
      });
    });
  }

  async _saveGuestPolicy() {
    return savePageChanges(this);
  }

  async _startFreshGuestPolicy() {
    if (!await this._confirm(
      "Start a fresh Guest policy?",
      "Starting fresh means guests will be able to use all Home Assistant entities normally available to this assistant unless you add exclusions. The existing policy remains enforced until you save.",
      "Start fresh",
    )) return;
    this._guestDraft = getRouteFeature("memory-browser")?.freshGuestPolicyDraft(this._guestDraft);
    this._guestMigrationReview = true;
    this._guestStartingFresh = true;
    this._render();
  }

  _dateTimeLocal(value) {
    if (!value) return "";
    const date = new Date(value);
    const shifted = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
    return shifted.toISOString().slice(0, 16);
  }

  _runGuestOperation(operation) {
    if (this._guestOperation) return this._guestOperation;
    const pending = Promise.resolve().then(operation);
    this._guestOperation = pending;
    syncAgentPicker(this);
    return pending.finally(() => {
      this._guestOperation = null;
      syncAgentPicker(this);
    });
  }

  _patchGuestModeStatus(agentId, status) {
    if (!agentId || !status) return;
    const agent = this._data?.agents?.find((item) => item.subentry_id === agentId);
    if (agent) agent.guest_mode = {...(agent.guest_mode || {}), ...status};
    if (this._agentId === agentId && this._viewKey() === "capabilities/guest-mode" && this._result) {
      this._result = {...this._result, status: {...(this._result.status || {}), ...status}};
    }
  }

  async _refreshGuestModeMutation(agentId, mutationResult) {
    this._patchGuestModeStatus(agentId, mutationResult?.status);
    if (this._agentId !== agentId || this._viewKey() !== "capabilities/guest-mode") return;
    const result = await this._call("guest_mode", "get");
    if (this._agentId !== agentId || this._viewKey() !== "capabilities/guest-mode") return;
    this._patchGuestModeStatus(agentId, result?.status);
    this._result = result;
    this._error = null;
    this._render();
  }

  _updateGuestMode(now = false) {
    return this._runGuestOperation(async () => {
      const root = this.shadowRoot;
      const agentId = this._agentId;
      const indefinite = root.querySelector("#guest-indefinite")?.checked ?? true;
      const start = now ? new Date().toISOString() : root.querySelector("#guest-start")?.value;
      const end = root.querySelector("#guest-end")?.value;
      try {
        const result = await this._call("guest_mode", "update", {
          ...(start ? {active_from: start} : {}),
          ...(!indefinite && end ? {active_until: end} : {}),
          indefinite: indefinite || !end,
        });
        await this._refreshGuestModeMutation(agentId, result);
        this._toast("Guest Mode updated");
      } catch (err) {
        this._toast(`Unable to update Guest Mode: ${err.message || String(err)}`, true);
      }
    });
  }

  _disableGuestMode() {
    return this._runGuestOperation(async () => {
      if (!await this._confirm("End Guest Mode?", "This immediately ends an active interval or cancels a future schedule.", "End Guest Mode")) return;
      const agentId = this._agentId;
      try {
        const result = await this._call("guest_mode", "disable");
        await this._refreshGuestModeMutation(agentId, result);
        this._toast("Guest Mode ended");
      } catch (err) {
        this._toast(`Unable to end Guest Mode: ${err.message || String(err)}`, true);
      }
    });
  }

  _diagnostics(agent) {
    return getRouteFeature("status")?.diagnosticsMarkup(this, agent);
  }

  _dialogs() {
    const content = `<dialog id="knowledge-dialog" class="editor-dialog wide" aria-labelledby="knowledge-dialog-title"><form id="knowledge-form"><div class="dialog-header"><h2 id="knowledge-dialog-title">Add Knowledge source</h2><button type="button" class="icon close-editor" aria-label="Close">×</button></div><div class="dialog-body"><label>Title<input id="knowledge-title" maxlength="${KNOWLEDGE_TITLE_LIMIT}" required></label><label>Description<textarea id="knowledge-description" class="short-textarea" maxlength="${KNOWLEDGE_DESCRIPTION_LIMIT}" spellcheck="true"></textarea></label>${knowledgeSourceAvailabilityControl()}<label>Content<textarea id="knowledge-content" class="knowledge-editor" maxlength="${KNOWLEDGE_LIMIT}" required spellcheck="true"></textarea></label><div id="knowledge-counter" class="counter">0 / ${KNOWLEDGE_LIMIT.toLocaleString()} characters</div><div id="knowledge-error" class="inline-error" role="alert"></div></div><div class="dialog-actions"><button type="button" id="knowledge-delete" class="danger" hidden>Delete</button><button type="button" class="secondary close-editor">Cancel</button><button type="submit" id="knowledge-save">Save</button></div></form></dialog>
      <dialog id="memory-dialog" class="editor-dialog" aria-labelledby="memory-dialog-title"><form id="memory-form"><div class="dialog-header"><h2 id="memory-dialog-title">Add memory</h2><button type="button" class="icon close-editor" aria-label="Close">×</button></div><div class="dialog-body"><label>Memory<textarea id="memory-content" required spellcheck="true" placeholder="What should the agent remember?"></textarea></label><label>Category<input id="memory-category" value="general" required></label><div id="memory-metadata"></div><p id="memory-meta" class="meta"></p><div id="memory-error" class="inline-error" role="alert"></div></div><div class="dialog-actions"><button type="button" id="memory-delete" class="danger" hidden>Delete</button><button type="button" class="secondary close-editor">Cancel</button><button type="submit" id="memory-save">Save</button></div></form></dialog>
      <dialog id="session-dialog" class="editor-dialog wide" aria-labelledby="session-title"><div class="dialog-header"><h2 id="session-title">Conversation</h2><button type="button" class="icon close-session" aria-label="Close">×</button></div><div id="session-body" class="dialog-body session-body"></div><div class="dialog-actions"><button type="button" class="secondary close-session">Close</button></div></dialog>
      <dialog id="reassign-dialog" class="editor-dialog" aria-labelledby="reassign-title"><div class="dialog-header"><h2 id="reassign-title">Assign unowned memory</h2></div><div class="dialog-body"><p class="help">Choose the user or household that should be able to use this older memory.</p><label>Assign to<select id="reassign-scope">${this._scopeOptions("memories", true, true)}</select></label></div><div class="dialog-actions"><button type="button" class="secondary" id="reassign-cancel">Cancel</button><button type="button" id="reassign-save">Assign memory</button></div></dialog>
      <dialog id="confirm-dialog" class="editor-dialog confirm-dialog" aria-labelledby="confirm-title"><div class="dialog-header"><h2 id="confirm-title">Confirm</h2></div><div class="dialog-body"><p id="confirm-message"></p></div><div class="dialog-actions"><button type="button" class="secondary" id="confirm-cancel">Cancel</button><button type="button" class="danger" id="confirm-accept">Confirm</button></div></dialog>
      ${this._viewKey() === "capabilities/request-rules" ? (getRouteFeature("capabilities/request-rules")?.requestRulesDialog(this) || "") : ""}${routeAssetKind(this._viewKey()) === "agent-config" ? getConfigurationEditor()?.configurationDialogs(this) || "" : this._viewKey() === "capabilities/functions" ? getConfigurationTools()?.configurationDialogs(this) || "" : ""}${this._viewKey() === "usage-maintenance/backup-restore" ? getRouteFeature("usage-maintenance/backup-restore")?.renderRestoreTransferDialog(this) || "" : ""}`;
    const usageDialog = this._viewKey() === "usage-maintenance/usage"
      ? getRouteFeature("usage-maintenance/usage")?.requestDetailsDialog() || "" : "";
    return `${content}${getRouteFeature("data-memory/memories")?.temporaryDialog(this) || ""}${usageDialog}`;
  }

  _bindActions() {
    const root = this.shadowRoot;
    const q = (selector) => root.querySelector(selector);
    if (!["data-memory/knowledge", "data-memory/memories"].includes(this._viewKey())) {
      q("#list-search")?.addEventListener("input", (event) => { this._query = event.target.value; this._updateVisibleList(); });
    }
    const view = this._viewKey();
    if (view === "data-memory/knowledge") getRouteFeature(view)?.bindKnowledge(this);
    if (view === "data-memory/conversations") getRouteFeature(view)?.bindConversationActions(this);
    if (view === "data-memory/conversations") {
      root.querySelectorAll(".end-active").forEach((button) => button.addEventListener("click", async () => { if (!await this._confirm("End active conversation?", "The next matching Assist request will start with fresh model context.", "End conversation")) return; await this._call("conversations", "end_active", { continuity_key: button.dataset.key }); await this._loadSection(); }));
      root.querySelectorAll(".delete-session").forEach((button) => button.addEventListener("click", (event) => { event.stopPropagation(); this._deleteSession(button.dataset.id); }));
    }
    if (view === "usage-maintenance/usage") q("#clear-details")?.addEventListener("click", () => this._clearUsageDetails());
    q("#test-agent")?.addEventListener("click", () => this._testAgent());
    if (view === "capabilities/guest-mode") {
      q("#guest-indefinite")?.addEventListener("change", (event) => { const end = q("#guest-end"); if (end) end.disabled = event.target.checked; });
      q("#guest-update")?.addEventListener("click", () => this._updateGuestMode(false));
      q("#guest-now")?.addEventListener("click", () => this._updateGuestMode(true));
      q("#guest-disable")?.addEventListener("click", () => this._disableGuestMode());
      q("#guest-review-converted")?.addEventListener("click", () => { this._guestMigrationReview = true; this._render(); });
      q("#guest-start-fresh")?.addEventListener("click", () => this._startFreshGuestPolicy());
      q("#guest-separate-control")?.addEventListener("change", (event) => { this._guestDraft.guest_separate_control_restrictions = event.target.checked; this._render(); });
      q("#guest-controls-enabled")?.addEventListener("change", (event) => { this._guestDraft.guest_mode_enabled = event.target.checked; });
      root.querySelectorAll("[data-guest-mode]").forEach((element) => element.addEventListener("change", () => { this._guestDraft[element.dataset.guestMode] = element.value; this._render(); }));
      this._setupGuestSelectors();
    }
    if (this._page === "assistant" || ["data-memory/conversations", "usage-maintenance/backup-restore", "usage-maintenance/retention"].includes(view)) getConfigurationEditor()?.bindConfiguration(this);
    if (view === "assistant/prompt-context") this._hydrateExposedAttributes();
    if (view === "usage-maintenance/backup-restore") getRouteFeature(view)?.bindBackupTransfer(this, getConfigurationEditor()?.backupSummaryLines);
    if (view === "capabilities/functions") getConfigurationTools()?.bindTools(this);
    if (view === "capabilities/request-rules") getRouteFeature(view)?.bindRequestRules(this);
    if (view === "overview") bindOverview(this);
    if (view === "guide") bindGuide(this);
    if (this._pendingSettingFocus && root.querySelector(`#${CSS.escape(this._pendingSettingFocus)}`)) {
      const target = this._pendingSettingFocus;
      this._pendingSettingFocus = null;
      requestAnimationFrame(() => { const element = this.shadowRoot.querySelector(`#${target}`); element?.scrollIntoView({behavior:"smooth", block:"start"}); (element?.querySelector("input,select,textarea,button") || element)?.focus?.(); });
    }
    if (["data-memory/memories", "data-memory/conversations", "capabilities/guest-mode"].includes(view)) {
      getRouteFeature("memory-browser")?.bindMemoryBrowser(this);
    }
    if (view === "data-memory/memories") getRouteFeature(view)?.bindTemporaryMemory(this);
    if (view === "data-memory/memory-settings") getRouteFeature(view)?.bindMemorySettings(this);
    if (["capabilities/home-assistant", "capabilities/web-skills", "data-memory/knowledge"].includes(view)) {
      getRouteFeature("capabilities")?.bindCapabilities(this);
    }
    if (view === "assistant/voice") getRouteFeature(view)?.bindVoiceIdentityCore(this);
    if (view === "capabilities/quiet-hours") getRouteFeature(view)?.bindQuietHours(this);
    if (view === "usage-maintenance/usage") {
      getRouteFeature(view)?.bindUsageDiagnostics(this);
    }
  }

  async _hydrateExposedAttributes() {
    const target = this.shadowRoot?.querySelector("[data-exposed-feature]");
    if (!target) return;
    const agentId = this._agentId;
    const revision = this._configData?.revision;
    try {
      const [feature] = await Promise.all([
        import("./exposed-attributes-ui.js"),
        this._loadConfigurationLiveMetadata("assistant/prompt-context"),
      ]);
      if (!target.isConnected || agentId !== this._agentId
          || revision !== this._configData?.revision
          || this._viewKey() !== "assistant/prompt-context") return;
      target.outerHTML = feature.renderExposedAttributeSettings(this);
      feature.bindExposedAttributeSettings(this);
    } catch (error) {
      if (target.isConnected && agentId === this._agentId
          && this._viewKey() === "assistant/prompt-context") {
        target.querySelector(".help").textContent = `Unable to load entity choices: ${error.message || String(error)}`;
      }
    }
  }

  _activate(element, callback) {
    element.addEventListener("click", (event) => { if (!event.target.closest("button") || event.currentTarget === event.target) callback(); });
    element.addEventListener("keydown", (event) => { if ((event.key === "Enter" || event.key === " ") && event.target === element) { event.preventDefault(); callback(); } });
  }

  _updateVisibleList() {
    if (this._viewKey() === "data-memory/knowledge") return getRouteFeature("data-memory/knowledge")?.filterKnowledge(this);
    if (this._viewKey() === "data-memory/memories") return this._memoryKind === "persistent"
      ? getRouteFeature("memory-browser")?.filterPersistentMemories(this)
      : getRouteFeature("data-memory/memories")?.filterTemporaryMemories(this);
    const query = this._query.trim().toLocaleLowerCase();
    this.shadowRoot.querySelectorAll(".list-card").forEach((card) => {
      card.hidden = query && !card.textContent.toLocaleLowerCase().includes(query);
    });
  }

  async _openKnowledge(sourceId = null) {
    const availability = this.shadowRoot?.querySelector("#knowledge-source-enabled");
    if (availability) availability.checked = true;
    const root = this.shadowRoot;
    const dialog = root.querySelector("#knowledge-dialog");
    const loadToken = (this._knowledgeLoadToken || 0) + 1;
    this._knowledgeLoadToken = loadToken;
    this._editingSource = null;
    this._knowledgeMode = sourceId ? "edit-loading" : "create";
    this._editorInitial = null;
    this._setDialogError("knowledge", "");
    root.querySelector("#knowledge-title").value = "";
    root.querySelector("#knowledge-description").value = "";
    root.querySelector("#knowledge-content").value = "";
    root.querySelector("#knowledge-delete").hidden = true;
    root.querySelector("#knowledge-dialog-title").textContent = sourceId ? "Loading source…" : "Add Knowledge source";
    this._setKnowledgeEditorDisabled(Boolean(sourceId));
    this._editorKind = "knowledge";
    dialog.showModal();
    if (sourceId) {
      try {
        const response = await this._call("knowledge", "get", { source_id: sourceId });
        if (this._knowledgeLoadToken !== loadToken || !dialog.open) return;
        this._editingSource = response.source;
        if (availability) availability.checked = response.source.enabled !== false;
        this._knowledgeMode = "edit";
        root.querySelector("#knowledge-title").value = response.source.title || "";
        root.querySelector("#knowledge-description").value = response.source.description || "";
        root.querySelector("#knowledge-content").value = response.source.content || "";
        root.querySelector("#knowledge-delete").hidden = false;
        root.querySelector("#knowledge-dialog-title").textContent = "Edit Knowledge source";
        this._setKnowledgeEditorDisabled(false);
      } catch (err) {
        if (this._knowledgeLoadToken !== loadToken || !dialog.open) return;
        this._knowledgeMode = "edit-error";
        this._setDialogError("knowledge", `Unable to load source: ${err.message || String(err)}`);
        root.querySelector("#knowledge-dialog-title").textContent = "Unable to load source";
      }
    }
    if (["create", "edit"].includes(this._knowledgeMode)) {
      this._editorInitial = this._knowledgeValues();
    }
    this._updateKnowledgeCounter();
    // The dialog and fields are ready; a deferred focus can steal later input.
    (this._knowledgeMode === "edit-error" ? dialog.querySelector(".close-editor") : root.querySelector("#knowledge-title")).focus();
  }

  async _openMemory(memoryId = null) {
    const root = this.shadowRoot;
    const memory = getRouteFeature("memory-browser")?.findPersistentMemory(this, memoryId) || null;
    this._editingMemory = memory ? {...memory} : null;
    this._memoryEditorScope = this._scopeId;
    this._memoryEditorAgent = this._agentId;
    this._editorKind = "memory";
    root.querySelector("#memory-dialog-title").textContent = memory ? "Edit memory" : "Add memory";
    root.querySelector("#memory-content").value = memory?.content || "";
    root.querySelector("#memory-category").value = memory?.category || "general";
    getRouteFeature("data-memory/memories").populateMemoryMetadata(this, memory);
    root.querySelector("#memory-delete").hidden = !memory;
    root.querySelector("#memory-meta").textContent = memory ? [memory.source, memory.created_at ? `Created ${this._formatDate(memory.created_at)}` : "", memory.updated_at ? `Updated ${this._formatDate(memory.updated_at)}` : ""].filter(Boolean).join(" · ") : "Categories help organise memories.";
    this._setDialogError("memory", "");
    this._editorInitial = this._memoryValues();
    root.querySelector("#memory-dialog").showModal();
    root.querySelector("#memory-content").focus();
  }

  async _requestEditorClose() {
    return this._confirmEditorClose(this.shadowRoot.querySelector(`#${this._editorKind}-dialog`));
  }

  _setKnowledgeEditorDisabled(disabled) {
    const availability = this.shadowRoot?.querySelector("#knowledge-source-enabled");
    if (availability) availability.disabled = disabled;
    const root = this.shadowRoot;
    ["#knowledge-title", "#knowledge-description", "#knowledge-content", "#knowledge-save"].forEach((selector) => {
      root.querySelector(selector).disabled = disabled;
    });
  }

  _knowledgeValues() {
    const root = this.shadowRoot;
    return { enabled: root.querySelector("#knowledge-source-enabled")?.checked ?? true, title: root.querySelector("#knowledge-title").value, description: root.querySelector("#knowledge-description").value, content: root.querySelector("#knowledge-content").value };
  }

  _memoryValues() {
    const root = this.shadowRoot;
    return { content: root.querySelector("#memory-content").value, category: root.querySelector("#memory-category").value, ...getRouteFeature("data-memory/memories").memoryMetadataValues(this) };
  }

  async _saveKnowledge() {
    const button = this.shadowRoot.querySelector("#knowledge-save");
    if (button.disabled || !["create", "edit"].includes(this._knowledgeMode)) return;
    const values = this._knowledgeValues();
    const editing = this._knowledgeMode === "edit";
    this._setSaving(button, true);
    try {
      await this._call("knowledge", editing ? "update" : "create", { ...(editing ? { source_id: this._editingSource.source_id } : {}), ...values });
      this.shadowRoot.querySelector("#knowledge-dialog").close();
      await this._refreshAfterMutation();
      this._toast(editing ? "Knowledge source updated" : "Knowledge source saved");
    } catch (err) {
      this._setDialogError("knowledge", `Unable to save source: ${err.message || String(err)}`);
    } finally { this._setSaving(button, false); }
  }

  async _saveMemory() {
    const button = this.shadowRoot.querySelector("#memory-save");
    if (button.disabled) return;
    const values = this._memoryValues();
    this._setSaving(button, true);
    try {
      if (this._memoryEditorAgent !== this._agentId || this._memoryEditorScope !== this._scopeId) throw new Error("The selected agent or scope changed. Close and reopen this editor.");
      if (this._editingMemory && !this._editingMemory.revision) throw new Error("Refresh the Memory list and reopen this editor before saving.");
      await this._call("memories", this._editingMemory ? "update" : "add", {
        scope_id: this._memoryEditorScope,
        ...(this._editingMemory ? {memory_id: this._editingMemory.memory_id, expected_revision: this._editingMemory.revision} : {}),
        ...getRouteFeature("data-memory/memories").memoryMutationValues(values, this._editingMemory),
      });
      this.shadowRoot.querySelector("#memory-dialog").close();
      await this._refreshAfterMutation();
      this._toast(this._editingMemory ? "Memory updated" : "Memory added");
    } catch (err) {
      this._setDialogError("memory", `Unable to save memory: ${err.message || String(err)}`);
    } finally { this._setSaving(button, false); }
  }

  async _deleteSource(sourceId, fromDialog = false) {
    if (!sourceId || !await this._confirm("Delete Knowledge source?", "This permanently removes the selected source from this agent's local Knowledge Library.", "Delete")) return;
    try {
      await this._call("knowledge", "delete", { source_id: sourceId, confirm: true });
      if (fromDialog) this.shadowRoot.querySelector("#knowledge-dialog").close();
      await this._refreshAfterMutation();
      this._toast("Knowledge source deleted");
    } catch (err) { this._toast(`Unable to delete source: ${err.message || String(err)}`, true); }
  }

  async _deleteMemory(memoryId, fromDialog = false) {
    if (!memoryId || !await this._confirm("Delete memory?", "This memory will be permanently removed from the selected scope.", "Delete")) return;
    try {
      await this._call("memories", "delete", { scope_id: this._scopeId, memory_id: memoryId });
      if (fromDialog) this.shadowRoot.querySelector("#memory-dialog").close();
      await this._refreshAfterMutation();
      this._toast("Memory deleted");
    } catch (err) { this._toast(`Unable to delete memory: ${err.message || String(err)}`, true); }
  }

  async _deleteSession(sessionId) {
    if (!await this._confirm("Delete conversation?", "This retained conversation and its turns will be permanently removed.", "Delete")) return;
    try { await this._call("conversations", "delete", { scope_id: this._scopeId, session_id: sessionId }); await this._refreshAfterMutation(); this._toast("Conversation deleted"); }
    catch (err) { this._toast(`Unable to delete conversation: ${err.message || String(err)}`, true); }
  }

  _openReassign(memoryId) {
    this._reassignMemoryId = memoryId;
    this.shadowRoot.querySelector("#reassign-dialog").showModal();
  }

  async _saveReassign() {
    const target = this.shadowRoot.querySelector("#reassign-scope").value;
    if (!target) return;
    try {
      const result = await this._call("memories", "reassign_legacy", { scope_id: "__anonymous__", target_scope_id: target, memory_ids: [this._reassignMemoryId] });
      this.shadowRoot.querySelector("#reassign-dialog").close();
      await this._refreshAfterMutation();
      this._toast(`Reassigned ${formatUsageNumber(result.reassigned)} memory record${result.reassigned === 1 ? "" : "s"}`);
    } catch (err) { this._toast(`Unable to reassign memory: ${err.message || String(err)}`, true); }
  }

  async _clearUsageDetails() {
    if (!await this._confirm("Clear recent usage details?", "Request and run details will be removed. Daily, monthly, and lifetime totals remain.", "Clear details")) return;
    try { await this._call("usage", "clear_details", { confirm: true }); await this._loadSection(); this._toast("Recent usage details cleared"); }
    catch (err) { this._toast(`Unable to clear details: ${err.message || String(err)}`, true); }
  }

  _testAgent() {
    return getRouteFeature("status")?.testAgent(this);
  }

  async _refreshAfterMutation() {
    await this._loadSection(true);
  }

  _confirm(title, message, confirmLabel = "Confirm") {
    const subject = this._eocDecisionConfirmSubject || "";
    this._eocDecisionConfirmSubject = "";
    const root = this.shadowRoot;
    root.querySelector("#confirm-title").textContent = title;
    root.querySelector("#confirm-message").textContent = message;
    root.querySelector("#confirm-accept").textContent = confirmLabel;
    root.querySelector("#confirm-dialog").showModal();
    enhanceConfirmationScope(this, subject);
    return new Promise((resolve) => { this._confirmResolver = resolve; });
  }

  _resolveConfirm(value) {
    const dialog = this.shadowRoot.querySelector("#confirm-dialog");
    if (dialog?.open) dialog.close();
    const resolver = this._confirmResolver;
    this._confirmResolver = null;
    if (resolver) resolver(value);
  }

  _setSaving(button, saving, label = "Saving…") {
    if (!button) return;
    if (saving && !button.dataset.label) button.dataset.label = button.textContent;
    button.disabled = saving;
    button.textContent = saving ? label : button.dataset.label || "Save";
  }

  _setDialogError(kind, message) {
    const element = this.shadowRoot.querySelector(`#${kind}-error`);
    if (element) element.textContent = message;
  }

  _updateKnowledgeCounter() {
    const length = this.shadowRoot.querySelector("#knowledge-content")?.value.length || 0;
    this.shadowRoot.querySelector("#knowledge-counter").textContent = `${formatUsageNumber(length)} / ${formatUsageNumber(KNOWLEDGE_LIMIT)} characters`;
  }

  _toast(message, error = false) {
    const toast = this.shadowRoot.querySelector("#toast");
    if (!toast) return;
    toast.textContent = message;
    toast.className = `toast visible${error ? " toast-error" : ""}`;
    clearTimeout(this._toastTimer);
    this._toastTimer = setTimeout(() => { toast.className = "toast"; }, 5000);
  }

  _scopePicker() {
    if (this._viewKey() === "data-memory/memories" && this._memoryKind === "temporary") return getRouteFeature("data-memory/memories")?.renderTemporaryScopePicker(this);
    const memories = this._viewKey() === "data-memory/memories";
    const hasEmpty = (this._data?.scopes || []).some((scope) => (memories ? scope.memory_count : scope.conversation_count) === 0 && scope.scope_type === "user" && !scope.is_current_user);
    return `<section class="scope-bar"><label><span>${memories ? "Show memories available to" : "Show conversations belonging to"}</span><select id="scope">${this._scopeOptions(memories ? "memories" : "conversations")}</select></label>${hasEmpty ? `<label class="show-empty"><input id="show-empty-scopes" type="checkbox" ${this._showEmptyScopes ? "checked" : ""}> Show users with no ${memories ? "memories" : "conversations"}</label>` : ""}${this._data?.is_admin ? `<small>You can view data for all users because you are an administrator.</small>` : ""}</section>`;
  }

  _scopeOptions(section, includeEmpty = this._showEmptyScopes, excludeLegacy = false) {
    const key = section === "memories" ? "memory_count" : "conversation_count";
    const scopes = [...(this._data?.scopes || [])].filter((scope) =>
      (!excludeLegacy || scope.scope_type !== "anonymous_legacy")
      && (scope.scope_id === this._scopeId || scope.is_current_user || scope[key] > 0 || scope.scope_type !== "user" || includeEmpty)
    );
    scopes.sort((a, b) => {
      if (a.scope_type === "anonymous_legacy") return 1;
      if (b.scope_type === "anonymous_legacy") return -1;
      if (a.is_current_user !== b.is_current_user) return a.is_current_user ? -1 : 1;
      const populated = Number(b[key] > 0) - Number(a[key] > 0);
      return populated || a.display_name.localeCompare(b.display_name);
    });
    return scopes.map((scope) => `<option value="${this._e(scope.scope_id)}" ${scope.scope_id === this._scopeId ? "selected" : ""}>${this._e(scope.display_name)} (${formatUsageNumber(scope[key] || 0)})${scope.is_current_user ? " · You" : ""}</option>`).join("");
  }

  _filtered(items, value) {
    const query = this._query.trim().toLocaleLowerCase();
    return query ? items.filter((item) => value(item).toLocaleLowerCase().includes(query)) : items;
  }

  _retentionOptions(selected) { return [0,7,30,90,180,365].map((value) => `<option value="${value}" ${value === selected ? "selected" : ""}>${value ? `${formatUsageNumber(value)} days` : "Disabled"}</option>`).join(""); }
  _metric(title, value, detail = "") { const display = typeof value === "number" && Number.isFinite(value) ? formatUsageNumber(value) : String(value); return `<article class="metric"><span>${this._e(title)}</span><strong>${this._e(display)}</strong>${detail ? `<small>${this._e(String(detail))}</small>` : ""}</article>`; }
  _toggle(id, label, checked) { return `<label class="toggle"><span>${this._e(label)}</span><input id="${id}" type="checkbox" role="switch" ${checked ? "checked" : ""}></label>`; }
  _table(headers, rows) { return `<div class="table"><table><thead><tr>${headers.map((header) => `<th>${this._e(header)}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((value) => `<td>${this._e(typeof value === "number" && Number.isFinite(value) ? formatUsageNumber(value) : String(value))}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`; }
  _loading() { return `<div class="loading" role="status"><span class="spinner"></span>Loading…</div>`; }
  _empty(message) { return `<div class="empty">${this._e(message)}</div>`; }
  _label(value) { return value[0].toUpperCase() + value.slice(1); }
  _titleCase(value) { return String(value || "").replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
  _temporaryOwner(scopeId) {
    const known = (this._data?.scopes || []).find((scope) => scope.scope_id === scopeId);
    if (known) return known.display_name;
    if (String(scopeId || "").startsWith("device:")) return "Assist device";
    if (String(scopeId || "").startsWith("conversation:")) return "Current Assist conversation";
    return "Current user";
  }

  _formatDate(value) {
    return formatManagementTimestamp(value, this._hass?.config?.time_zone);
  }
  _e(value) { return String(value ?? "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[character]); }

}

customElements.define("extended-openai-management-panel", ExtendedOpenAIManagementPanel);
