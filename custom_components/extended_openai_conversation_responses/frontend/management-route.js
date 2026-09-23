import {ensureGuideModule} from "./guide-page.js";
import {ensureOverviewModule} from "./overview-page.js";
import {SECTION_CACHE_TTL_MS, CLEAN_CONFIG_TTL_MS} from "./management-cache.js";
const REQUEST_RULES_VIEW = "capabilities/request-rules";
const CONFIG_VIEWS = new Set([
  "capabilities/home-assistant",
  "capabilities/web-skills",
  "data-memory/conversations",
]);
// These views consume the normal full configuration snapshot. Retention uses a
// separate projection; unauthorised readers must never speculate on config.
export function needsFullConfiguration(view, isAdmin = true) {
  return isAdmin && (String(view || "").startsWith("assistant/")
    || ["capabilities/home-assistant", "capabilities/web-skills", "capabilities/functions",
      "data-memory/conversations", "data-memory/memory-settings"].includes(view));
}

export function getConfigurationEditor() { return getRouteFeature("agent-config"); }
export function getConfigurationTools() { return getRouteFeature("agent-config-tools"); }

const CONFIG_SECTION_FAMILIES = {
  general: "primary", conversation: "primary", context: "primary", model: "primary",
  local: "capabilities", capabilities: "capabilities", archive: "capabilities",
  prompt: "media", voice: "media", speech: "media",
  retention: "maintenance", backup: "maintenance",
};
export function configurationSectionFamily(view, sections = null) {
  const section = sections?.find((item) => CONFIG_SECTION_FAMILIES[item]);
  if (section) return `agent-config-sections-${CONFIG_SECTION_FAMILIES[section]}`;
  if (view === "assistant/advanced") return "agent-config-sections-capabilities";
  if (String(view || "").startsWith("assistant/")) {
    return `agent-config-sections-${["prompt-context", "voice", "speech"].includes(view.split("/")[1]) ? "media" : "primary"}`;
  }
  if (view === "usage-maintenance/retention") return "agent-config-sections-maintenance";
  return "agent-config-sections-capabilities";
}

export function routeAssetKind(view) {
  if (view === "capabilities/functions") return "agent-config-tools";
  if (view === "usage-maintenance/backup-restore") return null;
  if (String(view || "").startsWith("assistant/") || CONFIG_VIEWS.has(view)) return "agent-config";
  if (view === REQUEST_RULES_VIEW) return "request-rules";
  return null;
}

const featureModules = new Map();
const featurePromises = new Map();
// Only routes whose data loader itself lives in the lazy feature must wait for it.
// Ordinary panel-owned data requests should begin while route assets download.
const DATA_FEATURES = new Set([
  "capabilities/quiet-hours",
  "usage-maintenance/request-debug",
]);
const featureLoaders = {
  "agent-config": () => import("./agent-config-editor.js"),
  "agent-config-sections-primary": () => import("./agent-config-sections-primary.js"),
  "agent-config-sections-capabilities": () => import("./agent-config-sections-capabilities.js"),
  "agent-config-sections-media": () => import("./agent-config-sections-media.js"),
  "agent-config-sections-maintenance": () => import("./agent-config-sections-maintenance.js"),
  "agent-config-tools": () => import("./agent-config-tools.js"),
  "usage-maintenance/backup-restore": () => import("./backup-route-ui.js"),
  "status": () => import("./management-feature-status.js"),
  "capabilities": () => import("./management-capabilities-ia.js"),
  "configuration": () => import("./management-configuration-feature.js"),
  "memory-browser": () => import("./guest-mode-ui.js"),
  "data-memory/memories": () => import("./management-memory-feature.js"),
  "data-memory/knowledge": () => import("./management-knowledge-feature.js"),
  "capabilities/guest-mode": () => import("./management-guest-feature.js"),
  "capabilities/request-rules": () => import("./request-rules-ui.js"),
  "capabilities/quiet-hours": () => import("./quiet-hours-ui.js"),
  "capabilities/functions": () => import("./management-function-repair.js"),
  "data-memory/conversations": () => import("./management-history-pagination.js"),
  "usage-maintenance/usage": () => import("./usage-chart.js"),
  "usage-maintenance/retention": () => import("./retention-settings-ui.js"),
  "usage-maintenance/request-debug": () => import("./debug-management.js"),
  "usage-maintenance/diagnostics": () => import("./management-provider-credentials.js"),
  "assistant/voice": () => import("./voice-identity-core.js"),
  "data-memory/memory-settings": () => import("./memory-settings-ui.js"),
};
export function getRouteFeature(view) { return featureModules.get(view); }

