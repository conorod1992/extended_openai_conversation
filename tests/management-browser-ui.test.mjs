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

// Search membership is authoritative after mutations, including Unicode casefold.
{
  const memory = {memory_id:"unicode", content:"Sonnenstraße", category:"place", source:"user"};
  const searched = {...panel, _query:"strasse", _result:{memories:[memory], total:1},
    shadowRoot:{querySelector:() => null}, _render() {}, _toast() {},
    _call:async () => ({memories:[{...memory, category:"home"}], total:1})};
  await ui.finishMemoryBrowserLoad(searched);
  await ui.applyPersistentMemoryMutation(searched, {scope_id:searched._scopeId, memory:{...memory, category:"home"}});
  assert.equal(searched._result.total, 1);
  assert.equal(searched._result.memories[0].memory_id, "unicode");
  assert.match(ui.renderPersistentMemories(searched), /data-memory-id="unicode" >/);
}

// In Turkish, distinct backend searches can have the same local projection.
{
  const requests = [];
  const upper = {memory_id:"upper", content:"IŞIK"};
  const dotless = {memory_id:"dotless", content:"ışık"};
  const searched = {...panel, _query:" IŞIK ", _result:{memories:[upper, dotless]},
    shadowRoot:{querySelector:() => null}, _toast(message) { throw new Error(message); },
    _call:async (_section, action, payload) => {
      assert.equal(action, "search");
      requests.push(payload.query);
      return {memories:[payload.query === "IŞIK" ? upper : dotless]};
    }};
  const lower = String.prototype.toLocaleLowerCase;
  String.prototype.toLocaleLowerCase = function () { return lower.call(this, "tr"); };
  try {
    ui.prepareMemoryBrowser(searched);
    await ui.finishMemoryBrowserLoad(searched);
    assert.equal(ui.memorySearchProjection(upper), ui.memorySearchProjection(dotless));
    searched._query = "ışık";
    ui.filterPersistentMemories(searched);
    await new Promise(resolve => setTimeout(resolve, 300));
    assert.deepEqual(requests, ["IŞIK", "ışık"]);
    const html = ui.renderPersistentMemories(searched);
    assert.match(html, /data-memory-id="upper" hidden/);
    assert.match(html, /data-memory-id="dotless" >/);
  } finally {
    String.prototype.toLocaleLowerCase = lower;
  }
}
