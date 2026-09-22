import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

import {applyRequestRuleMutation} from "../custom_components/extended_openai_conversation_responses/frontend/request-rules-ui-core.js";

const frontend = (name) => new URL(
  `../custom_components/extended_openai_conversation_responses/frontend/${name}`,
  import.meta.url,
);

const [entrySource, coreSource, editorSource, actionsSource, draftsSource] = await Promise.all([
  readFile(frontend("request-rules-ui.js"), "utf8"),
  readFile(frontend("request-rules-ui-core.js"), "utf8"),
  readFile(frontend("request-rules-ui-impl.js"), "utf8"),
  readFile(frontend("management-actions.js"), "utf8"),
  readFile(frontend("management-page-drafts.js"), "utf8"),
]);

assert.match(entrySource, /import\("\.\/request-rules-ui-impl\.js"\)/);
assert.match(entrySource, /import\("\.\/request-rules-match-test-ui\.js"\)/);
assert.doesNotMatch(entrySource, /^import .*model-catalog/m);
assert.doesNotMatch(entrySource, /^import .*request-rules-match-test-ui/m);
assert.doesNotMatch(coreSource, /model-catalog|request-rules-match-test-ui|management-decision-guidance/);
assert.match(editorSource, /import\("\.\/model-catalog\.js"\)/);
assert.doesNotMatch(editorSource, /^import .*model-catalog/m);
assert.doesNotMatch(actionsSource, /rule-duplicate|rule-delete|rule-enabled/);
assert.match(draftsSource, /panel\._call\("request_rules", "settings"/);
assert.doesNotMatch(draftsSource, /for \(const key of \["defaults", "wording_groups"\]\)/);

const baseRules = [
  {id:"one",name:"One",enabled:true,order:0},
  {id:"two",name:"Two",enabled:true,order:1},
];
const scope = {revision:"r0", result:null};
const panel = {
  _result: {revision:"r0",rules:baseRules},
  _sectionCache: new Map([["rules",{stale:true}]]),
  _sectionCacheKey: () => "rules",
  _unsavedState: {scopes:new Map([["capabilities/request-rules",scope]])},
  _render() { this.renders=(this.renders||0)+1; },
};

assert.equal(applyRequestRuleMutation(panel,"update",{revision:"r1",rule:{...baseRules[0],name:"Updated"}},{ruleId:"one"}),true);
assert.equal(panel._result.rules[0].name,"Updated");
assert.equal(panel._result.revision,"r1");
assert.equal(scope.revision,"r1");
assert.equal(panel._sectionCache.has("rules"),false);

applyRequestRuleMutation(panel,"duplicate",{revision:"r2",rule:{id:"copy",name:"Copy",enabled:true,order:1}},{ruleId:"one"});
assert.deepEqual(panel._result.rules.map((rule)=>rule.id),["one","copy","two"]);
assert.deepEqual(panel._result.rules.map((rule)=>rule.order),[0,1,2]);

applyRequestRuleMutation(panel,"move",{revision:"r3",rule:{id:"two",name:"Two",enabled:true,order:1}},{ruleId:"two",direction:"up"});
assert.deepEqual(panel._result.rules.map((rule)=>rule.id),["one","two","copy"]);
assert.deepEqual(panel._result.rules.map((rule)=>rule.order),[0,1,2]);

applyRequestRuleMutation(panel,"delete",{revision:"r4",deleted:true},{ruleId:"two"});
assert.deepEqual(panel._result.rules.map((rule)=>rule.id),["one","copy"]);
assert.equal(panel._result.revision,"r4");
assert.equal(panel.renders,4);

console.log("Request Rules fast-path dependency and authoritative mutation tests passed");
