import assert from "node:assert/strict";
const frontend = (name) => new URL(
  `../custom_components/extended_openai_conversation_responses/frontend/${name}`,
  import.meta.url,
);

const editor = await import(frontend("agent-config-editor-base.js"));
assert.deepEqual(
  editor.skillNamesFromText("weather, indoor\ncalendar\n\n  local notes  "),
  ["weather, indoor", "calendar", "local notes"],
  "commas inside a Skill name must survive the editor round-trip",
);

const apply = {disabled: false};
const summary = {textContent: "validated"};
const panel = {_importDocument: "old source"};
editor.invalidateImportPreview(panel, apply, summary);
assert.equal(panel._importDocument, null);
assert.equal(apply.disabled, true);
assert.match(summary.textContent, /Validate.*preview/i);