function routeFeatureKeys(view) {
  const keys = [view];
  const assetKind = routeAssetKind(view);
  if (assetKind === "agent-config" || assetKind === "agent-config-tools") keys.push(assetKind);
  if (assetKind === "agent-config") keys.push(configurationSectionFamily(view));
  // Configuration guidance is additive and never blocks route readiness.
  if (["data-memory/memories", "data-memory/conversations", "capabilities/guest-mode"].includes(view)) keys.push("memory-browser");
  if (["capabilities/home-assistant", "capabilities/web-skills"].includes(view)) keys.push("capabilities");
  if (["data-memory/memories", "usage-maintenance/diagnostics"].includes(view)) keys.push("status");
  return keys;
}

function warmSupplementalRouteFeatures(panel, view, token) {
  if (routeAssetKind(view) !== "agent-config" && view !== "data-memory/memory-settings") return;
  const pending = featureAssetPromise("configuration");
  pending?.then((module) => {
    if (!isCurrentLazyLoad(panel, view, token)) return;
    // Guidance decorates already-rendered configuration controls and does not own
    // their data or markup, so it can safely arrive after first usable paint.
    module?.enhanceConfigurationGuidance?.(panel);
  }).catch(() => {});
}

export function routeFeaturesReady(view) {
  return routeFeatureKeys(view).every((key) => !featureLoaders[key] || featureModules.has(key));
}

function routeFeaturePromise(view) {
  const pending = routeFeatureKeys(view).map(featureAssetPromise).filter(Boolean);
  return pending.length ? Promise.all(pending) : null;
}

function featureAssetPromise(view) {
  if (!featureLoaders[view] || featureModules.has(view)) return null;
  if (!featurePromises.has(view)) {
    featurePromises.set(view, featureLoaders[view]().then((module) => {
      featureModules.set(view, module);
      return module;
    }).finally(() => featurePromises.delete(view)));
  }
  return featurePromises.get(view);
}

export function routeAssetPromise(view, panel) {
  const feature = routeFeaturePromise(view);
  const core = coreAssetPromise(view);
  return feature ? Promise.all([feature, core]) : core;
}

export function warmRouteAsset(view) {
  const pending = routeAssetPromise(view);
  pending?.catch?.(() => {});
  return pending;
}

const INTENT_READS = new Map([
  ["overview", ["overview", "summary"]],
  ["data-memory/knowledge", ["knowledge", "list"]],
  ["capabilities/request-rules", ["request_rules", "list"]],
  ["usage-maintenance/retention", ["configuration", "retention_get"]],
]);
const INTENT_READ_TTL_MS = 3_000;

// Only read-only, parameter-free route requests enter this registry. The
// generation and agent identity prevent a pre-mutation or other-agent result
// from being reused by a later navigation.
export function prefetchIntentRead(panel, view) {
  const operation = INTENT_READS.get(view);
  const agent = panel._selectedAgent?.();
  if (!operation || !agent || panel._viewKey?.() === view || panel._configDirty) return null;
  if (view === "usage-maintenance/retention") {
    const active = panel._configData;
    if (panel._draftAgentId === agent.subentry_id && active?.config
        && active.projection !== "retention") return null;
    const retentionKey = panel._configurationSnapshotKey?.(agent.subentry_id, "retention");
    const cachedRetention = retentionKey ? panel._cleanConfigSnapshots?.get(retentionKey) : null;
    if (cachedRetention && Date.now() - cachedRetention.loadedAt <= CLEAN_CONFIG_TTL_MS) return null;
  }
  const cacheKey = panel._sectionCacheKey?.(view);
  const loadedAt = panel._eocSectionCacheTimes?.get(cacheKey);
  if (loadedAt && Date.now() - loadedAt < SECTION_CACHE_TTL_MS
      && panel._sectionCache?.has(cacheKey)) return null;
  const key = `${agent.entry_id}|${agent.subentry_id}|${panel._cacheGeneration || 0}|${view}`;
  panel._eocPendingRouteReads ||= new Map();
  const pending = panel._eocPendingRouteReads.get(key);
  if (pending && Date.now() - pending.started < INTENT_READ_TTL_MS) return pending.promise;
  const record = {started: Date.now(), promise: null};
  record.promise = panel._call(...operation).catch((error) => {
    if (panel._eocPendingRouteReads.get(key) === record) panel._eocPendingRouteReads.delete(key);
    throw error;
  });
  // A speculative read may fail without a navigation ever consuming it.
  record.promise.catch(() => {});
  panel._eocPendingRouteReads.set(key, record);
  return record.promise;
}

