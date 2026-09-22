import {loadInputFootprintData} from "./usage-data.js";
import {ensureGuideModule} from "./guide-page.js";
import {ensureOverviewModule, startOverviewBroadcastSnapshot} from "./overview-page.js";
const REQUEST_RULES_VIEW = "capabilities/request-rules";
const CONFIG_VIEWS = new Set([
  "capabilities/home-assistant",
  "capabilities/web-skills",
  "capabilities/functions",
  "data-memory/conversations",
  "usage-maintenance/backup-restore",
  "usage-maintenance/retention",
]);

export function getConfigurationEditor() { return getRouteFeature("agent-config"); }

export function routeAssetKind(view) {
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
  "assistant/prompt-context": () => import("./exposed-attributes-ui.js"),
  "usage-maintenance/backup-restore": () => import("./backup-transfer-ui.js"),
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
  "usage-maintenance/usage": async () => {
    const [usage, footprint] = await Promise.all([
      import("./usage-chart.js"), import("./usage-input-footprint.js"),
    ]);
    return {...usage, ...footprint};
  },
  "usage-maintenance/request-debug": () => import("./debug-management.js"),
  "usage-maintenance/diagnostics": () => import("./management-provider-credentials.js"),
  "assistant/voice": () => import("./voice-identity-ui.js"),
  "data-memory/memory-settings": () => import("./memory-settings-ui.js"),
};
export function getRouteFeature(view) { return featureModules.get(view); }

function routeFeatureKeys(view) {
  const keys = [view];
  if (routeAssetKind(view) === "agent-config") keys.push("agent-config");
  // Configuration guidance is additive for ordinary config routes; only Memory
  // Settings owns required UI in this feature and must block route readiness.
  if (view === "data-memory/memory-settings") keys.push("configuration");
  if (["data-memory/memories", "data-memory/conversations", "capabilities/guest-mode"].includes(view)) keys.push("memory-browser");
  if (["capabilities/home-assistant", "capabilities/web-skills", "data-memory/knowledge"].includes(view)) keys.push("capabilities");
  if (["data-memory/memories", "data-memory/knowledge", "usage-maintenance/diagnostics"].includes(view)) keys.push("status");
  return keys;
}

function warmSupplementalRouteFeatures(panel, view, token) {
  if (routeAssetKind(view) !== "agent-config") return;
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
      // The renderer skips unchanged markup, retaining controls and listeners.
      panel._render();
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
  if (view === "usage-maintenance/usage" && isCurrentLazyLoad(panel, view, token)) {
    if (panel._inputFootprintAgentId !== panel._agentId) {
      panel._inputFootprint = null;
      panel._inputFootprintError = null;
    }
    // Data acquisition is owned below the lazy Usage UI boundary so this starts
    // immediately while chart/footprint modules are still downloading.
    void loadInputFootprintData(panel);
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
  if (view === "overview") startOverviewBroadcastSnapshot(panel);
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
    promise: Promise.allSettled([
      overviewAsset,
      overviewSummary,
      startOverviewBroadcastSnapshot(panel),
    ]),
  };
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
    if (panel._viewKey?.() !== "overview" || panel._loadToken !== initialToken
        || panel._agentId !== prefetch.subentryId) return;
    if (assetResult.status === "fulfilled" && overviewResult.status === "fulfilled") {
      applyOverviewResult(panel, overviewResult.value);
      return;
    }
  }

  await panel._loadSection();
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
