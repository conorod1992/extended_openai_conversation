import assert from "node:assert/strict";

import {friendlyFieldChange, friendlyFieldChangesForService, mergeActionEditorValue, mergeFriendlyActionValue, parseAdvancedActionConfig, renderRequestRules, requestRulesDialog} from "../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui.js";

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
assert.match(requestRulesDialog(panel), /One phrase per line/);
assert.match(requestRulesDialog(panel), /Rest of this conversation/);
assert.match(requestRulesDialog(panel), /entire sequence before any action runs/);
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
