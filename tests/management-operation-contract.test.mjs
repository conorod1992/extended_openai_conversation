import assert from "node:assert/strict";
import {spawnSync} from "node:child_process";
import {readFileSync} from "node:fs";
import test from "node:test";
import {fileURLToPath} from "node:url";
import {mutationEffects} from "../custom_components/extended_openai_conversation_responses/frontend/management-mutation-effects.js";

const backend = new URL("../custom_components/extended_openai_conversation_responses/", import.meta.url);
const owner = fileURLToPath(new URL("management_operations.py", backend));
const dispatch = readFileSync(new URL("management_ui.py", backend), "utf8");
const begin = dispatch.indexOf("async def async_tools_command(");
const end = dispatch.indexOf("\nasync def async_scopes_command(", begin);
assert.ok(begin >= 0 && end > begin, "Inspect the actual Tools handler when its owner changes");
const frontend = readFileSync(new URL("frontend/management-mutation-effects.js", backend), "utf8");

// Discover candidate names from real consumers, rather than copy the mutation
// inventory into the test. Over-inclusion is intentional: reads/unknown names
// must remain unclassified. This tripwire cannot infer a new writer's semantics
// if BOTH consumers omit it; public persistence/revision tests remain necessary.
const identifiers = new Set(["__proto__", "constructor", "future_contract_probe"]);
for (const source of [dispatch.slice(begin, end), frontend]) {
  for (const match of source.matchAll(/["']([a-z_][a-z0-9_]*)["']/g)) identifiers.add(match[1]);
}
for (const match of frontend.matchAll(/\b([a-z_][a-z0-9_]*)\s*:/g)) identifiers.add(match[1]);
const actions = [...identifiers].sort();

// Load only the pure classification module, not the integration/HA package.
// Frontend CI already uses Python for its bundle checks; no client dependency
// or runtime metadata negotiation is introduced.
const python = process.env.PYTHON || (process.platform === "win32" ? "python" : "python3");
const result = spawnSync(python, ["-c", `
import json, runpy, sys
classify = runpy.run_path(sys.argv[1])["is_tool_mutation"]
print(json.dumps([classify("tools", action) for action in json.load(sys.stdin)]))
`, owner], {input:JSON.stringify(actions), encoding:"utf8"});
assert.equal(result.status, 0, result.error?.message || result.stderr);
const classifications = JSON.parse(result.stdout);
assert.equal(classifications.length, actions.length);

function assertContract(classified, effects) {
  for (let index = 0; index < actions.length; index++) {
    const action = actions[index];
    assert.equal(effects(action) !== null, classified[index], `Tool operation classification drift: ${action}`);
    if (classified[index]) {
      assert.deepEqual(effects(action), {configuration:true, requestRules:true, revision:"tools"}, `Tool mutation effects drift: ${action}`);
    }
  }
}

test("backend admission and actual frontend Tool mutation effects agree", () => {
  assertContract(classifications, action => mutationEffects("tools", action));
});

test("contract tripwire rejects a removed backend classification", () => {
  const altered = [...classifications];
  const index = altered.indexOf(true);
  assert.ok(index >= 0);
  altered[index] = false;
  assert.throws(() => assertContract(altered, action => mutationEffects("tools", action)), /classification drift/);
});

test("contract tripwire rejects changed frontend invalidation effects", () => {
  const changed = actions[classifications.indexOf(true)];
  assert.throws(() => assertContract(classifications, action => {
    const effects = mutationEffects("tools", action);
    return action === changed ? {...effects, configuration:false} : effects;
  }), /mutation effects drift/);
});
