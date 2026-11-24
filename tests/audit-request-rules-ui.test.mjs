import assert from "node:assert/strict";
import {syncRequestRuleRoutingControls} from "../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui-impl.js";
import {formatWordingAlternatives, parseWordingAlternatives} from "../custom_components/extended_openai_conversation_responses/frontend/wording-alternatives.js";

const phrases = ["would you, please", "say \"hello\"", "switch on"];
assert.deepEqual(parseWordingAlternatives(formatWordingAlternatives(phrases)), phrases);
assert.deepEqual(parseWordingAlternatives("one, two"), ["one", "two"]);

const option = {disabled:false};
const scope = {value:"request",querySelector:()=>option};
const reasoning = {value:"{effort}",ownerDocument:{createElement:()=>({})},replaceChildren(...options){this.options=options;}};
const elements = new Map([["#rule-action-type",{value:"model_routing"}],["#rule-match",{value:"equals"}],["#rule-scope",scope],["#rule-continue-to-ai",{checked:true}],["#rule-reasoning",reasoning]]);
const root = {querySelector:(key)=>elements.get(key)};
syncRequestRuleRoutingControls(root, ["low", "high"]);
assert.equal(scope.value,"request");
assert.equal(scope.disabled,false);
assert.equal(reasoning.value,"{effort}");
assert.ok(reasoning.options.some((entry)=>entry.value==="{effort}"));
reasoning.value="xhigh";
syncRequestRuleRoutingControls(root, []);
assert.equal(reasoning.value,"");
assert.ok(!reasoning.options.some(entry => entry.value === "xhigh"));
