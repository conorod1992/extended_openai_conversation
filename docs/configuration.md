# Configuration

Home Assistant's native integration flow now focuses on the provider connection: API provider/key, base URL, API version, organisation, and authentication/bootstrap options. Create, remove, and rename conversation subentries with normal Home Assistant lifecycle controls.

Configure conversation-agent behaviour from **Extended OpenAI** in the sidebar:

- **Configuration** is one searchable, full-width settings surface. Its sections remain in a single vertical document, with optional jump links and responsive fields inside each section.
- **Tools** keeps a searchable, grouped tool overview with friendly Function Group controls plus Edit, Duplicate, and Delete actions for individual YAML-defined functions.

The editor writes the existing Home Assistant config subentry; there is no separate frontend store. Changes remain a local draft until Save is pressed. Voice ownership is separate from spoken-response cleanup, dependent options stay visible but disabled, and Custom replacements remain directly visible while speech processing is enabled.

The **Prompt & context** section has default-on controls for Home Assistant-local date/time and exposed-device names/states on new or reset configurations. Their integration-maintained output is placed with volatile context after the indivisible user prompt. Optional advanced templates are collapsed by default and can be cleared to restore the maintained format. Migration never rewrites an existing prompt and leaves both generated blocks off for existing agents, preventing duplicate legacy template output.

**Preview effective request** renders the locally inspectable content accompanying a brand-new message: production-ordered system/context, first-request custom tools, the Function Group loader/catalogue, integration and provider-hosted tools, and non-secret request settings. User input, conversation and tool-call history, query-derived persistent memories, credentials, and opaque provider framing are excluded. Counts are exact characters of the displayed text or canonical local JSON serialization; Function Group savings compare the grouped first request (including loader overhead) with all enabled custom schemas sent eagerly. Provider token/cache usage after real requests remains authoritative. Preview does not save, call the provider, record usage, load groups, or mutate memory/history.

For natural provider prompt-cache reuse, the user-authored prompt remains an indivisible rendered block, followed by stable integration-owned memory, temporary-memory, Knowledge, archive, and continuation guidance where enabled. Volatile retrieved persistent-memory and active temporary-memory context follows those stable sections. Tool schemas remain provider tool definitions, message roles retain their normal system/history/current-user order, and actual cache hits remain provider-controlled.

Function Tool YAML uses a clean single-tool shape with `spec:` and `function:` at the top level. The backend serializes existing tools to readable YAML, parses edits, validates the selected function type, and returns normalized metadata. The browser does not maintain a second JSON/Form representation or duplicate backend schema validation.

The Add Function Tool dialog includes **Insert Built-in Function**. Built-in functions are implemented directly by Extended OpenAI Conversation; selecting one inserts a complete, editable native Function Tool preset into the normal YAML editor and does not save it automatically. Already-configured native implementations are labelled in the picker.

Every Function Tool is enabled by default unless its YAML contains `enabled: false`. Disabled tools stay configured, searchable, editable, duplicable, deletable, and assigned to their Function Group, but are not sent to the model and cannot execute. The card switch edits the same shared draft as the rest of the Tools page. Administrators can change the persisted state with `extended_openai_conversation_responses.enable_function_tools` and `extended_openai_conversation_responses.disable_function_tools`; these actions are intentionally not exposed to the model automatically.

Function Groups are optional metadata around that unchanged YAML. Existing ungrouped functions remain **Always available**. A **Load when needed** group initially sends only its compact name and description; the model requests relevant groups through the normal tool loop and their detailed schemas remain available for the active conversation. The first use may add one provider round-trip. No separate router/classifier call, keyword matching, or embeddings are used. Deleting a group never deletes its functions. See [Function groups](features/function-groups.md).

Configuration and Tools share one local draft, so moving between those pages preserves unsaved changes. Duplicate and Export are disabled until the draft is saved or reverted. Duplicate and versioned import/export copy configuration only. Parent provider credentials, memories, retained conversations, Knowledge content, and usage history are excluded. Export redaction is best effort: Function Tool definitions may still contain embedded credentials or sensitive values in arbitrary fields. Review exported files before sharing them. Import can create a new agent without discarding the current draft, or overwrite the current agent after validation and explicit confirmation that any dirty draft will be discarded.

