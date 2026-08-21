import assert from "node:assert/strict";

import { backupSummaryLines } from "../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor.js";

const lines = backupSummaryLines({
  persistent_memories: 24,
  temporary_memories: 3,
  knowledge_sources: 11,
  archive_sessions: 4,
  archive_turns: 19,
  usage_runs: 8,
  usage_requests: 13,
  guest_mode_scheduled: true,
});

assert.deepEqual(lines, [
  "Agent configuration",
  "24 persistent memories",
  "3 active temporary memories",
  "11 Knowledge sources",
  "4 archived conversations (19 turns)",
  "Usage history (8 runs, 13 requests)",
  "Guest Mode schedule included",
]);
