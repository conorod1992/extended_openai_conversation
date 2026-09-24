import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const manifest = JSON.parse(await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/dist/manifest.json", import.meta.url), "utf8"));
const entry = Object.entries(manifest).find(([, chunk]) => chunk.isEntry && chunk.name === "management")?.[0];
assert.ok(entry, "the production management entry must exist");

function staticDependencies(source, seen = new Set()) {
  if (seen.has(source)) return seen;
  seen.add(source);
  for (const dependency of manifest[source]?.imports || []) {
    assert.ok(manifest[dependency], `missing production chunk ${dependency}`);
    staticDependencies(dependency, seen);
  }
  return seen;
}

const coldGraph = staticDependencies(entry);
const lazyGraph = new Set();
for (const [source, chunk] of Object.entries(manifest)) {
  if (chunk.isDynamicEntry) staticDependencies(source, lazyGraph);
}
const lazyFeatures = ["management-navigation-search", "usage-data", "management-setting-metadata", "agent-config-tools"];
for (const name of lazyFeatures) {
  const source = Object.entries(manifest).find(([, chunk]) => chunk.name === name)?.[0];
  assert.ok(source, `${name} must be present in the production build`);
  assert.ok(!coldGraph.has(source), `${name} must stay out of the cold management graph`);
  assert.ok(lazyGraph.has(source), `${name} must remain reachable through a lazy boundary`);
}

// The browser lazy-ownership suite also checks that cold Overview/Guide loads
// no feature chunks and that hover, focus, and click warming behave correctly.
for (const dependency of manifest[entry].dynamicImports || []) {
  assert.ok(manifest[dependency]?.isDynamicEntry, `dynamic management dependency ${dependency} must resolve`);
}

const sourceFor = (name) => Object.keys(manifest).find((source) => source.endsWith(`/frontend/${name}.js`));
const backupRoute = sourceFor("backup-route-ui");
const backupTransfer = sourceFor("backup-transfer-ui");
const genericEditor = sourceFor("agent-config-editor");
const genericEditorBase = Object.keys(manifest).find((source) => manifest[source].name === "agent-config-editor-base");
assert.ok(backupRoute && backupTransfer && genericEditor && genericEditorBase);
assert.ok(!coldGraph.has(backupRoute), "Backup route must remain lazy");
assert.ok(manifest[backupRoute].dynamicImports?.includes(backupTransfer), "transfer controls must remain action loaded");
assert.ok(!manifest[backupRoute].dynamicImports?.includes(genericEditor), "Backup activation must not load the generic editor");
const backupActionGraph = staticDependencies(backupTransfer);
assert.ok(!backupActionGraph.has(genericEditor));
assert.ok(!backupActionGraph.has(genericEditorBase));
const backupSummary = sourceFor("backup-summary");
const assistantGraph = staticDependencies(genericEditor);
assert.ok(!backupSummary || !assistantGraph.has(backupSummary),
  "generic Assistant/config activation must not load Backup summary");
const editorSource = await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor.js", import.meta.url), "utf8");
const editorBaseSource = await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor-base.js", import.meta.url), "utf8");
assert.ok(!editorSource.includes('from "./backup-summary.js"'));
assert.ok(!editorBaseSource.includes('from "./backup-summary.js"'));
