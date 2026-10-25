import assert from "node:assert/strict";
import test from "node:test";
import {planConfigurationRecovery, combineRecoveredConfiguration, recoveredConfigurationChanges} from "../custom_components/extended_openai_conversation_responses/frontend/configuration-draft-recovery.js";

const state = (draft, title = "Saved") => ({draft, title});
const saved = (config, title = "Saved") => ({config, title});

test("disjoint edits preserve both owners and only overlay the retained authored delta", () => {
  const source = state({prompt:"Retained", optional:null}), sourceBaseline = saved({prompt:"Old", optional:null});
  const target = state({prompt:"Old", optional:null, usage_request_retention_days:0}), targetBaseline = saved({prompt:"Old", optional:null, usage_request_retention_days:30});
  assert.deepEqual(planConfigurationRecovery(source, sourceBaseline, target, targetBaseline), {conflicts:[], preserveTargetModel:false});
  assert.deepEqual(combineRecoveredConfiguration(target, targetBaseline, state({...targetBaseline.config, prompt:"Retained"})), state({prompt:"Retained", optional:null, usage_request_retention_days:0}));
  assert.deepEqual(source, state({prompt:"Retained", optional:null}));
});

test("equal overlapping edits may merge while unequal ones require review", () => {
  const baseline = saved({retention:30});
  assert.deepEqual(planConfigurationRecovery(state({retention:7}), baseline, state({retention:7}), baseline).conflicts, []);
  assert.deepEqual(planConfigurationRecovery(state({retention:7}), baseline, state({retention:90}), baseline).conflicts, ["retention"]);
});

test("field presence, null, false and zero remain distinct authored values", () => {
  const baseline = saved({optional:"old", enabled:true, count:1});
  const source = state({enabled:false, count:0}), target = state({optional:null, enabled:true, count:1});
  assert.deepEqual(planConfigurationRecovery(source, baseline, target, baseline).conflicts, ["optional"]);
  assert.deepEqual(recoveredConfigurationChanges(source, baseline), {config:{enabled:false, count:0}, deleted_fields:["optional"]});
  assert.deepEqual(combineRecoveredConfiguration(state(baseline.config), baseline, source), source);
});

test("title changes combine only when they agree or have one author", () => {
  const baseline = saved({prompt:"Old"});
  assert.deepEqual(planConfigurationRecovery(state({prompt:"Old"}, "A"), baseline, state({prompt:"New"}), baseline).conflicts, []);
  assert.deepEqual(combineRecoveredConfiguration(state({prompt:"New"}), baseline, state({prompt:"Old"}, "A")), state({prompt:"New"}, "A"));
  assert.deepEqual(planConfigurationRecovery(state({}, "A"), baseline, state({}, "B"), baseline).conflicts, ["__title"]);
});

test("coupled model edits require review even when their individual fields differ", () => {
  const baseline = saved({chat_model:"gpt-5-mini", reasoning_effort:"medium"});
  const source = state({chat_model:"gpt-4o", reasoning_effort:"medium"});
  const target = state({chat_model:"gpt-5-mini", reasoning_effort:"high"});
  assert.deepEqual(planConfigurationRecovery(source, baseline, target, baseline).conflicts, ["model_settings"]);
});

test("equal model settings preserve a newer target intent or explicit choice", () => {
  const baseline = saved({chat_model:"gpt-5-mini", reasoning_effort:"medium"});
  const model = {chat_model:"gpt-5", reasoning_effort:"minimal"};
  assert.deepEqual(planConfigurationRecovery(state({...model, prompt:"A"}), baseline, state(model), baseline, {targetOwnsModel:true}), {conflicts:[], preserveTargetModel:true});
});

test("an unchanged source model cannot overwrite a newer target's prepared defaults", () => {
  const baseline = saved({chat_model:"gpt-5-mini", reasoning_effort:"medium", prompt:"Old"});
  const target = state({chat_model:"gpt-5", reasoning_effort:"minimal", prompt:"Old"});
  const recovered = state({...baseline.config, prompt:"Retained"});
  assert.equal(planConfigurationRecovery(state({...baseline.config, prompt:"Retained"}), baseline, target, baseline, {targetOwnsModel:true}).preserveTargetModel, true);
  assert.deepEqual(combineRecoveredConfiguration(target, baseline, recovered), state({chat_model:"gpt-5", reasoning_effort:"minimal", prompt:"Retained"}));
});

test("copy-only recovery contains authored changes without unrelated saved values", () => {
  const baseline = saved({prompt:"Old", secret_saved_value:"private", remove:"old"});
  assert.deepEqual(recoveredConfigurationChanges(state({prompt:"Retained", secret_saved_value:"private"}, "A"), baseline), {config:{prompt:"Retained"}, deleted_fields:["remove"], title:"A"});
});

