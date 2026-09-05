import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const frontend = (name) => new URL(
  `../custom_components/extended_openai_conversation_responses/frontend/${name}`,
  import.meta.url,
);

const editor = await import(frontend("agent-config-editor-base.js"));
const editorSource = await readFile(frontend("agent-config-editor-base.js"), "utf8");
const panelSource = await readFile(frontend("management-panel.js"), "utf8");
const rulesSource = await readFile(frontend("request-rules-ui-impl.js"), "utf8");

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
assert.equal(summary.textContent, "Validate the document to preview it.");

assert.match(
  editorSource,
  /importDocument\?\.addEventListener\("input", \(\) => invalidateImportPreview/,
  "editing import source must invalidate the validated preview",
);
assert.match(
  editorSource,
  /panel\._importDocument!==source/,
  "apply must defensively reject source that differs from the validated document",
);
assert.match(
  editorSource,
  /revision:panel\._configData\?\.revision/,
  "configuration save/import-current must carry the loaded revision",
);

assert.match(
  rulesSource,
  /const revision = result\.revision/,
  "Request Rule edits must use the revision from the listed state",
);
for (const action of ["duplicate", "delete", "update", "defaults", "wording_groups"]) {
  assert.match(
    rulesSource,
    new RegExp(`panel\\._call\\("request_rules", "${action}", [^\\n]*revision`),
    `${action} mutation must carry a Request Rule revision`,
  );
}
assert.match(
  rulesSource,
  /panel\._editingRuleId \? "update" : "create"[\s\S]{0,220}rule,revision/,
  "create/update form submission must carry a Request Rule revision",
);

assert.match(
  panelSource,
  /section === "backup" && action === "restore"/,
  "successful full restore must have a dedicated cache invalidation path",
);
assert.match(panelSource, /this\._sectionCache\.keys\(\)/);
assert.match(panelSource, /this\._scopeCatalogCache\.keys\(\)/);
