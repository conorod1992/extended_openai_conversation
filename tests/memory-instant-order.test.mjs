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