function recoveryPanels(id, {sourceConfig = {}, targetConfig = {}, targetIntent = null, reasoningRevision = 0} = {}) {
  const baseline = {chat_model:"gpt-5-mini", reasoning_effort:"medium", prompt:"Old", usage_request_retention_days:30};
  const agent = {entry_id:`entry-${id}`, subentry_id:`agent-${id}`};
  const create = (changes, connected) => ({
    hass:{user:{id:"admin"}}, _agentId:agent.subentry_id, _draftAgentId:agent.subentry_id,
    _selectedAgent:() => agent, _data:{is_admin:true}, isConnected:connected,
    _configData:{config:{...baseline}, title:"Saved", revision:"r1"}, _configDataStale:false,
    _draft:{...baseline, ...changes}, _draftTitle:"Saved", _configDirty:true,
    _viewKey:() => connected ? "assistant/basics" : "usage-maintenance/retention",
    _loadToken:1, _cacheGeneration:0, shadowRoot:{querySelector:() => null, append() {}},
    _syncConfigDirty() {this._configDirty = JSON.stringify(this._draft) !== JSON.stringify(this._configData.config);},
    _setConfigDirty(value) {this._configDirty = value;}, _configurationDirtyDestinations:() => ["assistant/prompt-context"],
    _render() {}, _toast() {},
  });
  const source = create({prompt:"Retained", ...sourceConfig}, false), target = create(targetConfig, true);
  target._eocModelIntent = targetIntent;
  target._eocReasoningChoiceRevision = reasoningRevision;
  return {source, target};
}

globalThis.window = {addEventListener() {}, removeEventListener() {}};
const {retainPanelDrafts, restorePanelDrafts} = await import("../custom_components/extended_openai_conversation_responses/frontend/management-draft-lifecycle.js");

test("recovery rebinds a newer target's pending model lookup without replacing its intent", () => {
  const intent = {}, {source, target} = recoveryPanels("pending", {targetConfig:{chat_model:"gpt-5"}, targetIntent:intent});
  const original = target._draft;
  const preparation = {draft:original, rebindDraft(previous, next) {assert.equal(previous, this.draft); assert.equal(target._draft, previous); this.draft = next;}};
  target._eocPendingModelPreparation = preparation;
  source._eocModelIntent = {transferTo() {assert.fail("an older source intent must not replace the target lookup");}};
  retainPanelDrafts(source);
  restorePanelDrafts(target);
  assert.equal(target._draft.prompt, "Retained");
  assert.equal(target._eocModelIntent, intent);
  assert.equal(preparation.draft, target._draft);
  assert.notEqual(preparation.draft, original);
  // The late result still owns the rebound draft, so its default can complete.
  if (target._eocPendingModelPreparation === preparation && preparation.draft === target._draft) target._draft.reasoning_effort = "minimal";
  assert.equal(target._draft.reasoning_effort, "minimal");
  assert.equal(source._configDirty, false);
});

test("an explicitly selected target reasoning effort survives recovery of an unrelated prompt", () => {
  const {source, target} = recoveryPanels("explicit", {targetConfig:{reasoning_effort:"high"}, reasoningRevision:1});
  source._eocModelIntent = {transferTo() {assert.fail("an explicit target choice must not acquire old automatic provenance");}};
  retainPanelDrafts(source);
  restorePanelDrafts(target);
  assert.equal(target._draft.prompt, "Retained");
  assert.equal(target._draft.reasoning_effort, "high");
  assert.equal(target._eocModelIntent, null);
  assert.equal(target._eocReasoningChoiceRevision, 1);
  assert.equal(source._configDirty, false);
});

test("a declined conflicting recovery does not consume model read provenance", () => {
  const nodes = [];
  globalThis.document = {createElement(tagName) {const node = {tagName, dataset:{}, children:[], handlers:{}, append(...children) {this.children.push(...children);},
    setAttribute() {}, addEventListener(type, handler) {this.handlers[type] = handler;}, showModal() {this.open = true;}, close() {this.open = false; this.handlers.close?.();}, remove() {}}; nodes.push(node); return node;}};
  const {source, target} = recoveryPanels("conflict", {targetConfig:{prompt:"Current target draft"}});
  source._eocModelIntent = {acceptReasoningRead() {assert.fail("conflict checks must precede mutation of derived read provenance");}};
  let copy;
  target.shadowRoot.append = dialog => {copy = dialog;};
  const original = target._draft;
  retainPanelDrafts(source);
  restorePanelDrafts(target);
  assert.equal(target._draft, original);
  assert.equal(source._configDirty, true);
  assert.equal(copy.open, true);
  assert.equal(nodes.find(node => node.tagName === "h2").textContent, "Recovered unsaved configuration");
  assert.deepEqual(JSON.parse(nodes.find(node => node.tagName === "textarea").value), {config:{prompt:"Retained"}});
  const discard = nodes.find(node => node.tagName === "button" && node.textContent === "Discard recovered edits");
  discard.onclick();
  assert.equal(source._configDirty, false);
  assert.equal(target._draft.prompt, "Current target draft");
});
