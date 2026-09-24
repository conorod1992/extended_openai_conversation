import assert from "node:assert/strict";

import { BACKUP_CREDENTIAL_WARNING, backupSummaryLines } from "../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor.js";
import {backupSummaryLines as transferSummaryLines} from "../custom_components/extended_openai_conversation_responses/frontend/backup-transfer-ui.js";
import {backupSummaryLines as baseSummaryLines} from "../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor-base.js";

const summary = {
  request_rules: 5,
  persistent_memories: 24,
  temporary_memories: 3,
  knowledge_sources: 11,
  archive_sessions: 4,
  archive_turns: 19,
  usage_runs: 8,
  usage_requests: 13,
  guest_mode_scheduled: true,
};
const lines = backupSummaryLines(summary);

assert.deepEqual(lines, [
  "Agent configuration",
  "5 Request Rules",
  "24 persistent memories",
  "3 active temporary memories",
  "11 Knowledge sources",
  "4 archived conversations (19 turns)",
  "Usage history (8 runs, 13 requests)",
  "Guest Mode schedule included",
  BACKUP_CREDENTIAL_WARNING,
]);
assert.match(BACKUP_CREDENTIAL_WARNING, /redacted/i);
assert.match(BACKUP_CREDENTIAL_WARNING, /re-enter/i);
assert.match(BACKUP_CREDENTIAL_WARNING, /best-effort/i);
assert.deepEqual(transferSummaryLines(summary), lines);
assert.deepEqual(baseSummaryLines(summary), lines.slice(0, -1));
