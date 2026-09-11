import {lookupModelData} from "./model-catalog.js";
import {bindRequestRuleMatchTester, formatRequestRuleMatchResult, transformRequestRulesMatchTester} from "./request-rules-match-test-ui.js";

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

function addRequestRuleManagementClarity(panel, html) {
  const rules = panel._result?.rules || [];
  const diagnostics = panel._result?.diagnostics || {};
  const routingHelp = '<section class="notice"><strong>AI routing command behavior</strong><p><strong>Equals</strong> and <strong>ExtendedOpenAI sentence pattern</strong> routing rules are complete commands: they are acknowledged locally and apply to the rest of the current conversation; they are not sent to the AI provider. <strong>Starts with</strong>, <strong>Ends with</strong>, and <strong>Contains</strong> select a route and send the original request unchanged to the provider; matched words are not stripped.</p><p>A request-only reset bypasses a conversation override for that one provider request; it does not clear the saved conversation route. Rule order is only the final tie-breaker after match type and phrase specificity.</p></section>';
  let transformed = html.replace(
    '<section class="content-card rule-settings">',
    `${routingHelp}<section class="content-card rule-settings">`,
  );
  for (const [index, rule] of rules.entries()) {
    const id = panel._e(rule.id);
    const edit = `<button type="button" class="secondary rule-edit" data-id="${id}">Edit</button>`;
    const diagnostic = diagnostics?.[rule.id]
      ? `<p class="sensitive-warning"><strong>Rule inactive:</strong> ${panel._e(diagnostics[rule.id])} Edit and save this rule to use the current sentence-pattern syntax.</p>`
      : "";
    const controls = `${diagnostic}<button type="button" class="secondary rule-move" data-id="${id}" data-direction="up" ${index === 0 ? "disabled" : ""}>Move up</button><button type="button" class="secondary rule-move" data-id="${id}" data-direction="down" ${index === rules.length - 1 ? "disabled" : ""}>Move down</button>${edit}`;
    transformed = transformed.replace(edit, controls);
  }
  return transformed;
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
  const reasoning = root.querySelector("#rule-reasoning");
  const help = root.querySelector("#rule-routing-scope-help");
  if (!actionType || !matchType || !scope) return;

  const modelRouting = actionType.value === "model_routing";
  const consumed = modelRouting && ["equals", "sentence_pattern"].includes(matchType.value);
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
      : "Broad matches send the original request to the AI unchanged. This request only affects that provider call; Rest of this conversation also changes later requests.";
  }
}

export function renderRequestRules(panel) {
  const module = getRequestRulesModule();
  if (!module) {
    queueRender(panel);
    return panel._loading?.() || '<div class="loading">Loading Request Rules…</div>';
  }
  const html = addRequestRuleManagementClarity(
    panel,
    renderAllRulesForInPlaceSearch(panel, module),
  );
  return transformRequestRulesMatchTester(html);
}

export function bindRequestRules(panel) {
  const module = getRequestRulesModule();
  if (!module) return queueRender(panel);
  const result = module.bindRequestRules(panel);
  const root = panel.shadowRoot;
  root?.querySelectorAll(".rule-move:not([disabled])").forEach((button) => button.addEventListener("click", async () => {
    button.disabled = true;
    try {
      await panel._call("request_rules", "move", {
        rule_id: button.dataset.id,
        direction: button.dataset.direction,
        revision: panel._result?.revision,
      });
      await panel._loadSection();
    } catch (err) {
      await module.recoverRequestRuleMutation(panel, err, "Unable to move Request Rule");
    }
  }));
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
  root?.querySelectorAll(".rule-edit,#rule-add,#rule-empty-add").forEach((button) => button.addEventListener("click", refreshRouting));
  for (const selector of ["#rule-action-type", "#rule-match", "#rule-scope", "#rule-model", "#rule-reset"]) {
    const element = root?.querySelector(selector);
    if (!element) continue;
    element.addEventListener(selector === "#rule-model" ? "input" : "change", refreshRouting);
  }
  void refreshRouting();
  bindRequestRuleMatchTester(panel);
  return result;
}

export function requestRulesDialog(...args) {
  const dialog = getRequestRulesModule()?.requestRulesDialog(...args) || "";
  return dialog.replace(
    '<div id="rule-routing-config" hidden>',
    '<div id="rule-routing-config" hidden><p class="help"><strong>Equals</strong> and <strong>ExtendedOpenAI sentence pattern</strong> are complete commands: they are acknowledged locally and can only change or reset the rest of this conversation. Broader Starts/Ends/Contains matches preserve and send the entire original request to the provider.</p><p class="help" id="rule-routing-scope-help"></p>',
  );
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
