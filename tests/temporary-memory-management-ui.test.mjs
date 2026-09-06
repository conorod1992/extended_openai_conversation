import assert from "node:assert/strict";

import {
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
assert.match(markup, /remain manageable until they expire even when Temporary Memory is turned off/);
assert.doesNotMatch(markup, /Assist device/);
assert.doesNotMatch(markup, /data-scope=/);
