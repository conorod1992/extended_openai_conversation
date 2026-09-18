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

export function routeAssetPromise(view) {
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
  originalLoadSection,
  view,
  assetPromise,
  assetToken,
) {
  let sectionPromise;
  try {
    sectionPromise = Promise.resolve(
      originalLoadSection.call(panel, silent),
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
    if (!failure) return sectionResult.value;
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
