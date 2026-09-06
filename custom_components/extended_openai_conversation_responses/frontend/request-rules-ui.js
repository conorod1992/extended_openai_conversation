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

function applySentencePatternCopy(html) {
  return html
    .replaceAll("Home Assistant sentence pattern", "ExtendedOpenAI sentence pattern")
    .replaceAll("Hassil grammar; fuzzy and normalization settings do not apply", "ExtendedOpenAI syntax; fuzzy and normalization settings do not apply")
    .replace(
      "Use <code>[optional words]</code>, <code>(one|two)</code>, and variable values such as <code>{room}</code>. Named expansions such as <code>&lt;name&gt;</code> are not supported.",
      "Use <code>[optional words]</code>, <code>(one|two)</code>, free-text values such as <code>{room}</code>, constrained values such as <code>{room=kitchen|bedroom}</code>, and integer ranges such as <code>{level=0..100}</code>. Escape syntax characters with <code>\\</code>. This is ExtendedOpenAI syntax; named expansions and permutations are not supported.",
    )
    .replace(
      "Sentence patterns always use Hassil grammar matching, so these controls do not apply.",
      "Sentence patterns use ExtendedOpenAI's bounded matcher, so fuzzy matching, wording alternatives, and word-form normalization do not apply.",
    );
}

function addRequestRuleManagementClarity(panel, html) {
  const rules = panel._result?.rules || [];
  const diagnostics = panel._result?.diagnostics || {};
  const routingHelp = '<section class="notice"><strong>AI routing command behavior</strong><p><strong>Equals</strong> and <strong>ExtendedOpenAI sentence pattern</strong> routing rules are complete commands: they are acknowledged locally and apply to the rest of the current conversation. <strong>Starts with</strong>, <strong>Ends with</strong>, and <strong>Contains</strong> only select the route; the AI still receives the original request unchanged, including the words that matched the rule.</p><p>Rule order is only the final tie-breaker after match type and phrase specificity.</p></section>';
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

export function renderRequestRules(panel) {
  const module = getRequestRulesModule();
  if (!module) {
    queueRender(panel);
    return panel._loading?.() || '<div class="loading">Loading Request Rules…</div>';
  }
  const html = applySentencePatternCopy(addRequestRuleManagementClarity(
    panel,
    renderAllRulesForInPlaceSearch(panel, module),
  ));
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
  bindRequestRuleMatchTester(panel);
  return result;
}

export function requestRulesDialog(...args) {
  const dialog = getRequestRulesModule()?.requestRulesDialog(...args) || "";
  return applySentencePatternCopy(dialog.replace(
    '<div id="rule-routing-config" hidden>',
    '<div id="rule-routing-config" hidden><p class="help"><strong>Equals</strong> and <strong>ExtendedOpenAI sentence pattern</strong> are complete routing commands and therefore apply to the rest of this conversation. Broader Starts/Ends/Contains matches preserve and send the entire original request; matched words are not stripped.</p>',
  ));
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