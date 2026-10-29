# Backup & Restore

Open **Extended OpenAI → Usage & Maintenance → Backup & Restore** to create or restore a private backup for one conversation agent.

This is different from **Export configuration**. Configuration export is mainly for copying agent behaviour and settings; a full backup also carries the agent's saved memories, Knowledge, conversation history and other retained data.

## What a full backup includes

A full backup contains the selected agent's:

- saved configuration
- Request Rules, including their groups, global priority order, matching settings and wording alternatives
- persistent memories
- active temporary memories with their original expiry time
- Knowledge Library source text and metadata
- retained conversation archive sessions and turns
- Guest Mode schedule
- saved lifetime, daily, request and run usage data

## What it does not include

New private backups preserve inline credentials and include the parent provider connection when configuration is selected. Historical backup versions excluded parent credentials and redacted recognized inline secrets; their import semantics remain unchanged. Temporary runtime state such as requests currently in progress, Function Groups loaded only for the active conversation, caches and locks is excluded.

Provider restoration requires an explicit shared-connection choice because other agents may use that connection. It runs after agent restoration and refuses to overwrite concurrent connection edits. External HA scripts/files/services and expired credentials may still require attention. See [credential security](credential-security.md) for scope, format versions and limitations.

## What happens when you restore

Selecting a backup validates it before anything is replaced. Temporary memories that have already expired are discarded during restore.

**Restore everything** replaces the agent's corresponding saved data rather than merging the backup with what is already there. Request Rules are therefore restored as the saved rule set, including their exact global order and groups. This is different from **Rule Sharing**, which appends shared rules after existing rules in their shared relative order and does not import agent-wide wording alternatives. Knowledge search indexes are rebuilt from the restored source text, and usage totals are replaced rather than added again.

Restore is designed to avoid leaving only half of the backup applied. If saving the restored data fails part-way through, Extended OpenAI attempts to return the agent to the state it had before the restore began.

Delayed Function Tools are paused behind the same restore operation, so a due action is not deliberately run while the agent's configuration or restrictions are only partly restored.

## Damaged saved data

Persistent Memory, Knowledge and Request Rules preserve malformed, duplicate and over-limit records in their original store. Valid records remain readable where loading permits it, but edits and full backups are rejected when they would discard the damaged material. Harmless normalization of valid legacy records continues. Persistent Memory still removes records rejected by its privacy policy while preserving unrelated damage.

The error reports that storage needs repair. Keep the original store private; an administrator can repair it with Home Assistant stopped, or restore a validated backup into a clean assistant. Ordinary restore into a damaged destination can also be blocked because it cannot capture a complete rollback snapshot. An existing validated restore journal can still replace a readable damaged collection; its write must succeed before that collection accepts ordinary edits again. Recovery when normal initialization fails is not yet supported by this safeguard.

Temporary Memory uses the same record metadata checks for loading, failed-write readback and backup validation. Invalid IDs, duplicate IDs, source markers or creation/update timestamps block loading before cleanup can overwrite the damaged generation. Duplicate IDs are rejected even when the records have different owners or one has expired. Valid expiry and owner cleanup still apply normally.

Other malformed Temporary Memory records also block initialization and failed-write readback. If a separate record is rejected by the privacy policy, startup removes only that record and leaves the remaining raw generation intact for repair. The store remains unavailable until repaired; a failed privacy-purge write is reported as a failure.

Archive and Usage retain usable records for reads when malformed containers, records or duplicate identifiers are detected, but block ordinary persistence and complete backups that would omit the damaged material. Archive preserves the original metadata and partitions, while still removing content bound to validated private sessions. Usage reports that storage needs repair and that new accounting does not survive restart; routine telemetry continues in memory without scheduling writes to the damaged stores. Explicit detail clearing and retention writes are rejected, and shutdown still retires resources without flushing over the evidence. Validated complete replacements can clear these barriers after persistence succeeds. Missing legacy Usage counters still receive their normal defaults; malformed supplied counters require repair.

Before replaying its local partition journal, Archive validates the complete candidate, including unchanged partitions, unique turn IDs, session associations, turn counts and active bindings. Invalid intent or an unreadable unchanged partition leaves the journal and destination files untouched. A partition fully supplied by valid journal intent can be replaced even when its existing file is unreadable. This does not yet provide initialization-independent recovery for the full-agent restore journal.

Full-agent journal verification reads the actual file rather than a pending HA save or shared load cache. An explicit journal save during shutdown finishes its write before verification; deferred RAM data cannot serve as commit evidence.

An existing validated full-agent journal can now recover Temporary Memory and Knowledge when malformed destination data prevents normal initialization. Recovery uses the journal's durable decision (rollback while applying, target after commit), replaces only a collection whose normal getter rejects its data, then retries the production loaders and the normal full apply. Failed writes retain the journal and quarantine for retry. This path requires exclusive recovery ownership and does not allow a new restore to skip its rollback snapshot. Other initialization blockers, including corrupted Archive transaction intent and I/O failures, still fail closed.

## Interrupted transfer files

Transfers are not resumable across Home Assistant process restarts. New upload and export payloads live in an EOAI-private staging directory under `.storage`. Native owner locks protect active processes; startup removes abandoned owners after their locks have been released. Recovery neither recreates transfer sessions nor applies an abandoned import. Normal cancellation, expiry and shutdown still remove payloads.

Files created by older versions in the system temporary directory have no reliable owner metadata. They are left untouched rather than deleted by a global temporary-file scan. Inspect any known historical residue manually; a filename alone does not establish that another process has stopped using it.

## Security

Full and custom private backup files preserve credentials, prompts, memories, Knowledge sources, conversation history and usage information. They are not encrypted. Keep them private. Use Shareable Setup for conservative public sharing and review any custom content you explicitly include. See the [sharing security contract](credential-security.md).


Selective restores preserve active Archive privacy bindings when Archive is not
selected. The private local restore journal includes those same-agent bindings
for rollback and interrupted-transaction recovery. Portable exports omit them;
explicitly replacing Archive with imported data still starts without imported
active conversation bindings. A failed restore does not resume saving a private
conversation when its prior state is recovered.

Local recovery snapshots also preserve content-free active unretained sessions. These sessions are excluded from portable backups and durable Archive metadata, and recovery validation rejects any turns attached to them.
