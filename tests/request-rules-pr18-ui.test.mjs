import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const frontend = (name) => new URL(
  `../custom_components/extended_openai_conversation_responses/frontend/${name}`,
  import.meta.url,
);

const stateSafety = await import(frontend("management-state-safety.js"));
const rulesUi = await import(frontend("request-rules-ui-impl.js"));
const source = await readFile(frontend("request-rules-ui-impl.js"), "utf8");

const selector = {
  id: "",
  name: "",
  type: "",
  tagName: "HA-SELECTOR",
  value: [{action: "light.turn_on", target: {entity_id: "light.kitchen"}}],
};
const selectorDialog = {
  id: "rule-dialog",
  querySelectorAll: () => [selector],
};
const selectorBaseline = [{
  id: "",
  type: "HA-SELECTOR",
  checked: undefined,
  value: [{action: "light.turn_on", target: {entity_id: "light.kitchen"}}],
}];
assert.equal(stateSafety.dialogHasUnsavedChanges(selectorDialog, selectorBaseline), false);
selector.value = [{action: "light.turn_off", target: {entity_id: "light.kitchen"}}];
assert.equal(
  stateSafety.dialogHasUnsavedChanges(selectorDialog, selectorBaseline),
  true,
  "action-only ha-selector edits must make the Request Rule dialog dirty",
);

assert.equal(rulesUi.fuzzyThresholdValue(70), 70);
assert.equal(rulesUi.fuzzyThresholdValue(91), 91);
assert.equal(rulesUi.fuzzyThresholdValue("97"), 97);
assert.equal(rulesUi.fuzzyThresholdValue(100), 100);
assert.equal(rulesUi.fuzzyThresholdValue(69), 90);
assert.match(rulesUi.requestRulesDialog(), /type="number" min="70" max="100" step="1"/);

const escapeHtml = (value) => String(value)
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;");
const panel = {
  _e: escapeHtml,
  _query: "",
  _result: {
    defaults: {word_forms: true, wording_alternatives: true, fuzzy: false, fuzzy_threshold: 91},
    wording_groups: [],
    rules: [{
      id: "rule-1",
      name: "<b>unsafe name</b>",
      enabled: true,
      phrases: ["route this"],
      match_type: "contains",
      action_type: "model_routing",
      action: {
        model: '<img src=x onerror="boom">',
        reasoning_effort: "<b>high</b>",
        scope: "request",
        reset: false,
      },
      matching_behavior: "defaults",
      matching: {word_forms: true, wording_alternatives: true, fuzzy: false, fuzzy_threshold: 91},
    }],
  },
};
const rendered = rulesUi.renderRequestRules(panel);
assert.doesNotMatch(rendered, /<b>unsafe name<\/b>/);
assert.doesNotMatch(rendered, /<img src=x/);
assert.match(rendered, /&lt;img src=x/);
assert.match(rendered, /&lt;b&gt;high&lt;\/b&gt;/);

const toasts = [];
let refreshed = 0;
const mutationPanel = {
  _sectionCache: new Map([["agent|capabilities/request-rules", {stale: true}]]),
  _sectionCacheKey: () => "agent|capabilities/request-rules",
  _toast: (message, isError) => toasts.push({message, isError}),
  _loadSection: async (silent) => { assert.equal(silent, true); refreshed += 1; },
};
await rulesUi.recoverRequestRuleMutation(
  mutationPanel,
  new Error("backend rejected update"),
  "Unable to update Request Rule",
);
assert.equal(mutationPanel._sectionCache.has("agent|capabilities/request-rules"), false);
assert.equal(refreshed, 1);
assert.deepEqual(toasts, [{message: "Unable to update Request Rule: backend rejected update", isError: true}]);

assert.match(source, /recoverRequestRuleMutation\(panel, err, "Unable to duplicate Request Rule"\)/);
assert.match(source, /recoverRequestRuleMutation\(panel, err, "Unable to delete Request Rule"\)/);
assert.match(source, /input\.checked = Boolean\(rule\.enabled\)/);
