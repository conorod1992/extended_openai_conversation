import {ensureAgentConfigModule, getAgentConfigModule} from "./agent-config-loader.js";
import {ensureRequestRulesModule, getRequestRulesModule} from "./request-rules-loader.js";

const PATCHED = Symbol.for("extended-openai.management-route-performance");
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

function routeAssetPromise(view) {
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

function bindRequestRuleSearch(panel) {
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

function install(Panel) {
  const prototype = Panel?.prototype;
  if (!prototype || prototype[PATCHED]) return false;
  prototype[PATCHED] = true;
  prototype._eocInPlaceRequestRuleSearch = true;

  const originalLoadSection = prototype._loadSection;
  prototype._loadSection = function(silent = false) {
    const view = this._viewKey();
    const token = (this._eocRouteAssetToken || 0) + 1;
    this._eocRouteAssetToken = token;
    const assetPromise = routeAssetPromise(view);
    if (!assetPromise) return originalLoadSection.call(this, silent);

    if (!silent) {
      this._busy = true;
      this._render();
    }
    return assetPromise
      .then(() => {
        if (this._eocRouteAssetToken !== token || this._viewKey() !== view) return undefined;
        return originalLoadSection.call(this, silent);
      })
      .catch((err) => {
        if (this._eocRouteAssetToken !== token || this._viewKey() !== view) return undefined;
        this._busy = false;
        this._error = `Unable to load this frontend section: ${err.message || String(err)}`;
        this._render();
        return undefined;
      });
  };

  const originalRender = prototype._render;
  prototype._render = function(...args) {
    const result = originalRender.apply(this, args);
    bindRequestRuleSearch(this);
    if (this._viewKey() === REQUEST_RULES_VIEW) applyRequestRuleSearch(this);
    return result;
  };
  return true;
}

export function installManagementRoutePerformance(registry = globalThis.customElements) {
  if (typeof document === "undefined" || !registry?.whenDefined) return Promise.resolve(false);
  return registry.whenDefined("extended-openai-management-panel").then(() => install(registry.get("extended-openai-management-panel")));
}

if (typeof document !== "undefined" && typeof customElements !== "undefined") {
  installManagementRoutePerformance();
}
