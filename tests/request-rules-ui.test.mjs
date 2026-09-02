import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

import {createRequestRuleActionSelector, friendlyFieldChange, friendlyFieldChangesForService, loadRequestRuleActions, mergeActionEditorValue, mergeFriendlyActionValue, parseAdvancedActionConfig, readRequestRuleActions, refreshRequestRuleSlotSelectors, renderRequestRules, requestRulesDialog} from "../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui.js";

const escape = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
const panel = {
  _e: escape,
  _query: "",
  _result: {
    defaults: {word_forms:true,wording_alternatives:true,fuzzy:false,fuzzy_threshold:90},
    wording_groups: [{canonical:"turn on",alternatives:["switch on"]}],
    rules: [{
      id:"one",name:"Good night",enabled:true,phrases:["good night","bed time"],match_type:"equals",
      action_type:"local_action",action:{actions:[{domain:"script",service:"turn_on",target:{entity_id:["script.goodnight"]},data:{}}],success_response:"Done"},
      matching_behavior:"defaults",matching:{word_forms:true,wording_alternatives:true,fuzzy:false,fuzzy_threshold:90},order:0,
    }, {
      id:"two",name:"Think carefully",enabled:true,phrases:["think carefully"],match_type:"starts_with",
      action_type:"model_routing",action:{model:"gpt-5",reasoning_effort:"high",scope:"conversation",reset:false,success_response:"Updated"},
      matching_behavior:"defaults",matching:{word_forms:true,wording_alternatives:true,fuzzy:false,fuzzy_threshold:90},order:1,
    }],
  },
};

const html = renderRequestRules(panel);
assert.match(html, /Local commands skip the AI\/API call/);
assert.match(html, /Good night/);
assert.match(html, /Uses default settings/);
assert.match(html, /Create rule/);
assert.match(html, /Fuzzy matching/);
assert.match(html, /Wording alternatives/);
assert.match(html, /These settings apply to rules unless a rule has its own custom matching settings\./);
assert.match(html, /Normal matches are always preferred before fuzzy matching is tried\./);
assert.match(html, /class="matching-setting"/);
assert.match(html, /class="matching-copy"/);
assert.match(html, /Treats simple variations such as “light” and “lights” as the same\./);
assert.match(html, /Uses your saved alternative phrases, such as “switch on” matching “turn on”\./);
assert.match(html, /Allows small speech-recognition or typing mistakes when no normal match succeeds\./);
assert.match(html, /Controls how close a phrase must be before fuzzy matching is accepted\. Conservative is least likely to match the wrong rule\./);
assert.match(html, /Main phrase/);
assert.match(html, /Other ways to say it/);
assert.match(html, /Save wording alternatives/);
assert.match(html, /gpt-5 · high reasoning · rest of conversation/);
const resetHtml = renderRequestRules({...panel,_result:{...panel._result,rules:[{...panel._result.rules[1],action:{...panel._result.rules[1].action,reset:true}}]}});
assert.match(resetHtml, /Return this conversation to configured defaults/);
assert.doesNotMatch(resetHtml, /high reasoning/);
assert.match(requestRulesDialog(panel), /Alternatives must use the same variable names/);
assert.match(requestRulesDialog(panel), /Variable values let part of the request change each time/);
assert.match(requestRulesDialog(panel), /id="rule-action-sequence-host"/);
assert.doesNotMatch(requestRulesDialog(panel), /<ha-selector/);
assert.match(requestRulesDialog(panel), /Conditions, delays, choose, repeat, parallel/);
assert.match(requestRulesDialog(panel), /extended_openai_conversation_responses\.call_function/);
assert.match(requestRulesDialog(panel), /\{\{ item \}\}/);
const bindingSource = await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui-impl.js", import.meta.url), "utf8");
assert.match(bindingSource, /Value from request/);
assert.match(bindingSource, /function_catalog/);
assert.match(bindingSource, /Captured values:/);
assert.match(bindingSource, /selector = \{action:\{\}\}/);

{
  const hass = {localize: () => "localized"};
  const assignments = [];
  let connected = false;
  let valueChanged;
  const actionSelector = {
    set hass(value) { assignments.push(["hass", connected]); this._hass = value; },
    get hass() { return this._hass; },
    set selector(value) { assignments.push(["selector", connected]); this._selector = value; },
    get selector() { return this._selector; },
    set value(value) { assignments.push(["value", connected]); this._value = value; },
    get value() { return this._value; },
    addEventListener(type, listener) {
      assert.equal(type, "value-changed");
      assignments.push(["listener", connected]);
      valueChanged = listener;
    },
  };
  const host = {
    ownerDocument: {createElement: (tagName) => {
      assert.equal(tagName, "ha-selector");
      return actionSelector;
    }},
    replaceChildren(child) {
      assert.equal(child, actionSelector);
      connected = true;
    },
  };
  assert.equal(createRequestRuleActionSelector({_hass:hass}, host), actionSelector);
  assert.equal(actionSelector.hass, hass);
  assert.deepEqual(actionSelector.selector, {action:{}});
  assert.deepEqual(actionSelector.value, []);
  assert.deepEqual(assignments, [["hass",false],["selector",false],["value",false],["listener",false]]);

  const existingActions = [{action:"light.turn_on",data:{brightness_pct:50}}];
  loadRequestRuleActions(actionSelector, {action:{actions:existingActions}});
  assert.equal(actionSelector.value, existingActions);
  const editedActions = [{delay:1}];
  valueChanged({detail:{value:editedActions}});
  assert.equal(readRequestRuleActions(actionSelector), editedActions);
}

