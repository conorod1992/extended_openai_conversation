# Configuration

Home Assistant's native integration flow focuses on the provider connection: API provider/key, base URL, API version, organisation, and authentication/bootstrap options. Create, remove, and rename conversation subentries with normal Home Assistant lifecycle controls.

Configure conversation-agent behaviour from **Extended OpenAI** in the sidebar:

- **Assistant** contains Basics, Model & responses, Conversation, Prompt & context, Voice & identity, and Speech.
- **Capabilities** contains Home Assistant & local handling, Web search & Skills, Request Rules, Functions/Groups, Quiet Hours, and Guest Mode.
- **Data & Memory** contains Memory settings, Memories, Knowledge Library, and Conversation history. **Usage & Maintenance** contains Usage, Request debugging, Backup & Restore, Diagnostics, and Usage data retention.
- **Guide** provides searchable, concise orientation and links back into the relevant management subsection. The global settings search navigates directly to settings across sections.

On desktop, section buttons appear above each grouped page. On smaller screens,
Page and section dropdowns provide the same navigation. **Search all settings**
can jump to settings across sections; collections such as Functions, Memories,
and Knowledge also have their own search. Configuration duplication and transfer
are grouped under **Agent actions**. Save and Revert appear only when the
settings draft is dirty. Changing agent behaviour requires an administrator.

The editor writes the existing Home Assistant config subentry; there is no separate frontend store. Changes remain a local draft until Save is pressed. Voice ownership is separate from spoken-response cleanup, dependent options stay visible but disabled, and Custom replacements remain directly visible while speech processing is enabled.

The **Prompt & context** section has default-on controls for Home Assistant-local date/time and exposed-device names/states on new or reset configurations. Their integration-maintained output is placed with volatile context after the indivisible user prompt. Optional advanced templates are collapsed by default and can be cleared to restore the maintained format. Custom prompts are preserved. Exact historical stock prompts, including the unchanged 6.8.3 default, migrate to the current default. Agents predating the separate context controls keep those generated blocks off when migrated, preventing duplicate legacy template output; existing saved choices are retained.

**Preview effective request** renders the locally inspectable content accompanying a brand-new message: production-ordered system/context, first-request custom tools, the Function Group loader/catalogue, integration and provider-hosted tools, and non-secret request settings. User input, conversation and tool-call history, query-derived persistent memories, credentials, and opaque provider framing are excluded. Counts are exact characters of the displayed text or canonical local JSON serialization; Function Group savings compare the grouped first request (including loader overhead) with all enabled custom schemas sent eagerly. Provider token/cache usage after real requests remains authoritative. Preview does not save, call the provider, record usage, load groups, or mutate memory/history.

For natural provider prompt-cache reuse, the user-authored prompt remains an indivisible rendered block, followed by stable integration-owned memory, temporary-memory, Knowledge, archive, and continuation guidance where enabled. Volatile retrieved persistent-memory and active temporary-memory context follows those stable sections. Tool schemas remain provider tool definitions, message roles retain their normal system/history/current-user order, and actual cache hits remain provider-controlled.

Function Tool YAML uses a clean single-tool shape with `spec:` and `function:` at the top level. The backend serializes existing tools to readable YAML, parses edits, validates the selected function type, and returns normalized metadata. The browser does not maintain a second JSON/Form representation or duplicate backend schema validation.

The Add Function Tool dialog includes **Insert Built-in Function**. Built-in functions are implemented directly by Extended OpenAI Conversation; selecting one inserts a complete, editable native Function Tool preset into the normal YAML editor and does not save it automatically. Already-configured native implementations are labelled in the picker.

Every Function Tool is enabled by default unless its YAML contains `enabled: false`. Disabled tools stay configured, searchable, editable, duplicable, deletable, and assigned to their Function Group, but are not sent to the model and cannot execute. Function saves, group saves, card switches, and confirmed deletions persist immediately. Administrators can also change the persisted state with `extended_openai_conversation_responses.enable_function_tools` and `extended_openai_conversation_responses.disable_function_tools`; these actions are intentionally not exposed to the model automatically.

Function Groups are optional metadata around that unchanged YAML. Existing ungrouped functions remain **Always available**. A **Load when needed** group initially sends only its compact name and description; the model requests relevant groups through the normal tool loop and their detailed schemas remain available for the active conversation. The first use may add one provider round-trip. No separate router/classifier call, keyword matching, or embeddings are used. Deleting a group never deletes its functions. See [Function groups](features/function-groups.md).

