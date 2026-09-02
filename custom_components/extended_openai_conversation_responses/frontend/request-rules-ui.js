const {ensureRequestRulesModule, getRequestRulesModule} = await import("./request-rules-loader.js");

if (typeof document === "undefined") await ensureRequestRulesModule();

export const FRIENDLY_TARGET_KEYS = ["entity_id", "device_id", "area_id", "floor_id", "label_id"];

function requiredImplementation(name) {
  const module = getRequestRulesModule();
  if (!module) throw new Error(`Request Rules module is not loaded before ${name}`);
  return module;
}

function queueRender(panel) {
  void ensureRequestRulesModule()
    .then(() => panel?._render?.())
    .catch((err) => {
      if (!panel) return;
      panel._error = `Unable to load Request Rules: ${err.message || String(err)}`;
      panel._render?.();
    });
}

function renderAllRulesForInPlaceSearch(panel, module) {
  const query = String(panel._query || "");
  if (!panel._eocInPlaceRequestRuleSearch || !query) return module.renderRequestRules(panel);

  panel._query = "";
  let html;
  try {
    html = module.renderRequestRules(panel);
  } finally {
    panel._query = query;
  }
  return html.replace(
    /(<input id="rule-search" type="search" value=")[^"]*(")/,
    (_match, prefix, suffix) => `${prefix}${panel._e(query)}${suffix}`,
  );
}

export function renderRequestRules(panel) {
  const module = getRequestRulesModule();
  if (!module) {
    queueRender(panel);
    return panel._loading?.() || '<div class="loading">Loading Request Rules…</div>';
  }
  return renderAllRulesForInPlaceSearch(panel, module);
}

export function bindRequestRules(panel) {
  const module = getRequestRulesModule();
  if (!module) return queueRender(panel);
  return module.bindRequestRules(panel);
}

export function requestRulesDialog(...args) {
  return getRequestRulesModule()?.requestRulesDialog(...args) || "";
}

export function friendlyFieldChange(...args) {
  return requiredImplementation("friendlyFieldChange").friendlyFieldChange(...args);
}

export function friendlyFieldChangesForService(...args) {
  return requiredImplementation("friendlyFieldChangesForService").friendlyFieldChangesForService(...args);
}

export function parseAdvancedActionConfig(...args) {
  return requiredImplementation("parseAdvancedActionConfig").parseAdvancedActionConfig(...args);
}

export function mergeFriendlyActionValue(...args) {
  return requiredImplementation("mergeFriendlyActionValue").mergeFriendlyActionValue(...args);
}

export function mergeActionEditorValue(...args) {
  return requiredImplementation("mergeActionEditorValue").mergeActionEditorValue(...args);
}

export function refreshRequestRuleSlotSelectors(...args) {
  return requiredImplementation("refreshRequestRuleSlotSelectors").refreshRequestRuleSlotSelectors(...args);
}

export function createRequestRuleActionSelector(...args) {
  return requiredImplementation("createRequestRuleActionSelector").createRequestRuleActionSelector(...args);
}

export function loadRequestRuleActions(...args) {
  return requiredImplementation("loadRequestRuleActions").loadRequestRuleActions(...args);
}

export function readRequestRuleActions(...args) {
  return requiredImplementation("readRequestRuleActions").readRequestRuleActions(...args);
}
