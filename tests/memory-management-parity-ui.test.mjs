import assert from "node:assert/strict";
import {readFileSync} from "node:fs";

const panel = readFileSync(
  new URL(
    "../custom_components/extended_openai_conversation_responses/frontend/memory-management-panel.js",
    import.meta.url,
  ),
  "utf8",
);
const backend = readFileSync(
  new URL(
    "../custom_components/extended_openai_conversation_responses/memory_ui.py",
    import.meta.url,
  ),
  "utf8",
);

assert.match(panel, /extends BaseMemoryPanel/);
assert.match(panel, /result\.next_offset/);
assert.match(panel, /expected_revision: this\._editingMemory\.revision/);
assert.match(panel, /refresh_confirmation: false/);
assert.match(panel, /payload\.clear_fields = clearFields/);
assert.match(panel, /Memory content cannot be blank/);
assert.match(panel, /meta\.textContent = message/);
assert.match(panel, /if \(!confirmed\) return;/);
assert.match(panel, /memory\.scope === "Shared household"/);
assert.match(panel, /Search, category, and importance filters do not limit this action/);
assert.match(panel, /household\.disabled = !sharedEnabled/);
assert.doesNotMatch(panel, /original_scope/);

assert.match(backend, /expected_revision/);
assert.match(backend, /clear_fields/);
assert.match(backend, /_async_memory_owner/);
assert.match(backend, /"next_offset"/);
assert.match(backend, /extended-openai-memory-management-panel/);
