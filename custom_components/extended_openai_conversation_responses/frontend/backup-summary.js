export const BACKUP_CREDENTIAL_WARNING = "Recognised API keys, tokens, passwords, authorization headers and other common secrets are redacted from full backups. Re-enter any required credentials after restore. Redaction is best-effort, so review backup files before sharing them.";

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
  return [...baseBackupSummaryLines(summary), BACKUP_CREDENTIAL_WARNING];
}
