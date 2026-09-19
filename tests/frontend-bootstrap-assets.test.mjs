import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import {fileURLToPath} from "node:url";

const COMPONENT = fileURLToPath(new URL(
  "../custom_components/extended_openai_conversation_responses/", import.meta.url,
));
const FRONTEND = path.join(COMPONENT, "frontend");
const DIST = path.join(FRONTEND, "dist");
const manifest = JSON.parse(fs.readFileSync(path.join(DIST, "manifest.json"), "utf8"));

function entryByName(name) {
  return Object.values(manifest).find((entry) => entry.isEntry === true && entry.name === name);
}

function manifestSource(suffix) {
  return Object.entries(manifest).find(([source]) => source.endsWith(suffix));
}

function assertOutputExists(file) {
  assert.equal(typeof file, "string");
  assert.ok(fs.existsSync(path.join(DIST, file)), `missing built asset ${file}`);
}

test("production manifest exposes one hashed management entry and lazy route chunks", () => {
  const management = entryByName("management");
  assert.ok(management, "production Management entry is required");
  assert.match(management.file, /^assets\/management-[A-Za-z0-9_-]+\.js$/);
  assertOutputExists(management.file);

  assert.equal(entryByName("debug"), undefined, "Request Debug must not become a duplicate public entry");

  const [, debugPanel] = manifestSource("/debug-panel.js") || [];
  assert.ok(debugPanel, "Request Debug chunk must be present in the production graph");
  assert.equal(debugPanel.isDynamicEntry, true);
  assert.match(debugPanel.file, /^assets\/debug-panel-[A-Za-z0-9_-]+\.js$/);
  assertOutputExists(debugPanel.file);

  for (const source of management.dynamicImports || []) {
    assert.ok(manifest[source], `missing lazy manifest entry for ${source}`);
    assertOutputExists(manifest[source].file);
  }
});

test("every manifest import and emitted file resolves", () => {
  for (const [source, entry] of Object.entries(manifest)) {
    assertOutputExists(entry.file);
    for (const dependency of [...(entry.imports || []), ...(entry.dynamicImports || [])]) {
      assert.ok(manifest[dependency], `${source} references missing manifest dependency ${dependency}`);
      assertOutputExists(manifest[dependency].file);
    }
  }
});
