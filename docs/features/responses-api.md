# Responses API

The integration supports both OpenAI's Chat Completions API and the newer Responses API while keeping Home Assistant conversation history and function execution integrated with the same conversation agent.

## Which mode should I use?

For most users, leave **API mode** set to **Auto**.

- **Auto** selects the integration's appropriate endpoint for the configured model.
- **Chat Completions** always uses `/v1/chat/completions`.
- **Responses** always uses `/v1/responses`.

Use a forced mode mainly for provider compatibility, testing, or a known model requirement.

## What Responses mode adds

Depending on the model/provider, Responses mode can support:

- streaming responses
- Home Assistant function calls
- sequential tool calls
- image input
- PDF input
- structured outputs
- reasoning effort
- service tiers
- OpenAI hosted Web Search

A feature being available in Responses mode does not mean every model supports it. The options UI hides some model-specific settings when they are not applicable.

## Custom providers

A custom OpenAI-compatible provider must implement a compatible `/v1/responses` endpoint before you force **Responses** mode.

Many providers implement `/v1/chat/completions` without implementing the Responses API. In that situation, use Chat Completions even if the provider otherwise follows the OpenAI API format.

## Azure OpenAI

Azure/provider support varies by endpoint and deployment. OpenAI-hosted Web Search in this integration is specifically limited to direct OpenAI Responses usage.

See [Compatibility](../reference/compatibility.md) for a concise capability matrix.

## Tool calls

Responses mode keeps tool calls and their results as native conversation items. The integration also protects context truncation from splitting a tool call away from its corresponding result.

This matters particularly in long Home Assistant conversations where a model may call several services or functions before answering.

## Reasoning effort

For supported reasoning models, the options UI exposes **Low**, **Medium**, or **High** reasoning effort.

Reasoning effort is not a general quality slider. Higher settings can be useful for difficult planning or reasoning tasks, but they can also increase response time and token use.
