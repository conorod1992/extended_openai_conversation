import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

import {
  assistantScopeLabel,
  configurationDecisionBadges,
  displayDefaultValue,
  formatLiveRequestResult,
  requestRuleSummary,
} from "../custom_components/extended_openai_conversation_responses/frontend/management-decision-guidance.js";

assert.equal(
  assistantScopeLabel({title:"Kitchen Assistant", entry_title:"OpenAI Home"}),
  "Kitchen Assistant — OpenAI Home",
);
assert.equal(
  assistantScopeLabel({title:"Kitchen Assistant", entry_title:"Kitchen Assistant"}),
  "Kitchen Assistant",
);

assert.equal(displayDefaultValue("api_mode", "auto"), "Automatic (Auto)");
assert.equal(displayDefaultValue("memory_retrieval_mode", "lexical"), "Keyword matching (Lexical)");
assert.deepEqual(
  configurationDecisionBadges("api_mode", {api_mode:"auto"}),
  [
    {kind:"default", text:"Default: Automatic (Auto)"},
    {kind:"recommended", text:"Recommended: Automatic (Auto)"},
  ],
);
assert.deepEqual(
  configurationDecisionBadges("memory_retrieval_mode", {memory_retrieval_mode:"lexical"}),
  [{kind:"default", text:"Default: Keyword matching (Lexical)"}],
);
assert.deepEqual(configurationDecisionBadges("temperature", {temperature:1}), []);

const local = requestRuleSummary({
  action_type:"local_action",
  match_type:"equals",
  matching_behavior:"defaults",
  phrases:["good night", "night", "bed time", "bedtime", "lights out"],
  action:{actions:[{}, {}], success_response:"Good night"},
}, {word_forms:true, wording_alternatives:true, fuzzy:true, fuzzy_threshold:94});
assert.match(local.action, /Runs 2 local steps without an AI request/);
assert.match(local.action, /Good night/);
assert.match(local.matching, /5 trigger phrases · Equals · Default matching · Fuzzy fallback: Conservative/);
assert.equal(local.hiddenPhrases, 1);

const routing = requestRuleSummary({
  action_type:"model_routing",
  match_type:"contains",
  matching_behavior:"custom",
  phrases:["think carefully"],
  matching:{fuzzy:false, fuzzy_threshold:90},
  action:{model:"gpt-5.6", reasoning_effort:"high", scope:"conversation"},
});
assert.equal(routing.action, "Model: gpt-5.6 · High reasoning · rest of conversation");
assert.match(routing.matching, /1 trigger phrase · Contains · Custom matching/);

const sentence = requestRuleSummary({
  action_type:"local_action",
  match_type:"sentence_pattern",
  phrases:["add {item} to list"],
  action:{actions:[{}], success_response:"Added"},
});
assert.match(sentence.matching, /Sentence pattern · Hassil grammar/);

const liveResult = formatLiveRequestResult({
  response:"Done",
  handled_locally:true,
  matched_rule:{name:"Good night"},
  captured_values:{room:"kitchen"},
  conversation_id:"abc123",
});
assert.match(liveResult, /Path: Handled locally/);
assert.match(liveResult, /Matched rule: Good night/);
assert.match(liveResult, /Captured values: room=kitchen/);

const source = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/management-decision-guidance.js", import.meta.url),
  "utf8",
);
assert.match(source, /Preview rule match \(safe\)/);
assert.match(source, /Safe preview — nothing executes/);
assert.match(source, /eoc-rule-live-test/);
assert.match(source, /Run full request\?/);
assert.match(source, /panel\._call\("request_rules", "test", \{text\}\)/);
assert.match(source, /eoc-confirm-scope/);
assert.match(source, /This backup will replace:/);
assert.match(source, /Request Rule “\$\{rule\.name\}”/);

const bootstrap = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/management-bootstrap.js", import.meta.url),
  "utf8",
);
assert.match(bootstrap, /management-decision-guidance\.js/);
assert.ok(
  bootstrap.indexOf('await import("./management-configuration-guidance.js")')
    < bootstrap.indexOf('await import("./management-decision-guidance.js")'),
  "decision guidance should install after configuration clarity/conflict guidance",
);
