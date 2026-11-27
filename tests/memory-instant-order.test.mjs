import assert from "node:assert/strict";
import {applyPersistentMemoryMutation} from "../custom_components/extended_openai_conversation_responses/frontend/guest-mode-ui.js";

// Non-UTC browser environment must not reinterpret legacy timestamps locally.
process.env.TZ = "America/Los_Angeles";
const stamps = ["2026-10-25T02:10:00+02:00", "2026-10-25T00:20:00Z", "2026-10-25T01:15:00+01:00", "2026-10-25T00:15:00", "2026-10-24T19:15:00-05:00", "2026-10-25T00:20:00.000001+00:00"];
const memories = stamps.map((updated_at, i) => ({memory_id: String(i), updated_at, content: "Tea", category: "home"}));
const panel = {_scopeId: "user:alice", _agentId: "agent", _memoryKind: "persistent", _query: "", _result: {memories: [memories[5], memories[1], memories[2], memories[3], memories[4], memories[0]], total:6}};
for (const i of [2, 1, 3, 5, 4, 0]) {
  assert.equal(await applyPersistentMemoryMutation(panel, {status:"updated", scope_id: "user:alice", memory: {...memories[i]}}), true);
  assert.deepEqual(panel._result.memories.map(row => row.memory_id), ["5", "1", "2", "3", "4", "0"]);
  assert.equal(panel._result.total, 6);
}


const loaded = Array.from({length:150}, (_, i) => ({
  memory_id: `record-${i}`, updated_at:"2026-10-25T00:20:00Z", content:"Tea", category:"home",
}));
panel._result = {memories:loaded, has_more:false, limit:100, total:150};
await applyPersistentMemoryMutation(panel, {scope_id:panel._scopeId, memory:{...loaded[140], category:"kitchen"}});
assert.equal(panel._result.has_more, false);
assert.equal(panel._result.memories.length, 150);
await applyPersistentMemoryMutation(panel, {scope_id:panel._scopeId, memory:{...loaded[0], memory_id:"added", updated_at:"2026-10-26T00:20:00Z"}});
assert.equal(panel._result.memories.length, 151);
assert.equal(panel._result.memories[0].memory_id, "added");
assert.equal(panel._result.total, 151);
assert.equal(panel._result.has_more, false);

const {formatManagementTimestamp, browserState} = await import("../custom_components/extended_openai_conversation_responses/frontend/management-data-state.js");
assert.equal(formatManagementTimestamp("2026-10-25 00:20:00", "Europe/Dublin"), formatManagementTimestamp(stamps[1], "Europe/Dublin"));
assert.equal(formatManagementTimestamp("2026-10-25T01:20:00+01", "Europe/Dublin"), formatManagementTimestamp(stamps[1], "Europe/Dublin"));

const offsets = [];
panel._viewKey = () => "data-memory/memories";
panel.shadowRoot = {querySelector: () => null};
panel._query = browserState(panel).memoryQuery = "Tea";
panel._call = async (section, action, {offset}) => {
  assert.deepEqual([section, action], ["memories", "search"]);
  offsets.push(offset);
  return {memories:loaded.slice(offset, offset + 100), total:loaded.length, has_more:offset + 100 < loaded.length};
};
await applyPersistentMemoryMutation(panel, {scope_id:panel._scopeId, memory:{...loaded[140], category:"kitchen"}});
assert.deepEqual(offsets, [0, 100]);
assert.equal(panel._result.memories.length, 150);
assert.equal(panel._result.has_more, false);


// The rendered identifier survives keyed adoption and every delegated action.
const {bindMemoryBrowser, renderPersistentMemories} = await import("../custom_components/extended_openai_conversation_responses/frontend/guest-mode-ui.js");
const legacyId = "legacy\r\0%0D";
const encodedId = "legacy%0D%00%250D";
const empty = {};
const card = {dataset:{memoryId:encodedId}, nextElementSibling:empty};
const list = {
  children:[card, empty], firstElementChild:card, querySelectorAll:() => [card],
  querySelector:() => empty,
};
const controls = new Map([[".memory-list", list], ["#list-search", {addEventListener() {}}]]);
const listeners = new Map();
const host = {
  isConnected:true, contains:() => true,
  addEventListener(type, listener) { listeners.set(type, listener); },
  querySelector(selector) {
    if (!controls.has(selector)) controls.set(selector, {});
    return controls.get(selector);
  },
};
const actions = [];
const legacyPanel = {
  _scopeId:"__anonymous__", _data:{is_admin:true}, _query:"", _memoryKind:"persistent",
  _result:{memories:[{...loaded[0], memory_id:legacyId}]},
  _viewKey:() => "data-memory/memories",
  _e:String, _formatDate:String, _empty:String,
  _openMemory:id => actions.push(["edit", id]),
  _deleteMemory:id => actions.push(["delete", id]),
  _openReassign:id => actions.push(["assign", id]),
  shadowRoot:{querySelector:sel => sel === "[data-persistent-memories]" ? host : null},
};
const markup = renderPersistentMemories(legacyPanel);
assert.ok(markup.includes(`data-memory-id="${encodedId}"`));
bindMemoryBrowser(legacyPanel);
for (const choice of [".memory-edit-button", ".delete-memory", ".reassign-memory"]) {
  const control = {matches:selector => selector === choice, dataset:{id:encodedId}};
  listeners.get("click")({type:"click", target:{closest:() => control}});
}
assert.deepEqual(actions, [["edit", legacyId], ["delete", legacyId], ["assign", legacyId]]);
