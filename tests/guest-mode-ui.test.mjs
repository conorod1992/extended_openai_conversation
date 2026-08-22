import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const panel = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/management-panel.js", import.meta.url),
  "utf8",
);
const editor = await readFile(
  new URL("../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor.js", import.meta.url),
  "utf8",
);

assert.match(panel, /capabilities\/guest-mode/);
assert.match(panel, /guest_mode", "update"/);
assert.match(panel, /guest_mode", "disable"/);
assert.match(panel, /Voice and model safety/);
assert.match(panel, /Enable Guest Mode controls/);
assert.match(panel, /guest_excluded_entities/);
assert.match(panel, /guest_control_excluded_entities/);
assert.match(panel, /guest_knowledge_source_ids/);
assert.match(panel, /guest_allowed_function_names/);
assert.match(panel, /guest_shared_memory_policy/);
assert.match(panel, /ha-selector/);
assert.match(panel, /save_policy/);
assert.match(panel, /Excluded labels/);
assert.match(panel, /Excluded areas/);
assert.match(panel, /Excluded domains/);
assert.match(panel, /Excluded entities/);
assert.ok(panel.indexOf('"Excluded labels"') < panel.indexOf('"Excluded areas"'));
assert.ok(panel.indexOf('"Excluded areas"') < panel.indexOf('"Excluded domains"'));
assert.ok(panel.indexOf('"Excluded domains"') < panel.indexOf('"Excluded entities"'));
assert.match(panel, /Use separate control restrictions/);
assert.match(panel, /New Knowledge sources added later will also become available to guests/);
assert.match(panel, /New eligible functions added later will also become available to guests/);
assert.match(panel, /Turning it off never ends or weakens an active schedule/);
assert.doesNotMatch(editor, /Guest Mode policy/);
assert.doesNotMatch(editor, /group-guest-allowed/);
