import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

import {knowledgeSourceAvailabilityBadge} from "../custom_components/extended_openai_conversation_responses/frontend/management-capabilities-ia.js";

assert.match(knowledgeSourceAvailabilityBadge({enabled:true}), /availability-badge[^>]*>Available</);
assert.match(knowledgeSourceAvailabilityBadge({enabled:false}), /disabled-badge[^>]*>Unavailable</);
assert.match(knowledgeSourceAvailabilityBadge({}), /availability-badge[^>]*>Available/);

const source = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/management-capabilities-ia.js", import.meta.url),
  "utf8",
);
assert.match(source, /id=\"knowledge-source-enabled\" type=\"checkbox\" role=\"switch\" checked/);
assert.match(source, /Available to the assistant/);
assert.match(source, /keep the source stored locally without including it in Knowledge retrieval/);
assert.match(source, /prototype\._knowledge = function/);
assert.match(source, /decorateKnowledgeSources\(this, originalKnowledge\.apply\(this, args\)\)/);
assert.match(source, /prototype\._dialogs = function/);
assert.match(source, /addKnowledgeSourceAvailabilityControl\(originalDialogs\.apply\(this, args\)\)/);
assert.match(source, /prototype\._knowledgeValues = function/);
assert.match(source, /enabled: input\?\.checked \?\? true/);
assert.match(source, /prototype\._setKnowledgeEditorDisabled = function/);
assert.match(source, /prototype\._openKnowledge = async function/);
assert.match(source, /this\._editingSource\?\.enabled !== false/);
assert.match(source, /this\._editorInitial = this\._knowledgeValues\(\)/);
assert.doesNotMatch(source, /id=\"knowledge-enabled-toggle\"[^\n]*knowledge-source-enabled/);
