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

function frontendRegistrationSources() {
  return fs
    .readdirSync(COMPONENT, {withFileTypes: true})
    .filter((entry) => entry.isFile() && entry.name.endsWith(".py"))
    .map((entry) => ({
      name: entry.name,
      source: fs.readFileSync(path.join(COMPONENT, entry.name), "utf8"),
    }))
    .filter(({source}) =>
      /\b(?:MANAGEMENT_FRONTEND_MODULES|DEBUG_FRONTEND_MODULES)\b/.test(source),
    );
}

test("every direct management-panel dependency is registered as a served frontend asset", () => {
  const panel = read(
    "custom_components/extended_openai_conversation_responses/frontend/management-panel.js",
  );
  const panelModules = [...panel.matchAll(/from "\.\/([^"']+\.js)"/g)].map(
    (match) => match[1],
  );
  assert.ok(panelModules.length > 0, "management panel dependency list is empty");
  assert.equal(
    fs.existsSync(path.join(
      ROOT,
      "custom_components/extended_openai_conversation_responses/frontend/management-bootstrap.js",
    )),
    false,
    "obsolete management bootstrap should stay deleted",
  );

  // Runtime hardening modules can extend MANAGEMENT_FRONTEND_MODULES before
  // management_ui registers static paths. Discover those registration sources
  // by the variable they mutate rather than maintaining a second allowlist.
  const registrationSources = frontendRegistrationSources();
  assert.ok(registrationSources.length > 0, "frontend registration sources were not found");

  const servedModules = new Set(
    registrationSources.flatMap(({source}) => [...quotedJsFiles(source)]),
  );

  const missing = panelModules.filter((moduleName) => !servedModules.has(moduleName));
  assert.deepEqual(
    missing,
    [],
    `management-panel dependencies are not registered with Home Assistant: ${missing.join(", ")}`,
  );
});
