import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

import {
  formatRequestRuleMatchResult,
  renderRequestRuleMatchTester,
  REQUEST_RULE_MATCH_MAX_CHARS,
} from "../custom_components/extended_openai_conversation_responses/frontend/request-rules-match-test-ui.js";

const panel = {
  _e: (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;"),
};

const tester = renderRequestRuleMatchTester();
assert.equal(REQUEST_RULE_MATCH_MAX_CHARS, 2048);
assert.match(tester, /Preview rule match \(safe\)/);
assert.match(tester, /Safe preview — nothing executes/);
assert.match(tester, /using the real matcher/);
assert.match(tester, /No Home Assistant action runs/);
assert.match(tester, /AI provider is not called/i);
assert.match(tester, /maxlength="2048"/);
assert.match(tester, /256 words/);
assert.match(tester, /id="rule-match-test"/);
assert.match(tester, />Preview match</);
assert.doesNotMatch(tester, /Process request/);

const noMatch = formatRequestRuleMatchResult(panel, {matched:false});
assert.match(noMatch, /No Request Rule matched/);
assert.match(noMatch, /processing would continue normally/);
assert.match(noMatch, /original request could reach the AI provider/);

const localMatch = formatRequestRuleMatchResult(panel, {
  matched:true,
  rule:{name:"Kitchen <night>",match_type:"sentence_pattern",action_type:"local_action"},
  matched_phrase:"good night {room}",
  fuzzy:false,
  score:100,
  captured_values:{room:"kitchen"},
  would_do:{type:"local_action",action_count:2,consumed:true,provider_input:"none"},
});
assert.match(localMatch, /Matched: Kitchen &lt;night&gt;/);
assert.match(localMatch, /Sentence pattern/);
assert.match(localMatch, /Normal match/);
assert.match(localMatch, /room → kitchen/);
assert.match(localMatch, /Configured: 2 local actions/);
assert.match(localMatch, /command would be consumed locally/);

const continued = formatRequestRuleMatchResult(panel, {
  matched:true,rule:{name:"Battery",match_type:"equals",action_type:"local_action"},
  matched_phrase:"battery",fuzzy:false,score:100,captured_values:{},
  skipped_conditions:[{name:"Earlier rule",reason:"conditions_false"}],
  would_do:{type:"local_action",action_count:1,consumed:false,provider_input:"original",functions:[{name:"get_battery",result_alias:"battery"}]},
});
assert.match(continued, /get_battery → battery/);
assert.match(continued, /selected AI input would continue to the AI/);
assert.match(continued, /Earlier rule: Only when conditions were false/);
assert.match(continued, /no Function result was invented/);

const broadRouting = formatRequestRuleMatchResult(panel, {
  matched:true,
  rule:{name:"Think carefully",match_type:"starts_with",action_type:"model_routing"},
  matched_phrase:"think carefully",
  fuzzy:false,
  score:100,
  captured_values:{},
  would_do:{type:"model_routing",reset:false,model:"gpt-5",reasoning_effort:"high",scope:"conversation",consumed:false,provider_input:"original"},
});
assert.match(broadRouting, /Normal match/);
assert.match(broadRouting, /model gpt-5 and high reasoning/);
assert.match(broadRouting, /send the selected AI input/);
assert.match(broadRouting, /this request and later requests in this conversation/);

const exactRouting = formatRequestRuleMatchResult(panel, {
  matched:true,
  rule:{name:"Use Astra",match_type:"equals",action_type:"model_routing"},
  matched_phrase:"use astra",
  fuzzy:false,
  score:100,
  captured_values:{},
  would_do:{type:"model_routing",reset:false,model:"gpt-6-astra",reasoning_effort:"max",scope:"conversation",consumed:true,provider_input:"none"},
});
assert.match(exactRouting, /consume this command locally/);
assert.match(exactRouting, /AI provider would not receive this command/);

const requestReset = formatRequestRuleMatchResult(panel, {
  matched:true,
  rule:{name:"Default just this",match_type:"starts_with",action_type:"model_routing"},
  matched_phrase:"default just this",
  fuzzy:false,
  score:100,
  captured_values:{},
  would_do:{type:"model_routing",reset:true,model:null,reasoning_effort:null,scope:"request",consumed:false,provider_input:"original"},
});
assert.match(requestReset, /configured model settings for this request only/);
assert.match(requestReset, /saved conversation routing override would remain for the next request/);

const source = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/request-rules-match-test-ui.js", import.meta.url),
  "utf8",
);
assert.match(source, /panel\._call\("request_rules", "test_match", \{text\}\)/);
assert.doesNotMatch(source, /"process"/);

const conditional = formatRequestRuleMatchResult(panel, {
  matched:true, conditional:true,
  rule:{name:"Second <possible>",match_type:"equals",action_type:"model_routing"},
  matched_rules:[
    {rule:{name:"First"},status:"conditional",ai_input:null},
    {rule:{name:"Second <possible>"},status:"conditional",ai_input:{conditional:true,mode:"capture",capture:"question",provider_input:"what <next>"}},
  ],
  would_do:{type:"model_routing",conditional:true,consumed:null,provider_input:"conditional",model:"gpt-5"},
});
assert.match(conditional, /Conditional match: Second &lt;possible&gt;/);
assert.match(conditional, /First — conditional/);
assert.match(conditional, /Possible AI input: Captured value: question → what &lt;next&gt;/);
assert.match(conditional, /any AI handoff is conditional/);
assert.doesNotMatch(conditional, /Would send|Matched: Second|would send to AI, stopped/);

const uncertainLocal = formatRequestRuleMatchResult(panel, {
  matched:true,conditional:true,
  rule:{name:"Local",match_type:"equals",action_type:"local_action"},
  would_do:{conditional:true,action_count:1,consumed:null,provider_input:"conditional",functions:[{name:"battery",result_alias:"charge"}]},
});
assert.match(uncertainLocal, /including battery → charge/);
assert.match(uncertainLocal, /Conditional: actions may stop or fail/);
assert.doesNotMatch(uncertainLocal, /input would continue to the AI/);

const terminal = formatRequestRuleMatchResult(panel, {
  matched:true,rule:{name:"First",match_type:"equals",action_type:"local_action"},
  matched_rules:[{rule:{name:"First"},status:"stopped",ai_input:null}],
  would_do:{type:"local_action",action_count:1,terminal:true,consumed:true,provider_input:"none"},
});
assert.match(terminal, /Matched: First/);
assert.match(terminal, /First — stopped/);
assert.match(terminal, /command would be consumed locally/);
assert.doesNotMatch(terminal, /AI input|Second|continue to the AI/);

for (const type of ["local_action", "model_routing"]) {
  const continuing = formatRequestRuleMatchResult(panel, {
    matched:true,rule:{name:"First",match_type:"equals",action_type:type},
    would_do:{type,continue_matching:true,action_count:1,consumed:true,provider_input:"none"},
  });
  assert.match(continuing, /checking (?:would continue to )?later rules/);
  assert.doesNotMatch(continuing, /command would be consumed locally|input would continue to the AI/);
}

const repair = formatRequestRuleMatchResult(panel, {
  matched:true,rule:{name:"Old alias",match_type:"equals",action_type:"local_action"},
  would_do:{type:"local_action",action_count:0,terminal:true,consumed:true,needs_repair:true,provider_input:"none"},
});
assert.match(repair, /needs repair/);
assert.match(repair, /stop here without running its actions/);
