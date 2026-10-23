export const BACKUP_CREDENTIAL_WARNING = "New private backups preserve credentials exactly and are not encrypted. Keep them private. Older redacted backups may require credential re-entry. Restoring a shared provider connection requires a separate explicit choice.";

export function baseBackupSummaryLines(summary = {}) {
  return [
    "Agent configuration",
    `${Number(summary.request_rules || 0)} Request Rules`,
    `${Number(summary.persistent_memories || 0)} persistent memories`,
    `${Number(summary.temporary_memories || 0)} active temporary memories`,
    `${Number(summary.knowledge_sources || 0)} Knowledge sources`,
    `${Number(summary.archive_sessions || 0)} archived conversations (${Number(summary.archive_turns || 0)} turns)`,
    `Usage history (${Number(summary.usage_runs || 0)} runs, ${Number(summary.usage_requests || 0)} requests)`,
    `Guest Mode schedule ${summary.guest_mode_scheduled ? "included" : "inactive"}`,
  ];
}

export function backupSummaryLines(summary) {
  if (summary?.security) return [...baseBackupSummaryLines(summary), SHARING_CONTRACT, sharingSummary(summary.security)];
  return [...baseBackupSummaryLines(summary), BACKUP_CREDENTIAL_WARNING];
}
import {SHARING_CONTRACT, sharingSummary} from "./sharing-review.js";

