# Backup & Restore

Open **Extended OpenAI → Usage & Maintenance → Backup & Restore** to create or restore a private backup for one conversation agent.

This is different from **Export configuration**. Configuration export is intended for copying agent behaviour and settings; a full backup also carries the agent's durable retained data.

## What a full backup includes

A full backup contains the selected agent's:

- normalized saved configuration
- Request Rules
- persistent memories
- active temporary memories with their original absolute expiry
- Knowledge Library source text and metadata
- retained conversation archive sessions and turns
- Guest Mode schedule
- persisted lifetime, daily, request and run usage data

## What it does not include

Provider API keys, OAuth tokens and parent config-entry credentials are excluded. Runtime-only state such as in-flight conversations, loaded on-demand Function Groups, caches and locks is also excluded.

When moving a backup to another Home Assistant installation, reconnect or recreate the provider connection separately.

## Restore behaviour

Selecting a backup validates its format and categories before any state is replaced. Temporary memories that have already expired are discarded during restore.

**Restore everything** uses replacement semantics rather than merging data into the current agent. Knowledge indexes are rebuilt from the restored canonical source content, and usage totals are replaced rather than added a second time.

Restore is designed to avoid a partially applied durable state. If a storage write fails after restoration begins, Extended OpenAI attempts to restore the pre-restore snapshot rather than deliberately leaving, for example, restored memories alongside old Knowledge or archive data.

While a full restore is applying, delayed Function Tool execution is serialized with the same maintenance boundary so a due action does not execute against a half-restored policy/configuration state.

## Security

Full backup files can contain prompts, memories, Knowledge sources, conversation history and detailed usage information. Treat them as private data even though provider credentials are excluded.