## Backup & Restore

**Export configuration** remains the lightweight, reusable agent-configuration format. The separate **Backup & Restore** section creates a private recovery or migration backup for one conversation agent. A full backup contains the normalized saved configuration, persistent memories, active temporary memories with their original absolute expiry, canonical Knowledge source text and metadata, retained archive sessions and turns, plus lifetime, daily, request, and run usage data. Provider API keys, OAuth tokens, parent config-entry credentials, in-flight conversations, loaded Function Groups, caches, locks, and other runtime state are excluded.

Restore first validates the format and every category, drops temporary memories that have expired since the backup was created, and shows a summary. **Restore everything** then replaces the selected agent's existing durable state; it does not merge or add usage totals. Knowledge source data is restored and its derived local search index is rebuilt. If a storage write fails, the integration attempts to restore the pre-restore snapshot. Full backups can contain prompts, private memories, Knowledge content, archived conversations, and usage metadata, so store them securely. Reconnect or re-enter provider credentials separately when migrating to another Home Assistant installation.

## Speech cleanup

Speech post-processing cleans both progressive and completed TTS. A bounded streaming sanitizer removes Markdown links/citations, formatting, and bare URLs before Home Assistant's shared ChatLog listener can feed progressive TTS. The provider-neutral fallback works for Responses, Chat Completions, and compatible providers across arbitrary delta boundaries. The original provider text remains in ChatLog history, events, archive, and conversation context; Home Assistant currently shares one progressive listener between visual updates and TTS, so the live visual stream also receives the speech-safe deltas.

Responses API `url_citation` annotations are retained on the native response item for replay and context. Annotation events may arrive after their cited text, so structured metadata supplements rather than replaces the textual streaming sanitizer.

Example advanced rules:

```yaml
- pattern: '\[[0-9]+\]'
  replacement: ''
- pattern: '\bHA\b'
  replacement: 'Home Assistant'
```

Rules use standard Python regex syntax and run sequentially on the completed response. Because arbitrary patterns can depend on text that has not arrived yet, configuring any custom replacement disables progressive TTS for that agent. Invalid rules are rejected on save and skipped defensively at runtime. Use the frontend speech preview to test the exact completed-response pipeline.

Conversation-agent options are available from the assistant/integration configuration in Home Assistant. Most users can start with the defaults and change only the model.

## Main options

| Option | Default | What it does |
| --- | --- | --- |
| **Prompt Template** | Included assistant prompt | User-authored behavioural instructions sent as one intact block. Supports Home Assistant templates. |
| **Include current date & time** | On for new agents | Adds Home Assistant-local date/time as volatile integration context. |
| **Include exposed devices** | On for new agents | Adds compact exposed entity names, states, and genuine aliases as volatile integration context. |
| **Completion Model** | `gpt-5-mini` | Model name sent to the configured provider. |
| **API mode** | Auto | Chooses Chat Completions or Responses. |
| **Continue conversation** | HA Default | Controls immediate voice follow-ups. |
| **Web Search** | Off | Enables OpenAI hosted Web Search when supported. |
| **Web search context** | Low | Controls Web Search retrieval breadth. |
| **Memory mode** | Off | Enables Manual or Automatic persistent memory. |
| **Knowledge Library** | Off | Enables on-demand model search and retrieval of maintained per-agent Knowledge sources. |
| **Automatically retrieved memories** | 3 | Maximum locally ranked memories added automatically to a request. |
| **Maximum tokens to return in response** | 500 | Limits model output tokens. |
| **Maximum function calls per conversation** | 10 | Caps repeated tool calls within one conversation. |
| **Skills** | All available for a new agent | Selects reusable skill instructions the agent may load. |
| **Functions** | Included Home Assistant tools | Defines tools available to the model. |
| **Function groups** | None | Optionally keeps related function schemas always available or loads them only when needed. |
| **Context Threshold** | 40000 | Approximate conversation size at which the selected context strategy is applied. |
| **Context truncation strategy** | Keep recent messages | Decides what happens when context grows beyond the threshold. |
| **Advanced Options** | Off | Reveals model/provider-specific controls. |

