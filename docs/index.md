# Extended OpenAI Conversation (Responses)

Bring a more capable AI conversation agent to Home Assistant.

Extended OpenAI Conversation connects Home Assistant Assist to the OpenAI API, or a compatible provider, and adds richer Home Assistant control, memory, web search, local request handling, custom tools, voice features, privacy controls, diagnostics and more.

The project began as a fork of [jekalmin/extended_openai_conversation](https://github.com/jekalmin/extended_openai_conversation), but has since diverged substantially in features, architecture, configuration, testing and user interface.

## What it can do

You can start with a simple conversation agent and add more features only when you need them.

Common capabilities include:

- Control supported Home Assistant entities through natural-language requests.
- Use Responses API or Chat Completions with compatible providers.
- Search the web for current public information when supported.
- Continue conversations while keeping long context bounded.
- Store short-lived or persistent memories.
- Search larger local reference material through the Knowledge Library.
- Clean Markdown, citations and URLs from spoken output without replacing the original retained response.
- Apply Quiet Hours to lower Assist satellite volume and optionally suppress wake-word sounds on a daily schedule.
- Handle predictable phrases locally or route requests with Request Rules.
- Define custom Function Tools, including durable delayed execution, and organize large tool collections with optional on-demand Function Groups.
- Load reusable Skills per conversation agent.
- Apply Guest Mode as a backend-enforced visitor restriction layer.
- Track usage, inspect requests, run diagnostics, refresh model capability data, and create full private agent backups.

You do **not** need to configure all of these features. For most users, the best path is: install the integration, connect a provider, select the conversation agent in Assist, expose a small number of Home Assistant entities, and test basic operation before enabling extras.

## Start here

- [Installation](getting-started/installation.md)
- [Get an OpenAI API key](getting-started/api-key.md)
- [First setup](getting-started/setup.md)
- [Configuration](configuration.md)
- [Troubleshooting](troubleshooting.md)

## Find the right guide

- [Responses API](features/responses-api.md) and [Web Search](features/web-search.md)
- [Conversation continuity](features/conversation-continuity.md) and [context management](features/context-management.md)
- [Persistent memory](features/persistent-memory.md), [temporary memory](features/temporary-memory.md), [Knowledge Library](features/knowledge-library.md), and [conversation archive](features/conversation-archive.md)
- [Voice Identity](features/voice-identity.md), [voice follow-ups](features/voice-followups.md), [speech processing](features/speech-processing.md), and [Quiet Hours](features/quiet-hours.md)
- [Request Rules](features/request-rules.md), [Function Groups](features/function-groups.md), [delayed Function Tools](features/delayed-function-tools.md), and [custom functions](functions/index.md)
- [Guest Mode](features/guest-mode.md), [model data](features/model-data.md), [usage statistics](features/usage-statistics.md), [request debugging](features/request-debugging.md), and [Backup & Restore](features/backup-restore.md)

The left-hand documentation navigation is organised by the kind of task you are trying to accomplish: Assistant behaviour, Voice & Speech, Home Assistant access, Automation & Tools, Memory & Data, Usage & Maintenance, Functions, Skills, and Help & Reference.

## Permissions and privacy

Home Assistant Assist exposure determines which normal entities are available to the assistant, but it does not grant control by itself. The Home Assistant user making the request must also have the required permission, and Extended OpenAI features such as Guest Mode can restrict access further.

When model processing is needed, relevant request text, prompts/context, tool definitions and retrieved memory or Knowledge content may be sent to the provider you configure. Provider-side retention and processing are governed by that provider's policies.

Memories, Knowledge Library content, Request Rules, optional archived conversations, usage data and related integration state are stored locally in Home Assistant. Full backup files can contain sensitive retained data even though provider credentials are excluded, so treat them as private.

## Reliability

The integration is exercised through unit/integration tests, genuine Home Assistant acceptance tests and selected browser tests against Home Assistant. Upgrade-from-a-previous-release and cross-browser smoke paths are also covered for important management/runtime flows.

No test suite can guarantee identical behaviour across every provider, custom Function Tool or Home Assistant installation, but the project deliberately tests real integration boundaries rather than only isolated helpers.
