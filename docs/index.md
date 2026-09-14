# Extended OpenAI Conversation (Responses)

**Extended OpenAI Conversation (Responses)** is a Home Assistant conversation integration for OpenAI and compatible providers. It combines Home Assistant entity and service access with modern model APIs, Web Search, memory, voice features, custom Function Tools, privacy controls, and a dedicated management interface.

The project began as a fork of [jekalmin/extended_openai_conversation](https://github.com/jekalmin/extended_openai_conversation), but has since diverged substantially while retaining the original project's flexible custom-function framework.

## What can it do?

- Control Assist-exposed Home Assistant entities and call services, subject to the requesting Home Assistant user's permissions and any stricter Extended OpenAI policy.
- Use either Chat Completions or the Responses API with model-aware response and reasoning controls.
- Search current public information through supported hosted Web Search.
- Use persistent memory, expiring temporary memory, a Knowledge Library, and retained conversation archives for different kinds of reusable context.
- Continue recent conversations and trim or summarize older context when conversations grow large.
- Map voice devices to Home Assistant users, shared-household data, or no-personal-data scopes with Voice Identity.
- Keep voice satellites listening when an immediate follow-up is expected.
- Clean Markdown, citations and URLs from spoken output without replacing the original retained response.
- Apply Quiet Hours to lower Assist satellite volume and optionally suppress wake-word sounds on a daily schedule.
- Handle predictable phrases locally or route requests with Request Rules.
- Define custom Function Tools, including durable delayed execution, and organize large tool collections with optional on-demand Function Groups.
- Load reusable Skills per conversation agent.
- Apply Guest Mode as a backend-enforced visitor restriction layer.
- Track usage, inspect requests, run diagnostics, refresh model capability data, and create full private agent backups.
- Provide model-backed AI Task agents.

## Start here

1. [Install the integration](installation.mdx)
2. [Configure the assistant](configuration.mdx)
3. Review [Home Assistant access and permissions](features/home-assistant-access.md), especially for multi-user or shared-voice installations.

The GitHub Pages navigation is the canonical user-facing feature manual. The in-app Guide provides shorter contextual help and links users back to the relevant management areas.

## Feature guides

Useful starting points include:

- [Responses API](features/responses-api.md) and [Web Search](features/web-search.md)
- [Conversation continuity](features/conversation-continuity.md) and [context management](features/context-management.md)
- [Persistent memory](features/persistent-memory.md), [temporary memory](features/temporary-memory.md), [Knowledge Library](features/knowledge-library.md), and [conversation archive](features/conversation-archive.md)
- [Voice Identity](features/voice-identity.md), [voice follow-ups](features/voice-followups.md), [speech processing](features/speech-processing.md), and [Quiet Hours](features/quiet-hours.md)
- [Request Rules](features/request-rules.md), [Function Groups](features/function-groups.md), [delayed Function Tools](features/delayed-function-tools.md), and [custom functions](functions/overview.mdx)
- [Guest Mode](features/guest-mode.md), [model data](features/model-data.md), [usage statistics](features/usage-statistics.md), [request debugging](features/request-debugging.md), and [Backup & Restore](features/backup-restore.md)

## Permissions and privacy

Assist exposure controls which normal Home Assistant entities are available to conversation agents, but exposure does not by itself grant every user control permission. Model-driven actions are also checked against the requesting Home Assistant user's permissions. Guest Mode and other Extended OpenAI policies can narrow access further; they cannot elevate access that Home Assistant has denied.

For shared voice devices, configure Voice Identity deliberately so personal memories and retained conversations are not attached to the wrong speaker merely because several people use the same satellite.

## Reliability

Testing includes unit/integration coverage, genuine Home Assistant acceptance tests, browser-to-Home-Assistant tests, upgrade testing from previously published releases, and selected cross-browser checks. Runtime-sensitive areas such as authorization, entity/service lifecycle changes, backup restoration, conversation ownership, and delayed execution have dedicated real-Home-Assistant coverage.

## OpenAI API billing

A ChatGPT subscription is not an OpenAI API subscription. This integration sends requests to the configured API provider and API usage may be billed separately by that provider. Keep provider credentials private.
