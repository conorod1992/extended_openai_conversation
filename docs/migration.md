# Migrating from the original integration

Extended OpenAI Conversation (Responses) began as a fork of `jekalmin/extended_openai_conversation`, but Home Assistant treats the two projects as separate integrations.

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

## Memory migration inside this fork

When upgrading older versions of this fork, legacy memory settings migrate to **Off**, **Manual**, or **Automatic** without deleting existing memories.

Turning memory Off does not delete stored memory records.

## Context strategy migration

Existing agents without a stored context strategy retain the legacy clear-history fallback. New agents default to **Keep recent messages**.

You can change the context strategy at any time in the conversation agent's options.

## `query_image`

The fork's `extended_openai_conversation_responses.query_image` action accepts `api_mode`; its default is `auto`.
