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

Provider API keys, OAuth tokens and parent config-entry credentials are excluded. Temporary runtime state such as requests currently in progress, Function Groups loaded only for the active conversation, caches and locks is also excluded.

When moving a backup to another Home Assistant installation, reconnect or recreate the provider connection separately.

## What happens when you restore

Selecting a backup validates it before anything is replaced. Temporary memories that have already expired are discarded during restore.

**Restore everything** replaces the agent's corresponding saved data rather than merging the backup with what is already there. Request Rules are therefore restored as the saved rule set, including their exact global order and groups. This is different from **Rule Sharing**, which appends shared rules after existing rules in their shared relative order and does not import agent-wide wording alternatives. Knowledge search indexes are rebuilt from the restored source text, and usage totals are replaced rather than added again.

Restore is designed to avoid leaving only half of the backup applied. If saving the restored data fails part-way through, Extended OpenAI attempts to return the agent to the state it had before the restore began.

Delayed Function Tools are paused behind the same restore operation, so a due action is not deliberately run while the agent's configuration or restrictions are only partly restored.

## Security

Full backup files can contain prompts, memories, Knowledge sources, conversation history and detailed usage information. Treat them as private data even though provider credentials are excluded.
