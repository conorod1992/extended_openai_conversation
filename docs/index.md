# Extended OpenAI Conversation (Responses)

**Extended OpenAI Conversation (Responses)** is a Home Assistant conversation integration for OpenAI and compatible providers. It combines Home Assistant tool use with modern OpenAI features such as the Responses API, Web Search, persistent memory, reasoning controls, and richer conversation management.

The project began as a fork of [jekalmin/extended_openai_conversation](https://github.com/jekalmin/extended_openai_conversation), but has since diverged substantially while retaining the original project's flexible custom-function framework.

## What can it do?

- Control exposed Home Assistant entities and call Home Assistant services.
- Query entity history and use it in answers.
- Use either Chat Completions or the Responses API.
- Search the web through OpenAI's hosted Web Search tool when supported.
- Remember selected facts across conversations.
- Keep voice satellites listening when an immediate follow-up is expected.
- Manage long conversation context by retaining or summarizing older turns.
- Load reusable skills per conversation agent.
- Define custom tools using Home Assistant scripts, templates, REST requests, scraping, composites, SQLite, and native functions.
- Track provider-reported request and token usage.
- Test an agent's configuration from the Home Assistant UI.
- Provide model-backed AI Task agents.

## Start here

New to the integration? Follow these two guides in order:

1. [Install the integration](getting-started/installation.md)
2. [Complete first setup](getting-started/setup.md)

After that, the [configuration guide](configuration.md) explains the main options and links to detailed feature guides.

## Common examples

Once your entities are exposed and the appropriate features are enabled, you can ask things like:

> Turn off everything downstairs except the hallway light.

> Was the kitchen window left open overnight?

> Remember that I prefer temperatures in Celsius.

> What's the latest Home Assistant release?

> Create an automation that turns on the porch light when I arrive home after sunset.

What the assistant can actually do depends on the model, provider, exposed entities, functions, and enabled features.

## New to OpenAI APIs?

A ChatGPT subscription is not an OpenAI API subscription. This integration sends requests to the configured API provider and API usage may be billed separately by that provider.

For direct OpenAI usage, create an API key through the OpenAI platform and keep it private.

## Next steps

- [Configuration](configuration.md)
- [Responses API](features/responses-api.md)
- [Web Search](features/web-search.md)
- [Persistent memory](features/persistent-memory.md)
- [Voice follow-ups](features/voice-followups.md)
- [Custom functions](functions/index.md)
- [Troubleshooting](troubleshooting.md)
