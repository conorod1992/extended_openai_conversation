# Configuration

Home Assistant's native integration flow now focuses on the provider connection: API provider/key, base URL, API version, organisation, and authentication/bootstrap options. Create, remove, and rename conversation subentries with normal Home Assistant lifecycle controls.

Configure conversation-agent behaviour from **Extended OpenAI** in the sidebar:

- **Configuration** provides a responsive, searchable full-width editor for prompts, model options, capabilities, archive/memory/Knowledge, voice identity, context, speech cleanup, and retention.
- **Tools** provides a list editor, common structured fields, duplication/deletion, backend validation, and a large YAML fallback.

The editor writes the existing Home Assistant config subentry; there is no separate frontend store. Changes remain a local draft until Save is pressed.

Configuration and Tools share one local draft, so moving between those pages preserves unsaved changes. Duplicate and Export are disabled until the draft is saved or reverted. Duplicate and versioned import/export copy configuration only. Parent provider credentials, memories, retained conversations, Knowledge content, and usage history are excluded. Export redaction is best effort: Function Tool definitions may still contain embedded credentials or sensitive values in arbitrary fields. Review exported files before sharing them. Import can create a new agent without discarding the current draft, or overwrite the current agent after validation and explicit confirmation that any dirty draft will be discarded.

## Speech cleanup

Speech post-processing affects TTS only. The original assistant message remains available to the UI, events, archive, and conversation context. Processing runs built-in Markdown/link cleanup, ordered custom regex replacements, whitespace cleanup, and trimming.

Example advanced rules:

```yaml
- pattern: '\[[0-9]+\]'
  replacement: ''
- pattern: '\bHA\b'
  replacement: 'Home Assistant'
```

Rules use standard Python regex syntax and run sequentially. Invalid rules are rejected on save and skipped defensively at runtime. Use the frontend speech preview to test the exact backend pipeline.

Conversation-agent options are available from the assistant/integration configuration in Home Assistant. Most users can start with the defaults and change only the model.

## Main options

| Option | Default | What it does |
| --- | --- | --- |
| **Prompt Template** | Included assistant prompt | Instructions and Home Assistant context sent to the model. Supports Home Assistant templates. |
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

See [Custom functions](functions/index.md).

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