const makeSlotSelect = (value) => ({
  value,
  options: [],
  refreshes: 0,
  ownerDocument: {createElement: () => ({value:"",textContent:""})},
  replaceChildren(...options) { this.options = options; },
  _refreshSlotBinding() { this.refreshes += 1; },
});
const haSlot = makeSlotSelect("item"), functionSlot = makeSlotSelect("");
const slotRoot = {querySelectorAll: () => [haSlot, functionSlot]};
refreshRequestRuleSlotSelectors(slotRoot, ["item", "product"]);
assert.equal(haSlot.value, "item");
assert.equal(functionSlot.value, "");
assert.deepEqual(functionSlot.options.map((option) => option.value), ["", "item", "product"]);
refreshRequestRuleSlotSelectors(slotRoot, ["product"]);
assert.equal(haSlot.value, "");
assert.equal(functionSlot.value, "");
assert.equal(haSlot.refreshes, 2);
functionSlot.value = "product";
refreshRequestRuleSlotSelectors(slotRoot, ["product", "list_name"]);
assert.equal(functionSlot.value, "product");
assert.match(requestRulesDialog(panel), /Rest of this conversation/);
assert.match(requestRulesDialog(panel), /without asking the AI model/);
assert.match(requestRulesDialog(panel), /Home Assistant sentence pattern/);
assert.match(requestRulesDialog(panel), /Named expansions/);
assert.match(requestRulesDialog(panel), /1\. What will you say\?/);
assert.match(requestRulesDialog(panel), /3\. What should the assistant say\?/);
assert.match(requestRulesDialog(panel), /class="matching-setting"/);
assert.match(requestRulesDialog(panel), /Treats simple variations such as “light” and “lights” as the same\./);

const existing = {domain:"light",service:"turn_on",target:{entity_id:["light.lamp"],area_id:["kitchen"],device_id:"device-one",floor_id:["ground"],label_id:["ambient"],custom_target:"keep-me"},data:{brightness_pct:35,transition:2}};
const advanced = JSON.stringify({target:existing.target,data:existing.data});
assert.deepEqual(
  mergeActionEditorValue(existing, "light.lamp", advanced, "light.lamp"),
  existing,
);
assert.deepEqual(
  mergeFriendlyActionValue(existing, "area_id", ["garage"], advanced, "entity_id", ["light.lamp"]),
  {...existing,target:{area_id:["garage"],custom_target:"keep-me"}},
);
assert.deepEqual(
  mergeFriendlyActionValue(existing, "device_id", ["device-two"], advanced, "area_id", ["kitchen"]),
  {...existing,target:{device_id:["device-two"],custom_target:"keep-me"}},
);
assert.deepEqual(
  mergeActionEditorValue(existing, "light.desk", advanced, "light.lamp"),
  {...existing,target:{entity_id:["light.desk"],custom_target:"keep-me"}},
);
assert.deepEqual(
  mergeFriendlyActionValue(existing, "", [], advanced, "entity_id", ["light.lamp"]),
  {...existing,target:{custom_target:"keep-me"}},
);

assert.deepEqual(mergeFriendlyActionValue(existing, "entity_id", ["light.lamp"], advanced, "entity_id", ["light.lamp"], {}), existing);
assert.deepEqual(
  mergeFriendlyActionValue(existing, "entity_id", ["light.lamp"], advanced, "entity_id", ["light.lamp"], {brightness_pct:{operation:"set",value:50}}),
  {...existing,data:{brightness_pct:50,transition:2}},
);
assert.deepEqual(
  mergeFriendlyActionValue(existing, "entity_id", ["light.lamp"], advanced, "entity_id", ["light.lamp"], {brightness_pct:{operation:"delete"}}),
  {...existing,data:{transition:2}},
);
assert.deepEqual(friendlyFieldChange(0), {operation:"set",value:0});
assert.deepEqual(friendlyFieldChange(false), {operation:"set",value:false});
assert.deepEqual(friendlyFieldChange(""), {operation:"delete"});
assert.deepEqual(friendlyFieldChange(null), {operation:"delete"});
assert.deepEqual(
  mergeFriendlyActionValue(existing, "entity_id", ["light.lamp"], advanced, "entity_id", ["light.lamp"], {brightness_pct:friendlyFieldChange(0),enabled:friendlyFieldChange(false)}).data,
  {brightness_pct:0,transition:2,enabled:false},
);
const oldServiceChanges = {brightness_pct:friendlyFieldChange(50)};
assert.equal(friendlyFieldChangesForService("light.turn_on", "light.turn_on", oldServiceChanges), oldServiceChanges);
assert.deepEqual(friendlyFieldChangesForService("light.turn_on", "media_player.volume_set", oldServiceChanges), {});
assert.deepEqual(
  mergeFriendlyActionValue({domain:"media_player",service:"volume_set"}, "entity_id", ["media_player.lounge"], advanced, "entity_id", ["light.lamp"], friendlyFieldChangesForService("light.turn_on", "media_player.volume_set", oldServiceChanges)),
  {domain:"media_player",service:"volume_set",target:{entity_id:["media_player.lounge"],custom_target:"keep-me"},data:existing.data},
);
assert.throws(() => parseAdvancedActionConfig("target: bad"), /valid JSON/);
assert.throws(() => parseAdvancedActionConfig('{"target":[],"data":{}}'), /target must be a JSON object/);