Settings subsections share one local draft, so moving among them preserves unsaved changes. Immediate Knowledge availability saves preserve unrelated edits in that draft. Resetting model parameters preserves Memory settings and selects reasoning effort supported by the current model. The Save/Revert bar is shown only while that draft is dirty. Functions and Function Groups save independently against the latest persisted configuration, while their saved fields are synchronized into the browser draft so unrelated unsaved settings remain intact. Duplicate and Export are disabled until genuine settings changes are saved or reverted. Duplicate and Shareable Setup configuration import/export copy configuration only. They exclude parent provider credentials, memories, retained conversations, Knowledge content, and usage history. Shareable Setup defaults to restricted content; including custom prompts, templates, Function Tools or other custom text requires review because arbitrary fields may contain secrets that redaction cannot recognize. Review exported files before sharing them. Import can create a new agent without discarding the current draft, or overwrite the current agent after validation and explicit confirmation that any dirty draft will be discarded.

The **Home Assistant access** page distinguishes Assist entity exposure from automatic prompt context. Home Assistant decides which exposed entities the assistant may use. The integration's separate **Include exposed entity states in the prompt** setting controls whether exposed names and current states are added automatically; turning it off does not necessarily prevent access through Home Assistant tools.

Legacy panel routes such as `/configuration`, `/tools`, `/guest`, `/memories`, `/knowledge`, `/conversations`, `/usage`, and `/diagnostics` continue to resolve to their corresponding new subsection.

## Backup & Restore

Under **Usage & Maintenance → Backup & Restore**, **Shareable Setup** copies supported configuration and Request Rules without private retained data; **Full Backup** preserves private configuration and all supported saved-data categories; **Custom** selects individual private backup sections. A full backup can contain the saved configuration, Request Rules, persistent memories, active temporary memories with their original absolute expiry, canonical Knowledge source text and metadata, retained archive sessions and turns, the Guest Mode schedule, plus lifetime, daily, request, and run usage data. New private backups preserve inline credentials and include the parent provider connection, including saved API keys and OAuth tokens, when configuration is selected. They are not encrypted. In-flight conversations, loaded Function Groups, caches, locks, installed Skill files, workspace files and other temporary runtime state are excluded. Historical backup versions that excluded parent credentials or redacted inline secrets retain their original import semantics.

Restore first validates the format and every category, drops temporary memories that have expired since the backup was created, and shows a summary. Review **Sections to replace**, then choose **Restore selected sections**. Choosing all offered sections replaces them all; unselected destination sections remain unchanged. Restores do not merge records or add usage totals. Cross-installation restores ask you to resolve missing user identities before applying personal data. Knowledge source data is restored and its derived local search index is rebuilt. If an agent storage write fails, the integration attempts to restore the pre-restore snapshot. Full backups can contain prompts, private memories, Knowledge content, archived conversations, and usage metadata, so store them securely. Restoring the provider connection requires an explicit shared-connection choice because other agents may use it; it runs after agent restoration and refuses to overwrite concurrent connection edits. A separate provider-connection failure does not undo already committed agent data. External Home Assistant scripts, files, Skills and services, or expired credentials, may still need attention after migration. See [Backup & Restore](features/backup-restore.md) for details.

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

Rules use standard Python regex syntax and run sequentially on the completed response in a bounded isolated worker. Because arbitrary patterns can depend on text that has not arrived yet, configuring any custom replacement disables progressive TTS for that agent. Invalid rules are rejected on save. At runtime, malformed or invalid persisted rules, timeouts, worker failures, oversized input, or excessive output growth cause the whole custom-replacement stage to fail open atomically to the pre-replacement speech text; a partial replacement result is never spoken. Use the frontend speech preview to test the exact completed-response pipeline.

Conversation-agent settings are available in the **Extended OpenAI** sidebar panel. Most users can start with the defaults and change only the model. The native Home Assistant conversation-agent configuration screen points here; AI Task retains a separate native configuration flow.

## Main options