export function consumeIntentRead(panel, view, section, action) {
  const operation = INTENT_READS.get(view);
  const agent = panel._selectedAgent?.();
  if (!operation || operation[0] !== section || operation[1] !== action || !agent) {
    return panel._call(section, action);
  }
  const key = `${agent.entry_id}|${agent.subentry_id}|${panel._cacheGeneration || 0}|${view}`;
  const pending = panel._eocPendingRouteReads?.get(key);
  if (!pending || Date.now() - pending.started >= INTENT_READ_TTL_MS) {
    panel._eocPendingRouteReads?.delete(key);
    return panel._call(section, action);
  }
  panel._eocPendingRouteReads.delete(key);
  return pending.promise;
}

function coreAssetPromise(view) {
  if (view === "overview") return ensureOverviewModule();
  if (view === "guide") return ensureGuideModule();
  return null;
}

export function requestRuleSearchText(rule) {
  return `${rule?.name || ""} ${(rule?.phrases || []).join(" ")} ${rule?.action_type || ""}`.toLocaleLowerCase();
}

export function matchesRequestRuleSearch(rule, query) {
  const normalized = String(query || "").trim().toLocaleLowerCase();
  return !normalized || requestRuleSearchText(rule).includes(normalized);
}

function requestRuleSearchEntries(panel, root, rules) {
  const list = root.querySelector(".rule-list");
  const revision = panel._eocRequestRuleCollectionRevision || 0;
  const cached = panel._eocRequestRuleSearchCache;
  if (
    cached?.rules === rules
    && cached.list === list
    && cached.revision === revision
  ) return cached.entries;

  const cards = new Map(
    [...(list?.querySelectorAll?.("[data-rule-key]") || [])]
      .map((card) => [String(card.dataset.ruleKey), card]),
  );
  const entries = rules.map((rule) => ({
    card: cards.get(String(rule.id)),
    searchText: requestRuleSearchText(rule),
  })).filter((entry) => entry.card);
  panel._eocRequestRuleSearchCache = {rules, list, revision, entries};
  return entries;
}

export function applyRequestRuleSearch(panel, root = panel?.shadowRoot) {
  if (!root || panel?._viewKey?.() !== REQUEST_RULES_VIEW) return 0;
  const query = String(root.querySelector("#rule-search")?.value ?? panel._query ?? "");
  const normalized = query.trim().toLocaleLowerCase();
  const rules = panel._result?.rules || [];
  const entries = requestRuleSearchEntries(panel, root, rules);
  let visible = 0;

  for (const {card, searchText} of entries) {
    const matches = !normalized || searchText.includes(normalized);
    if (card.hidden === matches) card.hidden = !matches;
    if (matches) visible += 1;
  }

  const list = root.querySelector(".rule-list");
  const empty = list?.querySelector("[data-eoc-rule-search-empty]");
  if (empty) empty.hidden = !String(query).trim() || visible > 0 || !rules.length;

  const count = root.querySelector(".search-row .count");
  if (count) {
    const total = rules.length;
    count.textContent = String(query).trim()
      ? `${visible} of ${total} rule${total === 1 ? "" : "s"}`
      : `${total} rule${total === 1 ? "" : "s"}`;
  }
  return visible;
}

export function bindRequestRuleSearch(panel) {
  const root = panel.shadowRoot;
  if (!root || root.__eocInPlaceRuleSearchBound) return;
  root.__eocInPlaceRuleSearchBound = true;
  root.addEventListener("input", (event) => {
    const input = event.target;
    if (input?.id !== "rule-search") return;
    event.stopImmediatePropagation();
    panel._query = input.value;
    applyRequestRuleSearch(panel, root);
  }, true);
}

function isCurrentLazyLoad(panel, view, assetToken) {
  return panel._viewKey() === view && panel._eocViewAssetToken === assetToken;
}

