import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const source = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/management-page-drafts.js", import.meta.url),
  "utf8",
);

const saveBody = source.match(/export async function savePageChanges\(panel\) \{([\s\S]*?)\n\}/)?.[1] || "";
assert.match(source, /function syncSavedPageDom\(panel\)/);
assert.match(source, /function syncRequestRulesSavedDom\(panel\)/);
assert.match(source, /function syncQuietHoursSavedDom\(panel\)/);
assert.match(source, /function syncGuestSavedDom\(panel\)/);
assert.match(saveBody, /syncSavedPageDom\(panel\)/);
assert.doesNotMatch(saveBody, /window\.scrollTo/);
assert.doesNotMatch(saveBody, /querySelectorAll\("main details"\)/);
assert.doesNotMatch(saveBody, /renderSavedDraft/);
assert.match(saveBody, /if \(!syncSavedPageDom\(panel\)\) panel\._render\(\);/);
assert.match(source, /function renderDiscardedDraft\(panel\)/);
