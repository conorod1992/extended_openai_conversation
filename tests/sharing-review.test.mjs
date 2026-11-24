import assert from "node:assert/strict";
import {BACKUP_CREDENTIAL_WARNING, backupSummaryLines} from "../custom_components/extended_openai_conversation_responses/frontend/backup-summary.js";

const restricted = backupSummaryLines({security: {omitted: [{count: 2}, {count: -1}]}});
assert.ok(restricted.includes("This is an older sharing export."));
assert.ok(restricted.includes("2 configuration items or custom units omitted."));
assert.ok(!restricted.includes(BACKUP_CREDENTIAL_WARNING));

const reviewed = backupSummaryLines({security: {review_required: true}});
assert.ok(reviewed.some(line => line.includes("can include custom content")));
assert.ok(backupSummaryLines({}).includes(BACKUP_CREDENTIAL_WARNING));