## API mode

### Auto

Uses the integration's model-routing behavior to choose an endpoint. Newer supported GPT-5 models can use Responses, while older models can continue using Chat Completions.

For most users, **Auto** is the recommended starting point.

### Chat Completions

Always sends requests to `/v1/chat/completions`.

Choose this when:

- your provider supports Chat Completions but not Responses
- you are troubleshooting Responses compatibility
- you intentionally need legacy endpoint behavior

### Responses

Always sends requests to `/v1/responses`.

Responses mode supports integration features such as streaming, Home Assistant functions, sequential tool calls, image/PDF inputs, structured outputs, reasoning effort, and service tiers when the selected model/provider supports them.

Do not force Responses with a custom provider unless it implements a compatible `/v1/responses` endpoint.

See [Responses API](features/responses-api.md).

## Continue conversation

- **HA Default** — preserve Home Assistant's normal follow-up behavior.
- **Always** — request another utterance after every successful response.
- **Conditional** — allow the model to signal when its answer expects an immediate reply.

The voice client must support Home Assistant's `continue_conversation` signal. See [Voice follow-ups](features/voice-followups.md).

## Web Search

Web Search is available only when the configured request path supports OpenAI's hosted Web Search tool. It is intended for current public information rather than Home Assistant state.

**Web search context** controls retrieval breadth rather than an exact number of results or tokens. Higher settings can increase latency and API cost.

See [Web Search](features/web-search.md).

## Memory

- **Off** — no persistent-memory storage or automatic retrieval.
- **Manual** — store facts only after an explicit remember request.
- **Automatic** — allow proactive storage of stable useful facts under the integration's memory rules.

**Automatically retrieved memories** can be set from 0 to 10. Set it to `0` when you want the model to retrieve memories only through memory tools.

See [Persistent memory](features/persistent-memory.md).

## Knowledge Library

Turn on **Knowledge Library** to expose the built-in `knowledge_search`, `knowledge_list`, and `knowledge_get` tools when the agent has at least one source. Sources remain stored when the option is Off and can still be prepared in **Configure > Manage knowledge**. This option is independent of persistent-memory mode, and source contents are never injected wholesale into the normal system prompt.

See [Knowledge Library](features/knowledge-library.md).

## Context management

When a conversation becomes large enough to cross the configured threshold, choose one of the available strategies:

- **Keep recent messages** — removes oldest complete user turns while keeping tool calls and tool results together.
- **Clear all messages** — legacy behavior that starts again without prior conversation history.
- **Summarize older messages** — makes a bounded summary request, keeps recent raw turns, and falls back to keeping recent messages if summarization fails.

See [Context management](features/context-management.md).

## Functions

The default Functions configuration provides Home Assistant tools. Basic exposed-entity control does not require you to create a custom function.

Customize Functions when you want to give the assistant an additional tool or change the available function definitions.

For agents with many functions, create Function Groups from the Tools page and choose **Load when needed** to reduce repeated schema input. Groups and assignments are preserved by agent duplication and import/export, while legacy configurations require no changes.

See [Custom functions](functions/index.md) and [Function groups](features/function-groups.md).

## Advanced options

Only controls supported by the selected model are shown.

### Temperature and Top P

Control response variation for models that support these parameters. Avoid changing both unless you understand the interaction between them.

### Reasoning effort

For supported reasoning models, selects **Low**, **Medium**, or **High** reasoning effort.

Higher effort can improve difficult reasoning tasks but may increase latency and token usage.

### Service tier

Where supported, select **Auto**, **Default**, **Flex**, or **Priority**. Availability and pricing depend on the provider and account.

### Shorten tool call IDs

Creates 9-character tool-call IDs for providers that require or work better with shorter identifiers, such as some Mistral-compatible deployments.

Leave this disabled for OpenAI unless you have a specific compatibility reason to enable it.

## AI Task options

AI Task agents use a smaller configuration surface focused on:

- model
- API mode
- maximum output tokens
- supported advanced model settings

Conversation-specific features such as voice follow-ups, Web Search, memory, the Knowledge Library, skills, and custom Functions are not shown there.

See [AI Task](features/ai-task.md).
