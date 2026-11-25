# Backup & Restore

Open **Extended OpenAI → Usage & Maintenance → Backup & Restore** to create or restore a private backup for one conversation agent.

Choose an export type according to what you need:

| Export type | Use it for | Important limits |
| --- | --- | --- |
| **Full Backup (private)** | Recovering or moving the selected agent's configuration and retained data | Preserves credentials and private content; the file is not encrypted. |
| **Custom (private)** | Backing up only selected sections | Preserves the selected private sections, including provider credentials when configuration is selected; the file is not encrypted. |

## Create an export

1. Select the conversation agent you want to back up.
2. Open **Usage & Maintenance → Backup & Restore** and choose **Export type**.
3. For **Custom (private)**, select the sections to include.
4. Choose **Create export** and save the downloaded file securely.

Exports use saved configuration. Save any draft settings first if you want them included.

Private ZIP backups are limited to 160 MiB compressed and 320 MiB uncompressed. Legacy plain JSON imports are limited to 128 MiB. If a Full Backup exceeds a limit, export smaller groups of sections with Custom.

Backup inspection and Configuration import reject malformed Unicode in JSON and YAML fields. Valid Unicode, including a matched high and low surrogate written as escapes, is accepted.

To move a custom setup privately, choose **Custom (private)** and select **Agent configuration and Function Tools** and **Request Rules**. The configuration section includes provider credentials. Existing Shareable Setup and configuration export JSON files still work with **Import / Restore**, including older reviewed custom files. Configuration export files also work with **Assistant actions → Import configuration**.

## What a full backup includes

A full backup contains the selected agent's:

- saved configuration, including Function Tools, Function Groups and selected Skill names
- Request Rules, including their groups, global priority order, matching settings and wording alternatives
- persistent memories
- active temporary memories with their original expiry time
- Knowledge Library source text and metadata
- retained conversation archive sessions and turns
- Guest Mode schedule
- saved lifetime and daily usage totals, plus request and run details within their retention periods
- the parent provider connection and credentials

## What it does not include

Installed Skill files, external Home Assistant scripts/files/services, integration-wide Quiet Hours and Broadcast settings, and the pending delayed Function Tool queue are not included. Reinstall or copy Skills and preserve external dependencies separately when moving to another Home Assistant installation. Use an appropriate Home Assistant backup when you need to preserve the wider installation.

Temporary runtime state such as requests currently in progress, Function Groups loaded only for the active conversation, caches and locks is excluded. Historical backup versions excluded parent credentials and redacted recognized inline secrets; their import semantics remain unchanged.

Provider restoration requires an explicit shared-connection choice because other agents may use that connection. It runs after agent restoration and refuses to overwrite concurrent connection edits. External HA scripts/files/services and expired credentials may still require attention. See [credential security](credential-security.md) for scope, format versions and limitations.

## What happens when you restore

1. Select the destination conversation agent and choose **Choose file** under **Import / Restore**. The panel accepts Extended OpenAI JSON exports and ZIP backups.
2. Review the validated summary and select **Sections to replace**. Only sections present in the file are offered. If Usage is selected, the preview shows the request and run counts after the effective retention policy is applied and how many details will expire. If restoring Configuration shortens retention while Usage is unselected, the preview shows how many destination details will expire. Retention preserves daily and lifetime Usage totals.
   If Conversation archive is selected, the preview shows how many imported conversations and turns will remain after retention and warns about immediate expiry. If it is unselected, Configuration restore preserves the destination's retention period when the incoming period would shorten it.
3. If the backup contains personal data for users missing from this installation, map each source user to a destination Home Assistant user.
4. Choose whether to **Also restore the provider connection**, if the file contains one. This affects all agents sharing that connection.
5. Choose **Restore selected sections** and confirm the replacement.

Validation checks the combined destination configuration, including Request Rule references to Function Tools. An incompatible combination of selected and unselected sections must be resolved before restore. Temporary memories that have already expired are discarded. If configuration is selected, restoring it replaces any unsaved configuration draft for the destination agent.

Every selected section replaces the agent's corresponding saved data rather than merging the backup with what is already there. Private Request Rules are restored with their saved global order and groups. Some legacy Shareable Setup files contain an empty Request Rules section. Deselect it to keep the destination's rules. Knowledge search indexes are rebuilt from the restored source text, and usage totals are replaced rather than added again.

Restore is designed to avoid leaving only half of the backup applied. If saving the restored data fails part-way through, Extended OpenAI attempts to return the agent to the state it had before the restore began.

Delayed Function Tools are paused behind the same restore operation, so a due action is not deliberately run while the agent's configuration or restrictions are only partly restored.

Before restoring a shared provider connection, Extended OpenAI checks every assistant that uses it and their enabled AI routing rules. When the replacement connection introduces a function-tool limit, the check reserves space for eligible saved Home Assistant tool references even if the current user cannot resolve them. If any assistant is incompatible, restore stops before applying changes.

The shared provider connection is updated separately after the agent restore. Extended OpenAI verifies the connection on disk before reporting it as restored. If verification fails, check the shared connection before retrying. The completed agent restore stays committed.

## Interrupted restore or uncertain result

The integration keeps a private local recovery record for configuration and the data stores the restore can change. If Home Assistant stops during the operation, startup uses the saved decision to recover the previous state or finish the committed restore. If recovery cannot finish, the affected assistant remains blocked rather than processing requests with partly restored state.

If the panel reports **Restore recovery is pending** or that completion is pending, restart Home Assistant as directed and resolve any reported storage problem before trying another restore. Keep the recovery files intact. If the browser loses the result and reports **Restore outcome is unknown**, refresh the agent's saved state before deciding whether another restore is needed; losing the response does not prove that nothing changed.

