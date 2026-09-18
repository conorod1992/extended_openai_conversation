import {ensureAgentConfigModule, getAgentConfigModule} from "./agent-config-loader.js";
import {ensureRequestRulesModule, getRequestRulesModule} from "./request-rules-loader.js";

import {ensureGuideModule} from "./guide-page.js";
import {ensureOverviewModule} from "./overview-page.js";
const REQUEST_RULES_VIEW = "capabilities/request-rules";
const CONFIG_VIEWS = new Set([
  "capabilities/home-assistant",
  "capabilities/web-skills",
  "capabilities/functions",
  "data-memory/conversations",
  "usage-maintenance/backup-restore",
  "usage-maintenance/retention",
]);

export function routeAssetKind(view) {
  if (String(view || "").startsWith("assistant/") || CONFIG_VIEWS.has(view)) return "agent-config";
  if (view === REQUEST_RULES_VIEW) return "request-rules";
  return null;
}

const featureModules = new Map();
const featurePromises = new Map();
const featureLoaders = {
  "assistant/voice": () => import("./voice-identity-ui.js"),
  "data-memory/memory-settings": () => import("./memory-settings-ui.js"),
};
export function getRouteFeature(view) { return featureModules.get(view); }

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

export function routeAssetPromise(view) {
  const feature = featureAssetPromise(view);
  const core = coreAssetPromise(view);
  return feature ? Promise.all([feature, core]) : core;
}

function coreAssetPromise(view) {
  if (view === "overview") return ensureOverviewModule();
  if (view === "guide") return ensureGuideModule();
  if (view === "usage-maintenance/request-debug") return import("./debug-panel.js");
  const kind = routeAssetKind(view);
  if (kind === "agent-config" && !getAgentConfigModule()) return ensureAgentConfigModule();
  if (kind === "request-rules" && !getRequestRulesModule()) return ensureRequestRulesModule();
  return null;
}

export function matchesRequestRuleSearch(rule, query) {
  const normalized = String(query || "").trim().toLocaleLowerCase();
  if (!normalized) return true;
  const haystack = `${rule?.name || ""} ${(rule?.phrases || []).join(" ")} ${rule?.action_type || ""}`.toLocaleLowerCase();
  return haystack.includes(normalized);
}

export function applyRequestRuleSearch(panel, root = panel?.shadowRoot) {
  if (!root || panel?._viewKey?.() !== REQUEST_RULES_VIEW) return 0;
  const query = String(root.querySelector("#rule-search")?.value ?? panel._query ?? "");
  const rules = panel._result?.rules || [];
  const rulesById = new Map(rules.map((rule) => [String(rule.id), rule]));
  const cards = [...root.querySelectorAll(".request-rule-card")];
  let visible = 0;

  for (const card of cards) {
    const ruleId = card.querySelector(".rule-enabled")?.dataset?.id;
    const rule = rulesById.get(String(ruleId));
    const matches = rule ? matchesRequestRuleSearch(rule, query) : true;
    card.hidden = !matches;
    if (matches) visible += 1;
  }

  const list = root.querySelector(".rule-list");
  let empty = list?.querySelector("[data-eoc-rule-search-empty]");
  if (!empty && list && rules.length) {
    const documentRef = root.ownerDocument || globalThis.document;
    if (documentRef?.createElement) {
      empty = documentRef.createElement("section");
      empty.className = "content-card empty-state";
      empty.dataset.eocRuleSearchEmpty = "";
      empty.innerHTML = "<h2>No rules match your search</h2><p>Try a different phrase or rule name.</p>";
      list.append(empty);
    }
  }
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

// One native route entry point owns lazy assets and stale completion handling.
export function loadRoute(panel, silent = false) {
  const view = panel._viewKey();
  const token = (panel._eocViewAssetToken || 0) + 1;
  panel._eocViewAssetToken = token;
  const asset = routeAssetPromise(view);
  if (!asset) return panel._loadSectionData(silent);
  return loadSectionAlongsideAsset(panel, silent, panel._loadSectionData, view, asset, token);
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
  const initialToken = panel._loadToken;
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
    if (panel._viewKey?.() !== "overview" || panel._loadToken !== initialToken
        || panel._agentId !== prefetch.subentryId) return;
    if (assetResult.status === "fulfilled" && overviewResult.status === "fulfilled") {
      applyOverviewResult(panel, overviewResult.value);
      return;
    }
  }

  await panel._loadSection();
}

