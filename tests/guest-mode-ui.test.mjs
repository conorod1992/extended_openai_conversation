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

assert.match(panel, /"guest"/);
assert.match(panel, /guest_mode", "update"/);
assert.match(panel, /guest_mode", "disable"/);
assert.match(panel, /Voice and model safety boundary/);
assert.match(editor, /Guest Mode policy/);
assert.match(editor, /guest_shared_memory_read/);
assert.match(editor, /tool-guest-allowed/);
assert.match(editor, /group-guest-allowed/);