## Damaged saved data

The integration's diagnostic download includes a content-free storage recovery report and guidance for cases without a usable journal. See [Retained data or settings cannot be loaded](../troubleshooting.md#retained-data-or-settings-cannot-be-loaded). File presence is not proof of valid contents; the report neither reconstructs records nor changes recovery authority.

Persistent Memory, Knowledge and Request Rules preserve malformed, duplicate and over-limit records in their original store. Valid records remain readable where loading permits it, but edits and full backups are rejected when they would discard the damaged material. Harmless normalization of valid legacy records continues. Memory content is not classified for sensitivity: structurally valid credentials and personal facts are preserved, including beside damaged records.

The error reports that storage needs repair. Keep the original store private; an administrator can repair it with Home Assistant stopped, or restore a validated backup into a clean assistant. Ordinary restore into a damaged destination can also be blocked because it cannot capture a complete rollback snapshot. An existing validated restore journal can still replace a readable damaged collection; its write must succeed before that collection accepts ordinary edits again. Initialization-blocked recovery is limited to validated existing journals, as described below.

Temporary Memory uses the same record metadata checks for loading, failed-write readback and backup validation. Invalid IDs, duplicate IDs, source markers or creation/update timestamps block loading before cleanup can overwrite the damaged generation. Duplicate IDs are rejected even when the records have different owners or one has expired. Valid expiry and owner cleanup still apply normally.

Other malformed Temporary Memory records also block initialization and failed-write readback. Startup preserves the complete raw generation for repair, including any structurally valid credential-bearing siblings. The store remains unavailable until repaired; valid expiry and owner cleanup resume only after the generation validates.

Archive and Usage retain usable records for reads when malformed containers, records or duplicate identifiers are detected. Archive preserves the original metadata and partitions while removing content bound to validated private sessions. When only Usage details need repair, healthy lifetime and daily totals continue to be recorded. If aggregate data needs repair, the Usage page warns that the totals can be incomplete. The original damaged data remains available for repair.

A complete backup requires intact Usage data. To back up the other sections, choose **Custom backup** and leave out **Usage**. An in-place restore can leave damaged Usage untouched when it excludes Usage and keeps the current Usage retention settings. Replacing Usage or changing its retention still requires an intact rollback snapshot. Clearing details is blocked during repair to preserve the original records.

Before replaying its local partition journal, Archive validates the complete candidate, including unchanged partitions, unique turn IDs, session associations, turn counts and active bindings. Invalid intent or an unreadable unchanged partition leaves the journal and destination files untouched. A partition fully supplied by valid journal intent can be replaced even when its existing file is unreadable. This partition-journal protocol is distinct from the full-agent journal recovery described below.

Full-agent journal verification reads the actual file rather than a pending HA save or shared load cache. An explicit journal save during shutdown finishes its write before verification; deferred RAM data cannot serve as commit evidence.

On POSIX, retiring a full-agent journal also syncs its containing directory. Absence checks repeat that sync before releasing recovery quarantine, so a persistent sync failure cannot be mistaken for durable deletion. A later successful absence check can release quarantine without replaying a completed transaction. Windows retains its existing deletion guarantees; this directory-fsync path is POSIX-specific.

An existing validated full-agent journal can now recover Temporary Memory and Knowledge when malformed destination data prevents normal initialization. Recovery uses the journal's durable decision (rollback while applying, target after commit), replaces only a collection whose normal getter rejects its data, then retries the production loaders and the normal full apply. Failed writes retain the journal and quarantine for retry. This path requires exclusive recovery ownership and does not allow a new restore to skip its rollback snapshot. Corrupted Archive transaction intent can also be replaced from the selected full-agent snapshot: recovery stages a fresh validated Archive intent, preserves local active bindings, and empties old indexed months omitted by the selected snapshot before rebuilding the normal manager. Storage I/O failures still fail closed and retain quarantine for retry.

If there is no existing recovery journal and the original assistant cannot provide a complete rollback snapshot, create a clean assistant and restore the external backup there. The normal restore transaction then has a valid empty destination snapshot, while the original assistant's damaged files remain available for repair. Direct overwrite of an arbitrary damaged destination is not part of the current restore API.

## Interrupted transfer files

Transfers are not resumable across Home Assistant process restarts. New upload and export payloads live in an EOAI-private staging directory under `.storage`. Native owner locks protect active processes; startup removes abandoned owners after their locks have been released. Recovery neither recreates transfer sessions nor applies an abandoned import. Normal cancellation, expiry and shutdown still remove payloads.

Files created by older versions in the system temporary directory have no reliable owner metadata. They are left untouched rather than deleted by a global temporary-file scan. Inspect any known historical residue manually; a filename alone does not establish that another process has stopped using it.

## Security

Full and custom private backup files preserve credentials, prompts, memories, Knowledge sources, conversation history and usage information. They are not encrypted. Keep them private. See [credential handling and recovery](credential-security.md).


Selective restores preserve active Archive privacy bindings when Archive is not
selected. The private local restore journal includes those same-agent bindings
for rollback and interrupted-transaction recovery. Portable exports omit them;
explicitly replacing Archive with imported data still starts without imported
active conversation bindings. A failed restore does not resume saving a private
conversation when its prior state is recovered.

When a committed restore resets live conversation history, that reset also applies
to Home Assistant's live context for the affected assistant. Active private
conversations keep their privacy bindings. A Knowledge-only or Usage-only restore
can keep verified, retained live conversations.

Local recovery snapshots also preserve content-free active unretained sessions. These sessions are excluded from portable backups and durable Archive metadata, and recovery validation rejects any turns attached to them.