export function loadSectionAlongsideAsset(
  panel,
  silent,
  loadSectionData,
  view,
  assetPromise,
  assetToken,
) {
  let sectionPromise;
  try {
    sectionPromise = Promise.resolve(
      loadSectionData.call(panel, silent),
    );
  } catch (err) {
    sectionPromise = Promise.reject(err);
  }

  return Promise.allSettled([assetPromise, sectionPromise]).then(([assetResult, sectionResult]) => {
    if (!isCurrentLazyLoad(panel, view, assetToken)) return undefined;
    const failure = assetResult.status === "rejected"
      ? assetResult.reason
      : sectionResult.status === "rejected"
        ? sectionResult.reason
        : null;
    if (!failure) {
      // A route-owned feature can become available after the data renderer ran.
      // If the settled data already rendered with its feature loaded, another
      // full render would only rebuild the same route markup.
      if (panel._eocRenderedRoute !== `${panel._agentId}|${view}`
          || !panel._eocRenderedFeatureReady || panel._busy
          || panel._eocDeferredEditorRender) panel._render();
      return sectionResult.value;
    }
    panel._busy = false;
    panel._error = `Unable to load this frontend section: ${failure?.message || String(failure)}`;
    panel._render();
    return undefined;
  });
}

async function loadRouteData(panel, silent, view, token) {
  const feature = getRouteFeature(view);
  if (view === "capabilities/quiet-hours") return feature.loadQuietHours(panel, silent);
  if (view === "usage-maintenance/request-debug") return feature.loadRequestDebug(panel, silent);
  if (view === "data-memory/conversations") {
    panel._eocHistoryMode = "list";
    panel._eocHistoryQuery = "";
  }
  return panel._loadSectionData(silent);
}

// One native route entry point owns lazy assets and stale completion handling.
export function loadRoute(panel, silent = false) {
  const view = panel._viewKey();
  if (view !== "overview") panel._eocOverviewBroadcastPromise = null;
  const token = (panel._eocViewAssetToken || 0) + 1;
  panel._eocViewAssetToken = token;
  const feature = routeFeaturePromise(view);
  const asset = coreAssetPromise(view);
  warmSupplementalRouteFeatures(panel, view, token);
  if (!feature && !asset) return loadRouteData(panel, silent, view, token);
  let loadData = () => loadRouteData(panel, silent, view, token);
  if (feature && DATA_FEATURES.has(view)) {
    // Invalidate prior data work immediately, before awaiting route code.
    ++panel._loadToken;
    if (!silent) { panel._busy = true; panel._render(); }
    loadData = async function() {
      await feature;
      if (!isCurrentLazyLoad(panel, view, token)) return;
      return loadRouteData(panel, silent, view, token);
    };
  }
  return loadSectionAlongsideAsset(panel, silent, loadData, view, Promise.all([feature, asset]), token);
}

const WS_TYPE = "extended_openai_conversation_responses/management";
export const AGENT_KEY = "extended-openai-agent";
export const ENTRY_KEY = "extended-openai-agent-entry";

export function applyOverviewResult(panel, result) {
  const agent = panel._selectedAgent?.();
  if (!agent || !result) return false;
  if (result.agent) Object.assign(agent, result.agent);
  const {agent: _agent, ...overview} = result;
  panel._contentData = null;
  panel._result = overview;
  if (panel._sectionCacheKey && panel._sectionCache) {
    const key = panel._sectionCacheKey("overview");
    if (key) {
      panel._sectionCache.set(key, overview);
      panel._eocSectionCacheTimes?.set(key, Date.now());
    }
  }
  panel._error = null;
  panel._busy = false;
  panel._render();
  return true;
}

export function startStoredOverviewPrefetch(
  panel,
  preferredSubentryId,
  overviewAsset = ensureOverviewModule(),
) {
  if (panel._viewKey?.() !== "overview") return null;
  const subentryId = preferredSubentryId || globalThis.localStorage?.getItem?.(AGENT_KEY);
  const entryId = globalThis.localStorage?.getItem?.(ENTRY_KEY);
  if (!subentryId || !entryId) return null;
  panel._markColdLifecycle?.("overview-summary-start");
  const overviewSummary = panel._hass.callWS({
    type: WS_TYPE,
    section: "overview",
    action: "summary",
    entry_id: entryId,
    subentry_id: subentryId,
  }).then(
    (result) => {
      panel._markColdLifecycle?.("overview-summary-complete", {status: "fulfilled"});
      return result;
    },
    (err) => {
      panel._markColdLifecycle?.("overview-summary-complete", {status: "rejected"});
      throw err;
    },
  );
  return {
    entryId,
    subentryId,
    overviewAsset,
    overviewSummary,
    promise: Promise.allSettled([
      overviewAsset,
      overviewSummary,
    ]),
  };
}

