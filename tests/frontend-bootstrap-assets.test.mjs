import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import {fileURLToPath} from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..");
const COMPONENT = path.join(
  ROOT,
  "custom_components",
  "extended_openai_conversation_responses",
);

function read(relativePath) {
  return fs.readFileSync(path.join(ROOT, relativePath), "utf8");
}

function quotedJsFiles(source) {
  return new Set(
    [...source.matchAll(/["']([^"']+\.js)["']/g)].map((match) =>
      path.posix.basename(match[1]),
    ),
  );
}

test("every management bootstrap module is registered as a served frontend asset", () => {
  const bootstrap = read(
    "custom_components/extended_openai_conversation_responses/frontend/management-bootstrap.js",
  );
  const moduleBlock = bootstrap.match(
    /const BOOTSTRAP_MODULES = \[([\s\S]*?)\];/,
  );
  assert.ok(moduleBlock, "management bootstrap module list was not found");

  const bootstrapModules = [...moduleBlock[1].matchAll(/["']\.\/([^"']+\.js)["']/g)].map(
    (match) => match[1],
  );
  assert.ok(bootstrapModules.length > 0, "management bootstrap module list is empty");

  // Management assets are registered through the base management UI, split-module
  // additions in __init__, and the separately initialized request-debug UI.
  const registrationSources = [
    path.join(COMPONENT, "management_ui.py"),
    path.join(COMPONENT, "__init__.py"),
    path.join(COMPONENT, "debug_ui.py"),
  ].map((filename) => fs.readFileSync(filename, "utf8"));
  const servedModules = new Set(
    registrationSources.flatMap((source) => [...quotedJsFiles(source)]),
  );

  const missing = bootstrapModules.filter((moduleName) => !servedModules.has(moduleName));
  assert.deepEqual(
    missing,
    [],
    `bootstrap modules are not registered with Home Assistant: ${missing.join(", ")}`,
  );
});
