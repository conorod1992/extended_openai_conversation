import assert from "node:assert/strict";

import {
  deleteTemporaryMemory,
  ensureTemporaryScope,
  ownerLabel,
  renderTemporaryMemories,
  temporaryScopeOptions,
  validOwnerScope,
} from "../custom_components/extended_openai_conversation_responses/frontend/management-temporary-memory.js";

const scopes = [
  {scope_id: "user:alice", scope_type: "user", display_name: "Alice", is_current_user: true},
  {scope_id: "shared:household", scope_type: "shared", display_name: "Shared household"},
  {scope_id: "__anonymous__", scope_type: "anonymous_legacy", display_name: "Legacy unowned"},
];
const panel = {
  _selectedAgent: () => ({temporary_memory: "off"}),
  _data: {is_admin: true, scopes},
  _scopeId: "__anonymous__",
  _query: "",
  _result: {
    memories: [{
      memory_id: "memory-1",
      content: "Parents are visiting tomorrow",
      category: "visitors",
      owner_scope_id: "user:alice",
      scope_id: "device:kitchen",
      expires_at: "2026-09-07T23:00:00+01:00",
    }],
    stats: {},
  },
  _e: (value) => String(value),
  _filtered: (items) => items,
  _formatDate: (value) => value,
  _empty: (value) => value,
};

assert.equal(validOwnerScope(scopes[0]), true);
assert.equal(validOwnerScope(scopes[1]), true);
assert.equal(validOwnerScope(scopes[2]), false);

ensureTemporaryScope(panel);
assert.equal(panel._scopeId, "user:alice");

const options = temporaryScopeOptions(panel);
assert.match(options, /user:alice/);
assert.match(options, /shared:household/);
assert.doesNotMatch(options, /__anonymous__/);
assert.equal(ownerLabel(panel, "user:alice"), "Alice");
assert.equal(ownerLabel(panel, "shared:household"), "Shared household");

const markup = renderTemporaryMemories(panel);
assert.match(markup, /Parents are visiting tomorrow/);
assert.match(markup, /Alice/);
assert.match(markup, /edit-temporary-memory/);
assert.match(markup, /delete-temporary/);
assert.match(markup, /class="status-value ">Off</);
assert.match(markup, /does not create new short-term memories/);
assert.match(markup, /remain until their expiry time, even if short-term memory is later turned off/);
assert.doesNotMatch(markup, /Expires automatically/);
for (const [mode, label, description] of [
  ["balanced", "Balanced", "clearly relevant"],
  ["eager", "Eager", "more readily"],
]) {
  panel._selectedAgent = () => ({temporary_memory: mode});
  const updated = renderTemporaryMemories(panel);
  assert.match(updated, new RegExp(`class="status-value on">${label}<`));
  assert.match(updated, new RegExp(description));
}
assert.doesNotMatch(markup, /Assist device/);
assert.doesNotMatch(markup, /data-scope=/);

for (const editor of [false, true]) {
  for (const response of [{deleted:0}, {deleted:1}, {deleted:"1"}]) {
    const record = {memory_id:"memory-1"};
    const toasts = [], changes = [];
    let closed = false;
    const owner = {scope:"user:alice"};
    const runtime = {
      _agentId:"agent",
      _retainedMutationOwner: () => owner,
      _ownsRetainedMutation: other => other === owner,
      _confirm: async () => true,
      _call: async (section, action, payload) => {
        assert.deepEqual([section, action, payload], ["memories", "temporary_delete", {scope_id:"user:alice", memory_id:"memory-1"}]);
        return response;
      },
      _toast: (...args) => toasts.push(args),
      _patchScopeCount: (...args) => changes.push(args),
      _render() {},
      _setSaving() {},
      _result:{memories:[record]},
      _temporaryMemoryDraft:editor ? {...record} : null,
      shadowRoot:{querySelector: () => ({close: () => { closed = true; }})},
    };
    const succeeded = response.deleted === 1;
    assert.equal(await deleteTemporaryMemory(runtime, record.memory_id), succeeded);
    assert.equal(runtime._result.memories.length, succeeded ? 0 : 1);
    assert.equal(changes.length, succeeded ? 1 : 0);
    assert.equal(closed, editor && succeeded);
    assert.equal(toasts.length, 1);
    assert.equal(toasts[0][1], !succeeded);
    if (response.deleted === 0) assert.match(toasts[0][0], /Refresh the list/);
    if (succeeded) assert.equal(toasts[0][0], "Temporary memory deleted");
  }
}
