# Migrating from the original integration

Extended OpenAI Conversation (Responses) began as a fork of `jekalmin/extended_openai_conversation`, but Home Assistant treats the two projects as separate integrations.

## Upgrading this fork to 7.0.0

These steps apply to an existing installation of **Extended OpenAI Conversation (Responses)**, rather than migration from the original project:

1. Check that Home Assistant is **2026.7.0 or newer** and take a complete Home Assistant backup. A private agent backup is useful for restoring selected agent data, but does not capture external scripts, workspace files, installed Skills or the integration installation.
2. Update through HACS, or replace the complete integration folder for a manual installation, then restart Home Assistant. Reload the browser if the management panel still shows the old interface.
3. Open **Extended OpenAI** in the sidebar and select each conversation agent. The management panel has reorganised sections: memory settings are under **Data & Memory**, and **Web search & Skills** is under **Capabilities**. The native Home Assistant screen retains agent lifecycle controls and links to the panel. AI Task keeps its separate native options flow.
4. Review **Assistant → Basics**, **Capabilities**, and **Data & Memory**. Saved models and custom prompts are preserved. Exact historical stock prompts, including the unchanged 6.8.3 default, adopt the current default. Agents predating the separate date/time and exposed-device controls keep those blocks off when migrated; existing saved choices are retained. Check your prompt before changing those controls to avoid duplicate context.
5. Run the connection test in **Usage & Maintenance → Diagnostics**, then test representative Assist requests, local Request Rules, Function Tools, Guest Mode and any automations that use this integration.

Settings pages share a draft and require **Save**. Function Tools, Function Groups, Knowledge sources and other collections have their own save controls. Review imported Request Rules before enabling them: imports start disabled and may refer to tools or Home Assistant entities that do not exist on the destination.

Compatibility repairs also update exact historical stock native Function Tool schemas. Custom definitions are validated rather than silently rewritten. A saved `gpt-5-mini` configuration using the historical `flex` tier default is corrected to the normal `default` tier when Flex is unsupported; the saved model is not substituted.

Live conversation context, loaded Function Groups and conversation-scoped Request Rule routing are not restored after a restart. Durable data such as memories, Knowledge sources and optional archived conversations is separate. Pending delayed Function Tools are recovered where eligible and are checked against current permissions; work that may already have started is not automatically replayed. See [Delayed Function Tools](features/delayed-function-tools.md) and [Backup & Restore](features/backup-restore.md).

If recovery reports damaged or ambiguous storage, follow the reported repair instructions and preserve the affected files. Do not assume a blank collection means its previous data was intentionally deleted. See [Troubleshooting](troubleshooting.md).

## Side-by-side installation

This fork uses:

```text
extended_openai_conversation_responses
```

The original integration uses:

```text
extended_openai_conversation
```

Because the domains differ, both integrations can be installed at the same time.

They do not share:

- config entries
- conversation agents
- services/actions
- events
- workspace files
- per-agent storage

This makes it possible to test the fork without immediately removing the original integration.

## Config entries are not migrated automatically

Add **Extended OpenAI Conversation (Responses)** as a new integration and configure its conversation agents separately.

A practical migration sequence is:

1. install this fork alongside the original
2. create a new conversation-agent entry
3. copy or recreate the model/provider settings you still want
4. expose the appropriate Home Assistant entities
5. test basic conversation and tool use
6. enable fork-specific features such as Responses, Web Search, memory, or conditional follow-ups
7. update automations/scripts that should call the fork's service namespace
8. remove the original integration only after you no longer need it

## Workspace files

If you use skills or other files stored under the original integration's workspace, copy the files you still need from:

```text
/config/extended_openai_conversation/
```

to:

```text
/config/extended_openai_conversation_responses/
```

Review copied files rather than blindly moving everything, especially if your upstream configuration contains old examples or tools you no longer use.

## Services and events

Automations and scripts that explicitly reference the original namespace will continue targeting the original integration until you change them.

For this fork, use the `extended_openai_conversation_responses` service/event namespace where applicable.

## Legacy entry migration inside this fork

Very early installations of this fork used a single legacy config-entry layout. When one of those genuine legacy entries is upgraded, its existing settings become the Conversation subentry and the migration also creates the default AI Task subentry that a fresh installation receives.

This compatibility step applies only while converting that original legacy layout. It does not add a missing AI Task to modern entries, so an AI Task that was deliberately removed later is not recreated.

## Memory migration inside this fork

When upgrading older versions of this fork, legacy memory settings migrate to **Off**, **Manual**, or **Automatic** without deleting existing memories.

Turning memory Off does not delete stored memory records.

## Context strategy migration

Existing agents without a stored context strategy retain the legacy clear-history fallback. New agents default to **Keep recent messages**.

You can change the context strategy under **Extended OpenAI → Assistant → Conversation**.

## `query_image`

The fork's `extended_openai_conversation_responses.query_image` action accepts `api_mode`; its default is `auto`.
