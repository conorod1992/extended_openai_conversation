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

// Request Rules feature mutations have one owner: the lazy Request Rules module.
assert.doesNotMatch(actions, /rule-duplicate|rule-delete|rule-enabled/);
assert.match(rules, /rule-duplicate/);
assert.match(rules, /rule-delete/);
assert.match(rules, /rule-move/);
assert.match(rules, /rule-enabled/);

// Search has one owner: the route-level in-place cached search path.
assert.doesNotMatch(
  rules,
  /#rule-search"\)\?\.addEventListener\("input"[\s\S]*?panel\._render\(\)/,
);
assert.match(route, /export function bindRequestRuleSearch/);
assert.match(route, /applyRequestRuleSearch\(panel, root\)/);
assert.match(route, /event\.stopImmediatePropagation\(\)/);

console.log("Request Rules mutation and search ownership tests passed");
