import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

import {
  HELP_METADATA,
  helpSearchTerms,
} from "../custom_components/extended_openai_conversation_responses/frontend/agent-config-help.js";

const allowedFields = new Set([
  "title",
  "paragraphs",
  "items",
  "example",
  "keywords",
  "href",
]);
const editorSource = await readFile(
  new URL(
    "../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor.js",
    import.meta.url,
  ),
  "utf8",
);

assert.ok(Object.keys(HELP_METADATA).length > 0, "help metadata must not be empty");
for (const [key, entry] of Object.entries(HELP_METADATA)) {
  assert.match(key, /^[a-z][a-z0-9_]*$/, `${key} is not a valid help key`);
  assert.equal(typeof entry.title, "string", `${key} needs a title`);
  assert.ok(entry.title.trim(), `${key} has an empty title`);
  assert.ok(
    entry.paragraphs?.length || entry.items?.length,
    `${key} needs explanatory content`,
  );
  assert.deepEqual(
    Object.keys(entry).filter((field) => !allowedFields.has(field)),
    [],
    `${key} has unsupported metadata fields`,
  );
  for (const paragraph of entry.paragraphs || []) {
    assert.equal(typeof paragraph, "string", `${key} has a malformed paragraph`);
    assert.ok(paragraph.trim(), `${key} has an empty paragraph`);
  }
  for (const item of entry.items || []) {
    assert.equal(typeof item.term, "string", `${key} has a malformed item term`);
    assert.equal(typeof item.text, "string", `${key} has malformed item text`);
    assert.ok(item.term.trim() && item.text.trim(), `${key} has an empty help item`);
  }
  if (entry.href) assert.match(entry.href, /^https:\/\//, `${key} has an invalid documentation link`);
  assert.ok(helpSearchTerms(key).includes(entry.title), `${key} is missing from help search text`);
  assert.ok(editorSource.includes(`"${key}"`), `${key} is not referenced by the UI`);
}

assert.equal(helpSearchTerms("missing_help_key"), "");
