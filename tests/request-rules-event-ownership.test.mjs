import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const frontend = (name) => new URL(
  `../custom_components/extended_openai_conversation_responses/frontend/${name}`,
  import.meta.url,
);

const [actions, rules, route] = await Promise.all([
  readFile(frontend("management-actions.js"), "utf8"),
  readFile(frontend("request-rules-ui-impl.js"), "utf8"),
  readFile(frontend("management-route.js"), "utf8"),
]);

// Duplicate/Delete/Enable keep their existing active owner in the global
// capture-phase correctness layer. The lazy feature owns Move/Edit/Create only.
assert.match(actions, /button\.classList\.contains\("rule-duplicate"\)/);
assert.match(actions, /button\.classList\.contains\("rule-delete"\)/);
assert.match(actions, /input\?\.classList\?\.contains\("rule-enabled"\)/);
assert.doesNotMatch(rules, /button\.matches\("\.rule-duplicate/);
assert.doesNotMatch(rules, /button\.matches\("\.rule-delete/);
assert.doesNotMatch(rules, /input\.matches\?\.\("\.rule-enabled"\)/);
assert.match(rules, /button\.matches\("\.rule-move"\)/);

// Search has one owner: the route-level in-place cached search path.
assert.doesNotMatch(
  rules,
  /#rule-search"\)\?\.addEventListener\("input"[\s\S]*?panel\._render\(\)/,
);
assert.match(route, /export function bindRequestRuleSearch/);
assert.match(route, /applyRequestRuleSearch\(panel, root\)/);
assert.match(route, /event\.stopImmediatePropagation\(\)/);

console.log("Request Rules mutation and search ownership tests passed");
