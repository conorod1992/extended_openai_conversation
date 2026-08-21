# Extended OpenAI Conversation (Responses)

A feature-rich OpenAI conversation integration for Home Assistant, with Home Assistant tools, the OpenAI Responses API, Web Search, persistent memory, voice follow-ups, skills, usage diagnostics, AI Task support, and more.

This project began as a fork of [jekalmin/extended_openai_conversation](https://github.com/jekalmin/extended_openai_conversation). It retains the upstream project's powerful custom-function system and Chat Completions support, but has since diverged substantially in features, architecture, configuration, and user experience.

> [!NOTE]
> The integration uses the **OpenAI API**, which is separate from a ChatGPT subscription and billed separately by the configured provider.

## Highlights

### Modern OpenAI support

- **Responses API and Chat Completions** — use automatic model routing or select an API mode manually.
- **Reasoning models** — configure reasoning effort when supported by the selected model.
- **Image and PDF input** — available in Responses mode for compatible models.
- **Structured outputs and service tiers** — exposed where supported by the model/provider.

### Better Home Assistant conversations

- **Home Assistant control** — call services and control exposed devices and entities.
- **History access** — answer questions using Home Assistant entity history.
- **Persistent memory** — conversation-start lexical or embedding-assisted retrieval, personal plus shared-household reads, importance, canonical keys, and reliable upserts, while preserving Off, Manual, and Automatic modes.
- **Knowledge Library** — keep large per-agent reference sources local and retrieve only relevant excerpts on demand.
- **Voice follow-ups** — use Home Assistant's default behavior, always keep listening, or let the model decide whether a reply is expected.
- **Context management** — keep recent turns, clear history, or summarize older conversation context.

### Extensible and observable

- **Per-agent Backup & Restore** — create a private recovery or migration backup containing configuration, memories, Knowledge sources, archived conversations, and usage history, then inspect and explicitly restore it with replacement semantics.

- **Web Search** — let compatible OpenAI Responses models retrieve current information when needed.
- **Skills** — load reusable instruction sets per conversation agent.
- **Custom functions** — extend the assistant with native tools, Home Assistant scripts, templates, REST endpoints, scraping, composite functions, and SQLite queries.
- **Built-in Function presets and capability controls** — insert shipped native capabilities into the editable YAML workflow, and enable or disable any configured Function Tool without losing its definition or group assignment.
- **On-demand function groups** — keep large function collections compact by letting the model load only the relevant detailed tool schemas for an active conversation.
- **Usage diagnostics** — track provider-reported request and token usage without estimating cost.
- **Effective prompt preview** — render the fresh-request system/context baseline with current Home Assistant template values, excluding user input and conversation history.
- **Optional conversation archive** — locally retain and lexically search discussions, with deterministic private-session and deletion controls.
- **Explicit voice ownership** — map unidentified satellites to users, a separate shared household, or a no-retention policy.
- **Agent testing** — validate the configured model, API mode, tools, Web Search, memory, entities, and skills from the integration UI.
- **AI Task** — create dedicated AI Task agents with a streamlined set of model options.

## Getting started

### Requirements

You will need:

- Home Assistant
- an OpenAI API key, or a compatible provider supported by your chosen API mode
- a Home Assistant Voice Assistant / Assist pipeline if you want to use the integration for conversation
- entities exposed to Assist if you want the model to know about or control them

HACS is recommended for installation but is not required.

### 1. Install

**HACS**

1. Open HACS.
2. Add `https://github.com/conorod1992/extended_openai_conversation` as a custom **Integration** repository.
3. Download **Extended OpenAI Conversation (Responses)**.
4. Restart Home Assistant.

**Manual installation**

Copy the `extended_openai_conversation_responses` folder from `custom_components` into your Home Assistant `<config>/custom_components/` directory, then restart Home Assistant.

### 2. Add the integration

1. Open **Settings > Devices & services**.
2. Select **Add Integration**.
3. Search for **Extended OpenAI Conversation (Responses)**.
4. Enter your API key.
5. Leave **Base URL** unchanged for the OpenAI API. Change it only when using a compatible provider that requires a custom endpoint.

The native Home Assistant flow is intentionally limited to provider and connection setup. After the integration is added, open **Extended OpenAI** in the sidebar and choose **Configuration** for a searchable, full-width settings document covering conversation behaviour, prompts, capabilities, archive, voice identity, spoken-response cleanup, context, model options, and retention. Use **Tools** for the friendly tool list and YAML-first single-tool editor; YAML parsing and function validation remain backend-authoritative.

Under **Prompt & context**, new agents include Home Assistant-local date/time and exposed-device state through two simple, default-on controls. Advanced formatting stays collapsed and accepts optional templates; clearing an override restores the integration-maintained format. Existing agents keep their prompt unchanged and migrate with both generated blocks off, avoiding duplicate legacy `now()` or `exposed_entities()` output.

**Preview effective request** shows the locally assembled fresh-request system/context, effective first-request Function Tools and Function Group loader, integration/provider tools, and non-secret request settings. It excludes user input and all conversation/tool-call history, does not save or mutate runtime state, and reports deterministic character counts plus exact local Function Group schema savings. Provider token accounting and actual prompt-cache reuse remain authoritative after real requests.

### 3. Select the conversation agent

1. Open **Settings > Voice assistants**.
2. Edit the assistant you want to use.
3. Under **Conversation agent**, select **Extended OpenAI Conversation (Responses)**.

### 4. Expose Home Assistant entities

Open **Settings > Voice assistants > Expose** and expose the entities the assistant should be able to know about or control.

Expose only the entities you actually want available to conversation agents. Exposure provides the model with Home Assistant context and is separate from configuring custom functions.

### 5. Try it

Once configured, try prompts such as:

> Turn off everything downstairs except the hallway light.

> Was the kitchen window left open overnight?

> Remember that I prefer temperatures in Celsius.

> What's the latest Home Assistant release, and is there anything important for voice assistants?

The exact capabilities available depend on your exposed entities, enabled features, functions, model, and provider.

## Configuration at a glance

Most users can start with the defaults and choose only a model. The main options are:

| Setting | Default | Purpose |
| --- | --- | --- |
| **Completion Model** | `gpt-5-mini` | Model sent to the configured provider. |
| **API mode** | Auto | Chooses Chat Completions or Responses. |
| **Continue conversation** | HA Default | Controls immediate voice follow-ups. |
| **Web Search** | Off | Allows compatible Responses models to search the web. |
| **Memory mode** | Off | Enables Manual or Automatic persistent memory. |
| **Knowledge Library** | Off | Enables read-only, on-demand search of maintained Knowledge sources. |
| **Conversation archive** | Off | Locally retains user/final-assistant text for 30 days when enabled. |
| **Model archive search** | Off | Lets the model search only the current resolved scope. |
| **Unidentified voice** | Do not retain | Prevents transcript and memory writes until an owner policy is selected. |
| **Maximum tokens** | 500 | Limits generated response tokens. |
| **Maximum function calls** | 10 | Prevents unbounded tool-call loops. |
| **Context strategy** | Keep recent messages | Controls long-conversation truncation. |
| **Skills** | All available for new agents | Selects reusable instructions the agent may load. |
| **Functions** | Included HA tools | Defines tools available to the model. |
| **Speech cleanup** | Off | Optionally removes Markdown links/URLs and applies ordered regex replacements to TTS only. |

See the [full configuration guide](docs/configuration.md) for details and provider-specific limitations.

The Configuration and Tools pages share a local draft until it is explicitly saved or reverted. Duplicate and Export are disabled while that draft is dirty. Duplicate and import/export copy agent behaviour only: API credentials stay on the parent integration entry, and memories, archives, Knowledge content, and usage history are not copied. Export performs best-effort redaction of common credential fields, but Function Tool definitions can contain secrets in arbitrary strings, URLs, templates, commands, or provider-specific fields. Review every exported file before sharing it.

## Key features

### Responses API

For newer supported models, **Auto** mode can use OpenAI's `/v1/responses` endpoint while retaining Home Assistant function execution and conversation history. Responses mode adds support for features such as reasoning effort, image/PDF inputs, structured outputs, and OpenAI Web Search where available.

[Read about API modes and compatibility](docs/features/responses-api.md)

### Web Search

When enabled with the direct OpenAI Responses API, the model can decide when current information requires a hosted Web Search. Enabling the feature does **not** make every request search the web.

[Read the Web Search guide](docs/features/web-search.md)

### Persistent memory

Persistent memory retains concise facts beyond a single chat. **Manual** stores explicit remember requests; **Automatic** can also save stable useful facts. A bounded bundle is selected once from the opening message and reused for the logical conversation; `0` keeps retrieval tool-only. Local BM25-style lexical retrieval is the default, with optional embedding-only hybrid semantic retrieval and graceful lexical fallback. Authenticated users may read their personal and enabled shared-household facts, while writes default strictly to personal. Importance, canonical subject/key metadata, freshness timestamps, and `memory_upsert` support durable fact replacement without automatic expiry.

[Read the persistent memory guide](docs/features/persistent-memory.md)

### Knowledge Library

The opt-in Knowledge Library stores longer maintained references such as a kitchen layout, tools inventory, appliance notes, or household procedures. The model uses built-in `knowledge_search`, `knowledge_list`, and `knowledge_get` tools only when needed; full sources are never placed in every prompt and the model cannot modify them.

[Read the Knowledge Library guide](docs/features/knowledge-library.md)

### Conversation archive and privacy

The archive is distinct from persistent memory and disabled by default. It stores only user text and the final assistant response in local monthly partitions; tool data, provider payloads, attachments, and hidden reasoning are excluded. Titles are derived locally from the first message, with no extra model call.

Use **Extended OpenAI → Conversations** to search, inspect, and delete retained sessions. “Don't save this conversation” deletes retained turns for only the active session and blocks subsequent storage. “You can save conversations again” starts a new retained boundary and never restores private text. Bulk deletion requires confirmation and reports exact counts.

[Read the conversation archive and voice ownership guide](docs/features/conversation-archive.md)

### Voice follow-ups

Choose between Home Assistant's normal behavior, always requesting another utterance, or a conditional mode where the model signals when its answer expects an immediate reply.

[Read the voice follow-up guide](docs/features/voice-followups.md)

### Context management

Long conversations can keep recent complete turns, clear previous history, or summarize older context. Function calls and results are kept together when recent messages are retained.

[Read the context management guide](docs/features/context-management.md)

### Skills and custom functions

Skills provide reusable instructions, while custom functions provide new tools. You do **not** need to build custom functions for basic exposed-entity control.

[Read about skills](docs/features/skills.md) · [Read about custom functions](docs/functions/index.md)

### Function groups and on-demand tools

Function groups are an optional way to reduce repeated input from large tool collections. Open **Extended OpenAI → Tools**, choose **Create group**, add a concise model-facing description, select **Always available** or **Load when needed**, and choose member functions with the searchable checklist. Existing ungrouped functions remain always available, so upgrades do not change existing agent behaviour.

For an on-demand group, normal requests contain only a compact catalogue entry such as `Reminders — Create and manage scheduled reminders`. If the current request needs it, the model calls the integration-owned `load_function_groups` tool inside the existing tool loop. The next model step includes that group's full, already-validated function schemas. There is no classifier, separate router model, keyword matching, or embedding lookup.

Loaded groups remain available for follow-ups in the same active conversation and reset when that conversation expires, is replaced, or the agent/integration reloads. Loading itself performs no Home Assistant action and does not consume the configured real-function-call allowance, though a five-round internal loader cap prevents loops. The first use of a group may add one provider round-trip.

**Before:** an agent with 50 configured functions sends 50 full schemas on every request.

**After:** normal requests send ungrouped/always-available schemas plus a compact catalogue for groups such as Reminders, Calendar, Gmail, Conditional Notifications, and Deferred Actions. After the model requests `reminders`, only the Reminder functions are added for that active conversation. Exact savings vary with schema size; diagnostics report schema counts and serialized character estimates rather than fabricated token counts.

[Read the Function Groups guide](docs/features/function-groups.md)

### Spoken-response cleanup

Speech post-processing removes Markdown links/citations, formatting, and bare URLs from progressive and completed TTS while retaining the original provider response in ChatLog history, events, archives, and context. Home Assistant's progressive visual updates and TTS share one listener, so live visual deltas are speech-safe too. Custom regex replacements run on the completed response and disable progressive TTS for that agent because arbitrary patterns cannot be applied reliably to incomplete text.

Custom regex is an advanced feature. Rules use standard Python regular expressions and run sequentially, for example:

```yaml
- pattern: '\[[0-9]+\]'
  replacement: ''
- pattern: '\bHA\b'
  replacement: 'Home Assistant'
```

Invalid expressions are rejected when configuration is saved and skipped defensively at runtime.

### Usage statistics and agent testing

Each conversation-agent device can expose a disabled-by-default diagnostic Usage sensor with real provider-reported request and token counts. **Test agent** performs local checks plus at most one minimal model request without executing Home Assistant device or service actions.

[Read the usage guide](docs/features/usage-statistics.md)

## Provider compatibility

Feature support depends on both API mode and provider implementation.

| Capability | Direct OpenAI | Compatible custom provider | Azure OpenAI |
| --- | :---: | :---: | :---: |
| Chat Completions | ✓ | If implemented | ✓ |
| Responses API | ✓ | If implemented | Provider-dependent |
| Home Assistant functions | ✓ | If compatible | ✓ |
| Web Search | ✓, Responses only | — | — |
| Reasoning effort | Model-dependent | Provider/model-dependent | Provider/model-dependent |

Do not select **Responses** for a custom provider unless it implements `/v1/responses` compatibly.

## Migrating from the original integration

Home Assistant treats this fork as a separate integration with the domain `extended_openai_conversation_responses`. It can be installed alongside the original without sharing agents, services, events, or workspace files.

Configuration entries are not automatically migrated from `extended_openai_conversation`. Add this integration separately, configure its agents, and update only the automations/scripts you want to target the fork.

[Read the migration guide](docs/migration.md)

## Documentation

The README is intentionally a quick introduction. Detailed guides live in [`docs/`](docs/), including:

- [Installation and first setup](docs/getting-started/installation.md)
- [Configuration reference](docs/configuration.md)
- [Responses API](docs/features/responses-api.md)
- [Web Search](docs/features/web-search.md)
- [Persistent memory](docs/features/persistent-memory.md)
- [Knowledge Library](docs/features/knowledge-library.md)
- [Voice follow-ups](docs/features/voice-followups.md)
- [Context management](docs/features/context-management.md)
- [Skills](docs/features/skills.md)
- [Custom functions](docs/functions/index.md)
- [Usage statistics](docs/features/usage-statistics.md)
- [Conversation archive, privacy, and voice ownership](docs/features/conversation-archive.md)
- [Migration](docs/migration.md)
- [Troubleshooting](docs/troubleshooting.md)

The same documentation can be published as a navigable site with MkDocs Material using the included `mkdocs.yml` and GitHub Pages workflow.

## Logging

For integration debug logging, add:

```yaml
logger:
  logs:
    custom_components.extended_openai_conversation_responses: debug
```

Avoid leaving debug logging enabled indefinitely: API request/response logs can be verbose and may contain information you do not want retained in logs.

## Credits

Extended OpenAI Conversation (Responses) was originally forked from [jekalmin/extended_openai_conversation](https://github.com/jekalmin/extended_openai_conversation), whose custom-function architecture and earlier Home Assistant/OpenAI integration work formed the foundation of this project.

Thank you to the upstream author and contributors, and to everyone contributing to Home Assistant, HACS, and the OpenAI ecosystem.
