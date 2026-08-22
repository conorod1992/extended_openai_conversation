import assert from "node:assert/strict";

import {mergeActionEditorValue, parseAdvancedActionConfig, renderRequestRules, requestRulesDialog} from "../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui.js";

const escape = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
const panel = {
  _e: escape,
  _query: "",
  _result: {
    defaults: {word_forms:true,wording_alternatives:true,fuzzy:false,fuzzy_threshold:90},
    rules: [{
      id:"one",name:"Good night",enabled:true,phrases:["good night","bed time"],match_type:"equals",
      action_type:"local_action",action:{actions:[{domain:"script",service:"turn_on",target:{entity_id:["script.goodnight"]},data:{}}],success_response:"Done"},
      matching_behavior:"defaults",matching:{word_forms:true,wording_alternatives:true,fuzzy:false,fuzzy_threshold:90},order:0,
    }],
  },
};

const html = renderRequestRules(panel);
assert.match(html, /Local commands skip the AI\/API call/);
assert.match(html, /Good night/);
assert.match(html, /Uses default settings/);
assert.match(html, /Create rule/);
assert.match(html, /Fuzzy matching/);
assert.match(requestRulesDialog(panel), /One phrase per line/);
assert.match(requestRulesDialog(panel), /Rest of this conversation/);
assert.match(requestRulesDialog(panel), /entire sequence before any action runs/);

const existing = {domain:"light",service:"turn_on",target:{entity_id:["light.lamp"],area_id:["kitchen"],device_id:"device-one"},data:{brightness_pct:35,transition:2}};
assert.deepEqual(
  mergeActionEditorValue(existing, "light.lamp", JSON.stringify({target:existing.target,data:existing.data}), "light.lamp"),
  existing,
);
assert.deepEqual(
  mergeActionEditorValue(existing, "light.desk", JSON.stringify({target:existing.target,data:existing.data}), "light.lamp"),
  {...existing,target:{...existing.target,entity_id:["light.desk"]}},
);
assert.throws(() => parseAdvancedActionConfig("target: bad"), /valid JSON/);
assert.throws(() => parseAdvancedActionConfig('{"target":[],"data":{}}'), /target must be a JSON object/);
