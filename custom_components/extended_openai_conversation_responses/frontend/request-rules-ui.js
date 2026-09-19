import {bindDecisionRequestRules} from "./management-decision-guidance.js";
import {lookupModelData} from "./model-catalog.js";
import {bindRequestRuleMatchTester, formatRequestRuleMatchResult} from "./request-rules-match-test-ui.js";

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

function setReasoningOptions(root, efforts, selected = "") {
  const select = root?.querySelector("#rule-reasoning");
  if (!select) return;
  const values = Array.isArray(efforts) ? efforts : [];
  const keepCurrent = select.ownerDocument.createElement("option");
  keepCurrent.value = "";
  keepCurrent.textContent = "Keep current";
  const options = [keepCurrent, ...values.map((value) => {
    const option = select.ownerDocument.createElement("option");
    option.value = value;
    option.textContent = value.charAt(0).toUpperCase() + value.slice(1);
    return option;
  })];
  select.replaceChildren(...options);
  select.value = selected && values.includes(selected) ? selected : "";
}

export function syncRequestRuleRoutingControls(root, efforts = null, selectedEffort = null) {
  if (!root) return;
  const actionType = root.querySelector("#rule-action-type");
  const matchType = root.querySelector("#rule-match");
  const scope = root.querySelector("#rule-scope");
  const continueToAi = root.querySelector("#rule-continue-to-ai");
  const reasoning = root.querySelector("#rule-reasoning");
  const help = root.querySelector("#rule-routing-scope-help");
  if (!actionType || !matchType || !scope) return;

  const modelRouting = actionType.value === "model_routing";
  const consumed = modelRouting
    && ["equals", "sentence_pattern"].includes(matchType.value)
    && !(continueToAi?.checked ?? false);
  const requestOption = scope.querySelector('option[value="request"]');
  if (requestOption) requestOption.disabled = consumed;
  if (consumed) scope.value = "conversation";
  scope.disabled = consumed;

  if (reasoning && efforts) {
    const desired = selectedEffort ?? reasoning.value;
    setReasoningOptions(root, efforts, desired);
  }

  if (help) {
    help.textContent = consumed
      ? "This is a complete routing command. It is acknowledged locally and is not sent to the AI provider, so it must change or reset the rest of this conversation."
      : "This sends the original request to the AI unchanged after applying the route. This request only affects that provider call; Rest of this conversation also changes later requests.";
  }
}

export function renderRequestRules(panel) {
  const module = getRequestRulesModule();
  if (!module) {
    queueRender(panel);
    return panel._loading?.() || '<div class="loading">Loading Request Rules…</div>';
  }
  return module.renderRequestRules(panel, {query: panel._query || "", inPlaceSearch: Boolean(panel._eocInPlaceRequestRuleSearch)});
}

export function reconcileRequestRules(panel) {
  return getRequestRulesModule()?.reconcileRequestRules(panel) || false;
}

export function bindRequestRules(panel) {
  const module = getRequestRulesModule();
  if (!module) return queueRender(panel);
  const result = module.bindRequestRules(panel);
  bindDecisionRequestRules(panel);
  const root = panel.shadowRoot;
  let revision = 0;
  const refreshRouting = async () => {
    syncRequestRuleRoutingControls(root);
    const modelInput = root?.querySelector("#rule-model");
    if (!modelInput) return;
    const current = ++revision;
    const editingRule = (panel._result?.rules || []).find((item) => item.id === panel._editingRuleId);
    const selectedEffort = editingRule?.action?.reasoning_effort || root.querySelector("#rule-reasoning")?.value || "";
    const model = modelInput.value.trim();
    try {
      const data = await lookupModelData(panel, model);
      if (current === revision) syncRequestRuleRoutingControls(root, data.reasoning_effort_options, selectedEffort);
    } catch (err) { panel._toast(`Unable to load model choices: ${err.message || String(err)}`, true); }
  };
  root?.querySelector("#rule-add")?.addEventListener("click", refreshRouting);
  root?.querySelector(".rule-list")?.addEventListener("click", event => {
    if (event.target.closest?.(".rule-edit,#rule-empty-add")) void refreshRouting();
  });
  for (const selector of ["#rule-action-type", "#rule-match", "#rule-scope", "#rule-model", "#rule-reset", "#rule-continue-to-ai"]) {
    const element = root?.querySelector(selector);
    if (!element) continue;
    element.addEventListener(selector === "#rule-model" ? "input" : "change", refreshRouting);
  }
  void refreshRouting();
  bindRequestRuleMatchTester(panel);
  return result;
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

export {formatRequestRuleMatchResult};