export function startStoredConfigurationPrefetch(panel, preferredSubentryId) {
  const view = panel._viewKey?.();
  const action = view === "usage-maintenance/retention"
    ? "retention_get"
    : needsFullConfiguration(view) ? "get" : null;
  if (!action) return null;
  const subentryId = preferredSubentryId || globalThis.localStorage?.getItem?.(AGENT_KEY);
  const entryId = globalThis.localStorage?.getItem?.(ENTRY_KEY);
  if (!subentryId || !entryId) return null;
  const request = panel._hass.callWS({
    type: WS_TYPE,
    section: "configuration",
    action,
    entry_id: entryId,
    subentry_id: subentryId,
  });
  return {
    view,
    entryId,
    subentryId,
    promise: request.then(
      (value) => ({status: "fulfilled", value}),
      (reason) => ({status: "rejected", reason}),
    ),
  };
}

function applyPrefetchedConfiguration(panel, prefetch, configData) {
  panel._configData = configData;
  panel._rememberCleanConfiguration?.(configData, prefetch.subentryId);
  panel._draft = JSON.parse(JSON.stringify(configData.config));
  panel._draftTitle = configData.title;
  panel._draftAgentId = prefetch.subentryId;
  panel._setConfigDirty?.(false);
}

export async function loadAgentsWithOverviewPrefetch(panel, selectedId = null) {
  const initialToken = panel._loadToken;
  const previousAgentId = panel._agentId;
  const saved = globalThis.localStorage?.getItem?.(AGENT_KEY);
  const preferred = selectedId || saved;
  if (panel._viewKey?.() === "overview") panel._markColdLifecycle?.("overview-asset-start");
  const routeAsset = warmRouteAsset(panel._viewKey?.());
  if (panel._viewKey?.() === "overview" && routeAsset?.then) {
    routeAsset.then(
      () => panel._markColdLifecycle?.("overview-asset-complete", {status: "fulfilled"}),
      () => panel._markColdLifecycle?.("overview-asset-complete", {status: "rejected"}),
    );
  }
  const prefetch = startStoredOverviewPrefetch(panel, preferred, routeAsset);
  const configurationPrefetch = startStoredConfigurationPrefetch(panel, preferred);

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

  const overviewSelected = panel._viewKey?.() === "overview" && Boolean(selected);
  if (overviewSelected) {
    // The agent catalogue is enough for a useful first Overview. Do not hide it
    // behind the full Overview module or storage-backed summary.
    panel._contentData = null;
    panel._result = null;
    panel._error = null;
    panel._busy = false;
    panel._render();
  }

  if (
    configurationPrefetch
    && panel._data?.is_admin !== false
    && selected?.subentry_id === configurationPrefetch.subentryId
    && selected?.entry_id === configurationPrefetch.entryId
    && panel._viewKey?.() === configurationPrefetch.view
  ) {
    const settled = await configurationPrefetch.promise;
    if (
      panel._viewKey?.() === configurationPrefetch.view
      && panel._loadToken === initialToken
      && panel._agentId === configurationPrefetch.subentryId
      && settled.status === "fulfilled"
      && settled.value?.config && typeof settled.value.config === "object"
    ) {
      applyPrefetchedConfiguration(panel, configurationPrefetch, settled.value);
    }
  }

  if (
    prefetch
    && selected?.subentry_id === prefetch.subentryId
    && selected?.entry_id === prefetch.entryId
    && overviewSelected
  ) {
    const overviewResult = await prefetch.overviewSummary.then(
      (value) => ({status: "fulfilled", value}),
      (reason) => ({status: "rejected", reason}),
    );
    if (panel._viewKey?.() !== "overview" || panel._loadToken !== initialToken
        || panel._agentId !== prefetch.subentryId) return;
    if (overviewResult.status === "fulfilled") {
      applyOverviewResult(panel, overviewResult.value);
      return;
    }
    // A speculative stored-ID request can fail after an agent was recreated.
    // Keep the useful snapshot visible while the authoritative route load retries.
    await panel._loadSection(true);
    return;
  }

  await panel._loadSection(overviewSelected);
}


export function isRestrictedManagementView(page, subsection = null) {
  if (page === "data-memory" && subsection === "knowledge") return true;
  if (page === "usage-maintenance" && subsection === null) return true;
  return page === "usage-maintenance" && ["usage", "diagnostics"].includes(subsection);
}

export function nonAdminOverviewKnowledgeSnapshot(panel) {
  const agent = panel?._selectedAgent?.();
  return {
    sources: [],
    stats: {source_count: Number(agent?.knowledge_source_count || 0)},
  };
}