| Option | Default | What it does |
| --- | --- | --- |
| **System prompt** | Included assistant prompt | User-authored behavioural instructions sent as one intact block under Prompt & context. Supports Home Assistant templates. |
| **Include current date & time** | On for new agents | Adds Home Assistant-local date/time as volatile integration context. |
| **Include exposed devices** | On for new agents | Adds compact exposed entity names, states, and genuine aliases as volatile integration context. |
| **Chat model** | `gpt-5-mini` | Model or Azure deployment name sent to the configured provider. |
| **Provider API format** | Auto | Chooses Chat Completions or Responses; under Basics → Advanced request settings. |
| **Listen for a follow-up** | HA Default | Controls immediate voice follow-ups. |
| **Web search** | Off | Enables OpenAI hosted Web Search when supported. |
| **Web search detail** | Low | Controls Web Search retrieval breadth. |
| **Long-term memory** | Off | Enables Manual or Automatic persistent memory. |
| **Allow the assistant to use Knowledge** | Off | Enables on-demand model search and retrieval of maintained per-agent Knowledge sources. |
| **Automatically include memories** | 3 | Maximum memories selected from the opening message and reused for that conversation. |
| **Maximum response length (tokens)** | 500 | Limits model output tokens, including reasoning tokens where the provider counts them against output. |
| **Tool-call limit per request** | 10 | Limits model-requested Function Tool executions within one turn; resets for the next turn. |
| **Skills** | None on initial setup; available Skills selected when adding an agent | Lists installed instruction sets the agent may load, one installed directory name per line. |
| **Functions** | Included Home Assistant tools | Defines tools available to the model. |
| **Function groups** | None | Optionally keeps related function schemas always available or loads them only when needed. |
| **Trim history after (input tokens)** | 40000 | Input-token threshold for history trimming, using provider usage when available and a conservative local estimate otherwise. |
| **Conversation history trimming** | Keep recent messages for new agents | Decides what happens when context grows beyond the threshold. |

## API mode

### Auto

Uses the active model catalogue to choose an endpoint compatible with the selected model, reasoning setting, and required tools. It does not probe an endpoint or retry through another API after a provider error. Unknown model IDs use conservative capabilities and choose Responses for text-only Auto requests; selecting a familiar model family name does not establish support for an unknown ID.

For most users, **Auto** is the recommended starting point.

### Chat Completions

Uses `/v1/chat/completions` when the model catalogue allows the configured capabilities.

Choose this when:

- your provider supports Chat Completions but not Responses
- you are troubleshooting Responses compatibility
- you intentionally need legacy endpoint behavior

### Responses

Uses `/v1/responses` when the model catalogue allows the configured capabilities.

Responses mode supports integration features such as streaming, Home Assistant functions, sequential tool calls, image/PDF inputs, structured outputs, reasoning effort, and service tiers when the selected model/provider supports them.

New attachments are validated strictly. On later turns, an unavailable file or an attachment unsupported by the selected API (such as a historical PDF after switching to Chat Completions) is omitted with a provider-visible notice, preserving the earlier text and answer. Current uploads take priority over retained attachments within the existing per-file, combined-byte and attachment-count limits; historical attachment metadata is kept so usable files can be replayed on a later request.

Responses can return several final-answer messages. Conversation speech, AI Task output and the optional archive combine those messages while excluding commentary; native message identities and phases remain intact in provider history.

Do not force Responses with a custom provider unless it implements a compatible `/v1/responses` endpoint.

See [Responses API](features/responses-api.md).

## Continue conversation

**Listen for a follow-up** only controls responses handled by ExtendedOpenAI. Home Assistant sentence triggers and native local handling may intercept a request before ExtendedOpenAI is invoked; their follow-up behavior remains controlled by Home Assistant.

Always also applies to successful local intents and consumed Request Rules inside ExtendedOpenAI. Failed local handling does not request follow-up listening. These local responses keep listening disabled in HA Default and Conditional modes; Conditional requires an actual model continuation decision.

- **HA Default** — preserve Home Assistant's normal follow-up behavior.
- **Always** — request another utterance after every successful response.
- **Conditional** — allow the model to signal when its answer expects an immediate reply.

The voice client must support Home Assistant's `continue_conversation` signal. See [Voice follow-ups](features/voice-followups.md).

## Web Search

Web Search is available only when the configured request path supports OpenAI's hosted Web Search tool. It is intended for current public information rather than Home Assistant state.

**Web search detail** controls retrieval breadth rather than an exact number of results or tokens. Higher settings can increase latency and API cost.

See [Web Search](features/web-search.md).

## Memory

- **Off** — disables persistent-memory tools and automatic retrieval. Existing records remain stored and manageable until you delete them.
- **Manual** — store facts only after an explicit remember request.
- **Automatic** — allow proactive storage of stable useful facts under the integration's memory rules.

**Automatically include memories** can be set from 0 to 10. Set it to `0` when you want the model to retrieve memories only through memory tools.

See [Persistent memory](features/persistent-memory.md).

