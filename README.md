# Extended OpenAI Conversation (Responses)

Bring a more capable AI conversation agent to Home Assistant.

Extended OpenAI Conversation connects Home Assistant Assist to the OpenAI API (or a compatible provider) and adds features such as device control, conversation memory, web search, request rules, local Home Assistant handling, custom tools, voice follow-ups, a knowledge library, guest controls, and more.

You can start with a simple setup and leave the advanced features alone until you need them.

> [!IMPORTANT]
> **A ChatGPT subscription does not include OpenAI API usage.**
>
> This integration uses the OpenAI API, which is billed separately by OpenAI or by the compatible provider you configure.

This project began as a fork of [jekalmin/extended_openai_conversation](https://github.com/jekalmin/extended_openai_conversation), but has since diverged substantially in features, configuration, architecture, and user interface.

## What can it do?

With a basic setup, you can use natural language through Home Assistant Assist to ask questions and control entities you have exposed to Assist.

For example:

> Turn off everything downstairs except the hallway light.

> Was the kitchen window left open overnight?

> Set the living room lights to 30%.

Optional features can add much more:

- **Web Search** — allow supported models to look up current information.
- **Persistent Memory** — let the assistant remember useful facts between conversations.
- **Knowledge Library** — give an agent larger reference material that it can search when needed.
- **Request Rules** — handle selected requests locally without calling the AI provider, or route requests to different models.
- **Local handling** — let Home Assistant handle simple built-in commands before an AI request, while more complex requests continue to the normal AI path.
- **Voice follow-ups** — control whether Assist keeps listening after a reply.
- **Guest Mode** — restrict what visitors can access and keep guest conversations separate.
- **Conversation archive** — optionally keep searchable conversation history locally.
- **Skills** — give agents reusable sets of instructions.
- **Custom Functions** — add tools for Home Assistant actions, scripts, templates, REST APIs, SQLite queries, and more.
- **Function Groups** — keep large sets of tools out of every request and load them only when needed.
- **Image and PDF input** — available with compatible models in Responses mode.
- **AI Task support** — create dedicated AI Task agents.
- **Usage diagnostics and agent testing** — inspect provider-reported usage and check an agent's configuration from the UI.
- **Backup & Restore** — back up or restore an individual agent and its associated data.

You do **not** need to understand or configure all of these features to use the integration.

## Before you install

You will need:

- Home Assistant
- an OpenAI API key, or credentials for a compatible provider
- a Home Assistant Assist pipeline if you want to use it as a conversation agent

If you want the assistant to know about or control Home Assistant entities, those entities also need to be **exposed to Assist**.

HACS is the easiest installation method, although manual installation is also supported.

## Installation

### Recommended: HACS

If you have not added a custom HACS repository before:

1. Open **HACS** in Home Assistant.
2. Open the HACS menu and choose **Custom repositories**.
3. Enter:

   `https://github.com/conorod1992/extended_openai_conversation`

4. Choose **Integration** as the repository type.
5. Add the repository.
6. Find **Extended OpenAI Conversation (Responses)** in HACS and download it.
7. Restart Home Assistant.

> [!TIP]
> A **custom repository** is simply a Home Assistant integration that is installed through HACS but is not part of HACS's default repository list.

### Manual installation

Copy:

`custom_components/extended_openai_conversation_responses`

from this repository into:

`<config>/custom_components/extended_openai_conversation_responses`

in your Home Assistant configuration directory, then restart Home Assistant.

For more detail, see [Installation](docs/getting-started/installation.md).

## First-time setup

### 1. Add the integration

After restarting Home Assistant:

1. Open **Settings → Devices & services**.
2. Select **Add Integration**.
3. Search for **Extended OpenAI Conversation (Responses)**.
4. Enter your API key.
5. If you are using OpenAI directly, leave **Base URL** unchanged.

You normally only need to change **Base URL** when using another compatible provider.

### 2. Open Extended OpenAI

After adding the integration, open **Extended OpenAI** from the Home Assistant sidebar.

This is where most configuration is managed.

The interface is split into sections for:

- assistant settings
- capabilities
- data and memory
- usage and maintenance

There is also an **Overview** page for common tasks and a **Guide** that explains the major features.

For a first setup, you can usually leave most settings at their defaults.

### 3. Select the conversation agent

To make your Home Assistant voice assistant use Extended OpenAI:

1. Open **Settings → Voice assistants**.
2. Edit the assistant you want to use.
3. Under **Conversation agent**, select **Extended OpenAI Conversation (Responses)**.

### 4. Expose the entities you want it to use

Open:

**Settings → Voice assistants → Expose**

and expose the entities the assistant should be able to know about or control.

For example, if `light.kitchen` is not exposed to Assist, the conversation agent should not be expected to know about or control it through normal Home Assistant entity control.

You do **not** need to create custom functions just to control ordinary exposed Home Assistant entities.

### 5. Try it

Start with something simple, such as:

> What lights are on?

or:

> Turn off the kitchen light.

Once that works, you can enable additional features such as Web Search, memory, Request Rules, local handling, or custom functions as needed.

For a fuller walkthrough, see [First setup](docs/getting-started/setup.md).

## A few terms you may see

You do not need to know these before getting started, but they are useful when exploring the settings.

| Term | What it means |
| --- | --- |
| **Conversation agent** | The system Home Assistant Assist sends your request to for a response. |
| **Exposed entity** | A Home Assistant entity you have allowed Assist to know about or control. |
| **Model** | The AI model used to answer a request, such as an OpenAI GPT model. |
| **API** | The connection used by Home Assistant to send requests to the AI provider. |
| **Responses API** | OpenAI's newer API used by supported models and features. |
| **Chat Completions** | An older OpenAI-compatible API format that remains supported. |
| **Function / tool** | A capability the model can call, such as controlling Home Assistant or running a custom action. |
| **Prompt** | Instructions and context supplied to the model. |

## Recommended starting settings

Most users can begin with the defaults and change only what they actually need.

| Setting | Default | What it does |
| --- | --- | --- |
| **Completion Model** | `gpt-5-mini` | Selects the model used by the configured provider. |
| **API mode** | Auto | Automatically chooses the appropriate supported API mode. |
| **Continue conversation** | HA Default | Uses Home Assistant's normal voice follow-up behaviour. |
| **Local handling** | Off | Optionally lets Home Assistant complete simple built-in commands before an AI request. |
| **Web Search** | Off | Lets compatible OpenAI Responses models search the web when needed. |
| **Memory mode** | Off | Enables persistent memory in Manual or Automatic mode. |
| **Knowledge Library** | Off | Lets an agent search longer reference material stored locally. |
| **Conversation archive** | Off | Optionally keeps searchable conversation text locally. |
| **Maximum tokens** | 500 | Limits the length of generated replies. |
| **Maximum function calls** | 10 | Limits tool-call loops in a single request. |
| **Context strategy** | Keep recent messages | Controls how long conversations are managed. |
| **Speech cleanup** | Off | Optionally cleans formatting or selected text from spoken replies. |

See the [full configuration guide](docs/configuration.md) for all options.

## Key features

### Home Assistant control

Extended OpenAI can work with entities exposed to Home Assistant Assist and can use included Home Assistant tools to perform supported actions.

This is enough for normal requests such as turning lights on or off, changing supported settings, or asking about entity state.

For supported entity changes, the integration can also keep a small snapshot of the previous state in the conversation so follow-up requests such as “undo that” can sometimes restore the previous setting.

This is not a universal undo system. Actions that are not reversible, are not supported by the snapshot system, or happened outside Extended OpenAI may not have enough information to be undone accurately.

### Local handling

Local handling is an optional shortcut for commands that Home Assistant already understands on its own.

When it is enabled, the order is:

1. **Request Rules** get the first chance to handle or route the request.
2. Home Assistant can handle a clear built-in command locally.
3. Anything that was not handled locally continues through Extended OpenAI and the AI model normally.

This means a simple request such as “turn off the kitchen light”, “what time is it?”, or “set a 20 minute timer” can be completed without an AI API request when Home Assistant has a matching built-in command. A more flexible request that Home Assistant does not understand still reaches the AI as usual.

The settings page shows the command types currently provided by your Home Assistant version. You can choose any command types that should **always continue to AI** instead of using the local shortcut.

There is also a separate option for **delayed device commands**. Home Assistant uses its timer command for both ordinary timers and requests such as “turn off the lights in 20 minutes”. You can keep normal timers local while sending delayed device actions to the normal AI / Function Tool path.

> [!TIP]
> Home Assistant Assist also has its own **Prefer local handling** pipeline setting. If that is enabled, Home Assistant may complete a command before it reaches Extended OpenAI at all. The Extended OpenAI settings page warns when a pipeline using the current agent is configured this way. Turn the pipeline option off if you want Extended OpenAI to control the order and apply its command-type exceptions.

When Guest Mode is active, this shortcut is deliberately skipped so guest requests continue through Extended OpenAI's existing policy checks.

### Web Search

When Web Search is enabled with a compatible direct OpenAI Responses setup, the model can decide when it needs current information.

It does **not** search the web for every request.

[Read the Web Search guide](docs/features/web-search.md)

### Persistent Memory

Persistent Memory lets the assistant keep useful facts beyond a single conversation.

- **Off** — no persistent memory.
- **Manual** — facts are stored when explicitly requested.
- **Automatic** — the assistant can also save stable, useful information automatically.

The integration retrieves only a limited selection of relevant memories for a conversation instead of placing every saved memory into every prompt.

[Read the Persistent Memory guide](docs/features/persistent-memory.md)

### Knowledge Library

The Knowledge Library is intended for larger reference material that should not be placed into every request.

Examples might include:

- household information
- appliance notes
- procedures
- inventories
- reference documents

The model can search the library when it needs information from it. Knowledge sources are read-only from the model's point of view.

[Read the Knowledge Library guide](docs/features/knowledge-library.md)

### Request Rules

Request Rules let you define special handling for selected requests.

They can, for example:

- handle a simple command locally without making an AI API request
- call an enabled Function Tool directly
- run a Home Assistant action
- route a request or conversation to another model
- change reasoning effort for matching requests

Sentence patterns can also capture changing values. For example:

`Add {item} to my shopping list`

can capture the words spoken in place of `{item}` and use them in a supported action or function.

[Read the Request Rules guide](docs/features/request-rules.md)

### Guest Mode

Guest Mode provides a separate policy for visitors using an assistant.

It can restrict available Home Assistant access, tools, and Knowledge sources while keeping guest conversation continuity separate from the owner's retained data.

[Read the Guest Mode guide](docs/features/guest-mode.md)

### Voice follow-ups

You can choose whether Home Assistant:

- uses its normal follow-up behaviour
- always listens for another request
- lets the model indicate when an immediate reply is expected

[Read the Voice Follow-ups guide](docs/features/voice-followups.md)

### Conversation archive and privacy

Conversation archiving is optional and disabled by default.

When enabled, the integration locally retains the user's text and the final assistant response so conversations can be searched and managed from **Extended OpenAI → Conversations**.

Provider payloads, attachments, tool data, and hidden reasoning are not stored as archive conversation text.

[Read the Conversation Archive guide](docs/features/conversation-archive.md)

### Skills

Skills are reusable instruction sets that can be made available to an agent.

They are useful when you want the assistant to follow a particular workflow or set of instructions without adding all of that text permanently to the main prompt.

[Read about Skills](docs/features/skills.md)

### Custom Functions

Custom Functions give the model additional tools.

They can be used for more advanced workflows involving:

- Home Assistant scripts and actions
- templates
- REST endpoints
- web scraping
- composite functions
- SQLite queries

They are an advanced feature. Basic control of exposed Home Assistant entities does not require custom functions.

[Read about Custom Functions](docs/functions/index.md)

### Function Groups

If an agent has many functions, Function Groups can reduce how much tool information is sent with every request.

A group can be:

- **Always available**, or
- **Load when needed**

For a load-on-demand group, the model initially receives only a short description of the group. If it needs those functions, it can load their full definitions for the active conversation.

This can reduce request size for agents with large tool collections, although the first use of a group may require an additional provider round-trip.

[Read the Function Groups guide](docs/features/function-groups.md)

### Context management

Long conversations can be managed by:

- keeping recent complete turns
- clearing older conversation history
- summarising older context

[Read the Context Management guide](docs/features/context-management.md)

### Image and PDF input

Compatible models using Responses mode can accept supported image and PDF input.

Availability depends on the model and provider.

[Read about Responses API support](docs/features/responses-api.md)

### Speech cleanup

Optional speech cleanup can remove things such as Markdown links and bare URLs from text sent to text-to-speech while preserving the original model response elsewhere.

Advanced users can also configure ordered regular-expression replacements.

### Usage statistics and agent testing

The integration can expose a disabled-by-default diagnostic Usage sensor using provider-reported request and token counts.

The **Test agent** tool checks the current agent configuration and can make at most one small model request. It does not execute Home Assistant device or service actions.

[Read the Usage Statistics guide](docs/features/usage-statistics.md)

### Backup & Restore

Each agent can be backed up independently for recovery or migration.

Backups can include the agent configuration and associated Extended OpenAI data such as Request Rules, memories, Knowledge sources, archived conversations, Guest Mode settings, and usage history.

Treat backup files as private data.

## Responses API and provider compatibility

The integration supports both OpenAI's newer **Responses API** and **Chat Completions**.

For most users, **API mode: Auto** is the best starting point.

Some features depend on the API mode, model, or provider. For example:

- OpenAI Web Search requires a compatible Responses setup.
- image/PDF support depends on the selected model and API mode.
- reasoning settings are model-dependent.
- a custom OpenAI-compatible provider may support Chat Completions without supporting the Responses API.

Do not manually select **Responses** for a custom provider unless that provider implements `/v1/responses` compatibly.

See [Responses API and compatibility](docs/features/responses-api.md).

## Advanced: direct processing from an automation

Most users do not need this. Normal Assist usage should continue to use the selected conversation agent.

For automations or sentence triggers that need to send text directly into Extended OpenAI's processing pipeline, use:

```yaml
action: extended_openai_conversation_responses.process
data:
  text: "Turn off the kitchen light"
response_variable: result
```

When multiple Extended OpenAI agents exist, you can also specify `agent_id`.

The action supports additional context fields such as `conversation_id`, `device_id`, `satellite_id`, and `language`.

Request Rules, local handling, Guest Mode, memory, tools, model routing, conversation continuity, and response cleanup still apply.

> [!WARNING]
> The Request Rules **Test request** feature uses the real processing path. It can perform Home Assistant actions and is not a dry run.

## Saving, duplicating and exporting agents

Most agent settings are edited as a draft and are not applied until you select **Save**.

Function Tools and Function Groups are managed separately and save their own create, edit, enable/disable, and delete operations immediately.

When duplicating or exporting an agent:

- API credentials remain with the parent integration entry
- memories are not copied as part of normal agent duplication/export
- conversation archives are not copied
- Knowledge content is not copied
- usage history is not copied

Exports attempt to redact common credential fields, but custom Function Tool definitions can contain secrets in arbitrary text, URLs, templates, commands, or provider-specific fields.

**Always review an exported file before sharing it.**

## Migrating from the original Extended OpenAI Conversation

Home Assistant treats this fork as a separate integration with the domain:

`extended_openai_conversation_responses`

It can be installed alongside the original `extended_openai_conversation` integration.

Existing configuration entries are not automatically migrated. Add this integration separately, configure the agents you want, and update only the automations or scripts that should use this fork.

[Read the migration guide](docs/migration.md)

## Documentation

More detailed documentation is available in [`docs/`](docs/).

Good places to start:

- [Installation](docs/getting-started/installation.md)
- [First setup](docs/getting-started/setup.md)
- [Full configuration reference](docs/configuration.md)
- [Request Rules](docs/features/request-rules.md)
- [Persistent Memory](docs/features/persistent-memory.md)
- [Knowledge Library](docs/features/knowledge-library.md)
- [Guest Mode](docs/features/guest-mode.md)
- [Custom Functions](docs/functions/index.md)
- [Migration from the original integration](docs/migration.md)

## Debug logging

If you need integration debug logs, add the following to your Home Assistant YAML configuration:

```yaml
logger:
  logs:
    custom_components.extended_openai_conversation_responses: debug
```

Restart Home Assistant after changing logger configuration if required by your setup.

> [!CAUTION]
> Debug logs can be verbose and may contain information you do not want to retain. Avoid leaving debug logging enabled permanently.

## Credits

Extended OpenAI Conversation (Responses) was originally forked from [jekalmin/extended_openai_conversation](https://github.com/jekalmin/extended_openai_conversation).

The upstream project's custom-function architecture and earlier Home Assistant/OpenAI integration work formed the foundation of this project.

Thanks to the upstream author and contributors, and to everyone contributing to Home Assistant, HACS, and the OpenAI ecosystem.
