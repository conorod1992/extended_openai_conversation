import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import {fileURLToPath} from "node:url";

const COMPONENT = fileURLToPath(new URL(
  "../custom_components/extended_openai_conversation_responses/", import.meta.url,
));
const FRONTEND = path.join(COMPONENT, "frontend");

function registry(source) {
  const match = source.match(/^MANAGEMENT_FRONTEND_MODULES(?:\s*:[^=\n]+)?\s*=\s*\(([\s\S]*?)^\)/m);
  assert.ok(match, "management_ui.py must declare the complete static registry");
  return [...match[1].matchAll(/"([^"\n]+\.js)"/g)].map((item) => item[1]);
}

const modules = registry(fs.readFileSync(path.join(COMPONENT, "management_ui.py"), "utf8"));

function assertImportsServed(registered) {
  // Request Debug retains its separately owned setup/HTTP registration.
  const served = new Set([...registered, "debug-panel.js"]);
  for (const name of served) {
    const source = fs.readFileSync(path.join(FRONTEND, name), "utf8");
    const imports = [...source.matchAll(/(?:\bfrom\s*|\bimport\s*(?:\(\s*)?)["']\.\/([^"']+\.js)["']/g)];
    for (const match of imports) {
      assert.ok(served.has(match[1]), `${name} imports unserved module ${match[1]}`);
    }
  }
}

test("the canonical registry covers every static and lazy frontend import", () => {
  assert.equal(modules.length, new Set(modules).size, "duplicate static routes");
  assertImportsServed(modules);
  assert.equal(fs.existsSync(path.join(FRONTEND, "management-bootstrap.js")), false);
  assert.equal(fs.existsSync(path.join(FRONTEND, "guide-page-base.js")), false);
  assert.equal(modules.includes("guide-page-base.js"), false);
  assert.equal(modules.includes("agent-config-model-presentation.js"), true);
});

test("missing direct or lazy assets cannot be hidden by unrelated Python strings", () => {
  assert.throws(() => assertImportsServed(modules.filter((name) => name !== "management-route.js")),
    /imports unserved module management-route\.js/);
  assert.throws(() => assertImportsServed(modules.filter((name) => name !== "quiet-hours-ui.js")),
    /imports unserved module quiet-hours-ui\.js/);
});