The selected bundle is reused for the conversation (`0` is tool-only). **How memories are matched** defaults to local Keyword matching. Optional **Keyword + meaning** sends memory content/metadata and memory search queries, including the opening message and later memory-tool searches, to the configured provider's embeddings endpoint, which can incur API charges; it falls back to keyword matching when unsupported. The adjacent Embedding model setting applies only to Keyword + meaning.

## Knowledge Library

Under **Data & Memory → Knowledge Library**, turn on **Allow the assistant to use Knowledge** to expose the built-in `knowledge_search`, `knowledge_list`, and `knowledge_get` tools when the agent has an enabled source. This availability switch saves immediately. Sources remain stored and editable while the feature is off. Knowledge is independent of persistent-memory mode, and source contents are never injected wholesale into the normal system prompt.

See [Knowledge Library](features/knowledge-library.md).

## Guest Mode

The **Guest Mode** page is the single policy editor. Compact managers for Labels,
Areas, Domains, and Individual entities keep large exclusion sets out of the main
page; each manager expands to the native Home Assistant selector. Guests begin with entities
normally exposed to Assist, then entity, domain, area, and label exclusions are
subtracted. The advanced **Make some visible entities read-only** option reveals
the same managers for additional control-only exclusions, which can only narrow
the readable set.
Knowledge and Functions support Off, On, or Custom selection; shared-household
memory supports Off, Read only, or Read & write. Personal memory and owner
archives remain unavailable. **Allow the assistant to activate Guest Mode** only
exposes the model's one-way restriction tool; turning it off does not end,
shorten, or weaken an active or scheduled restriction. See
[Guest Mode](features/guest-mode.md).

## Direct processing action

`extended_openai_conversation_responses.process` is mainly for automations and sentence triggers. It bypasses Home Assistant's outer conversation routing, but uses the selected Extended OpenAI agent's complete normal pipeline.

```yaml
action: extended_openai_conversation_responses.process
data:
  text: "Turn off the kitchen light"
response_variable: result
```

The response contains final `response` text, the `conversation_id`, and `handled_locally`. Optional agent, conversation, device, satellite, and language fields preserve available context. The Request Rules page can run the same real path from **Test request**. Testing can perform Home Assistant actions; it is not a dry run.

## Context management

When a conversation becomes large enough to cross the configured threshold, choose one of the available strategies:

- **Keep recent messages** — removes oldest complete user turns while keeping tool calls and tool results together.
- **Clear all messages** — legacy behavior that starts again without prior conversation history.
- **Summarize older messages** — makes a bounded summary request, keeps recent raw turns, and falls back to keeping recent messages if summarization fails.

See [Context management](features/context-management.md).

## Functions

The default Functions configuration provides Home Assistant tools. Basic exposed-entity control does not require you to create a custom function.

Customize Functions when you want to give the assistant an additional tool or change the available function definitions.

For agents with many functions, create Function Groups from **Capabilities → Functions** and choose **Load when needed** to reduce repeated schema input. Groups and assignments are preserved by agent duplication and import/export, while legacy configurations require no changes.

See [Custom functions](functions/index.md) and [Function groups](features/function-groups.md).

## Advanced options

Model parameters live under **Assistant → Model & responses**. The catalogue determines the available values; unsupported controls may remain visible but disabled. **Advanced request settings** under Basics contains provider format, Azure underlying model, tool-call limit and safe tool-error recovery.

### Temperature and Top P

Control response variation for models that support these parameters. Avoid changing both unless you understand the interaction between them.

### Reasoning effort

The available reasoning values depend on the exact model and API. They can include **None**, **Minimal**, **Low**, **Medium**, **High**, **Xhigh**, or **Max** where supported; use the choices shown for your model rather than assuming every level is available.

Higher effort can improve difficult reasoning tasks but may increase latency and token usage.

### Service tier

**Processing tier** lists the values supported by the selected model catalogue entry. Availability, entitlement and pricing still depend on the provider and account.

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

## Refreshing model data

Model capability metadata can be refreshed independently of the integration. Use **Update model data** or **Use bundled model data** under Model parameters. See [Model data catalogue](features/model-data.md) for the automatic update cadence, fallback, and storage details.


### Agent diagnostics for text-only configurations

The agent test sends a minimal text request. It includes a no-op Function schema only when the agent requires Function Calling, with tool execution disabled. Text-only configurations omit Function schemas and report Function Calling as **Skipped — Not required by this agent**. A failed text probe does not report a Function Calling failure.
