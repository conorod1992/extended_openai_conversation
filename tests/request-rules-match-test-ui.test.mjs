import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

import {
  formatRequestRuleMatchResult,
  renderRequestRuleMatchTester,
  transformRequestRulesMatchTester,
} from "../custom_components/extended_openai_conversation_responses/frontend/request-rules-match-test-ui.js";

const panel = {
  _e: (value) => String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;"),
};

const tester = renderRequestRuleMatchTester();
assert.match(tester, /Test matching/);
assert.match(tester, /Safe preview only/);
assert.match(tester, /same matcher as real requests/);
assert.match(tester, /does not run Home Assistant actions/);
assert.match(tester, /does not .*call the AI provider/i);
assert.match(tester, /id="rule-match-test"/);
assert.match(tester, />Test match</);
assert.doesNotMatch(tester, /Process request/);

const transformed = transformRequestRulesMatchTester(
  '<main>Rules</main><section class="content-card"><h2>Test a request</h2><p>dangerous legacy tester</p></section>',
);
assert.match(transformed, /<main>Rules<\/main>/);
assert.match(transformed, /Test matching/);
assert.doesNotMatch(transformed, /dangerous legacy tester/);

const noMatch = formatRequestRuleMatchResult(panel, {matched:false});
assert.match(noMatch, /No Request Rule matched/);
assert.match(noMatch, /No Home Assistant action ran/);
assert.match(noMatch, /AI provider was not called/);

const localMatch = formatRequestRuleMatchResult(panel, {
  matched:true,
  rule:{name:"Kitchen <night>",match_type:"sentence_pattern",action_type:"local_action"},
  matched_phrase:"good night {room}",
  fuzzy:false,
  score:100,
  captured_values:{room:"kitchen"},
  would_do:{type:"local_action",action_count:2},
});
assert.match(localMatch, /Matched: Kitchen &lt;night&gt;/);
assert.match(localMatch, /Sentence pattern/);
assert.match(localMatch, /Normal match/);
assert.match(localMatch, /room → kitchen/);
assert.match(localMatch, /Would run 2 local actions\. Nothing was executed\./);

const routingMatch = formatRequestRuleMatchResult(panel, {
  matched:true,
  rule:{name:"Think carefully",match_type:"starts_with",action_type:"model_routing"},
  matched_phrase:"think carefully",
  fuzzy:true,
  score:93.47,
  captured_values:{},
  would_do:{type:"model_routing",reset:false,model:"gpt-5",reasoning_effort:"high",scope:"conversation"},
});
assert.match(routingMatch, /Fuzzy match · 93\.5%/);
assert.match(routingMatch, /model gpt-5 and high reasoning/);
assert.match(routingMatch, /rest of this conversation/);
assert.match(routingMatch, /Nothing was changed and the AI provider was not called/);

const source = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/request-rules-match-test-ui.js", import.meta.url),
  "utf8",
);
assert.match(source, /panel\._call\("request_rules", "test_match", \{text\}\)/);
assert.doesNotMatch(source, /"process"/);
