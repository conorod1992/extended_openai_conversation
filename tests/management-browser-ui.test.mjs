import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const ui = await import("../custom_components/extended_openai_conversation_responses/frontend/guest-mode-ui.js");

assert.equal(ui.memorySearchProjection({content:"Likes Tea", category:"Preference", source:"Explicit"}), "likes tea preference explicit");
assert.equal(ui.formatManagementTimestamp("not-a-date"), "not-a-date");
assert.equal(ui.formatManagementTimestamp(null), "Unknown date");

const panel = {
  _query: "", _memoryKind: "persistent", _scopeId: "user:alice",
  _viewKey: () => "data-memory/memories", _e: String, _empty: String,
  _formatDate: ui.formatManagementTimestamp,
  _result: {memories: [{memory_id: "one", content: "Likes tea", category: "drink", source: "user"}], has_more: true},
};
ui.prepareMemoryBrowser(panel);
await ui.finishMemoryBrowserLoad(panel);
assert.match(ui.renderPersistentMemories(panel), /Likes tea/);
assert.match(ui.renderPersistentMemories(panel), /load-more-memories/);
panel._query = "coffee";
// Filtered cards stay present, hidden, until an authoritative collection refresh.
assert.match(ui.renderPersistentMemories(panel), /data-memory-id="one" hidden/);
assert.match(ui.renderPersistentMemories(panel), /Likes tea/);
assert.match(ui.renderPersistentMemories(panel), /No memories match this search\./);

const source = await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/guest-mode-ui.js", import.meta.url), "utf8");
assert.match(source, /MEMORY_SEARCH_DEBOUNCE_MS = 250/);
assert.match(source, /id="load-more-memories"/);
assert.doesNotMatch(source, /load-more-conversations|decorateConversations/);
assert.match(source, /memories", action/);
