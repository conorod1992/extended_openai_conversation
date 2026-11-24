export const BACKUP_CREDENTIAL_WARNING = "New private backups preserve credentials exactly and are not encrypted. Keep them private. Older redacted backups may require credential re-entry. Restoring a shared provider connection requires a separate explicit choice.";

export function unassignedTemporaryMemoryNotice(summary = {}) {
  const count = Number(summary.unassigned_legacy_temporary_memories || 0);
  return count > 0
    ? `${count} legacy temporary ${count === 1 ? "memory has" : "memories have"} no verified owner. After restore, they remain isolated until their original expiry. Assistants cannot access them.`
    : "";
}

export function baseBackupSummaryLines(summary = {}) {
  const unassigned = unassignedTemporaryMemoryNotice(summary);
  return [
    "Agent configuration",
    `${Number(summary.request_rules || 0)} Request Rules`,
    `${Number(summary.persistent_memories || 0)} persistent memories`,
    `${Number(summary.temporary_memories || 0)} active temporary memories`,
    ...(unassigned ? [unassigned] : []),
    `${Number(summary.knowledge_sources || 0)} Knowledge sources`,
    `${Number(summary.archive_sessions || 0)} archived conversations (${Number(summary.archive_turns || 0)} turns)`,
    `Usage history (${Number(summary.usage_runs || 0)} runs, ${Number(summary.usage_requests || 0)} requests)`,
    `Guest Mode schedule ${summary.guest_mode_scheduled ? "included" : "inactive"}`,
  ];
}

export function backupSummaryLines(summary) {
  if (summary?.security) {
    const {omitted = [], review_required: reviewRequired} = summary.security;
    const count = omitted.reduce((total, item) => total + (Number.isSafeInteger(item.count) && item.count > 0 ? item.count : 0), 0);
    return [
      ...baseBackupSummaryLines(summary),
      "This is an older sharing export.",
      `${count} configuration items or custom units omitted.`,
      ...(reviewRequired ? ["This file can include custom content. Review it before restoring or sharing it again."] : []),
    ];
  }
  return [...baseBackupSummaryLines(summary), BACKUP_CREDENTIAL_WARNING];
}
