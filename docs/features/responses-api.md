# Responses API

The integration supports both OpenAI's Chat Completions API and the newer Responses API while keeping Home Assistant conversation history and function execution integrated with the same conversation agent.

## Which mode should I use?

For most users, leave **Provider API format** (API mode) set to **Auto** under **Assistant → Basics → Advanced request settings**.

- **Auto** selects an endpoint from the active catalogue using the exact model, reasoning setting and required tools.
- **Chat Completions** selects `/v1/chat/completions`.
- **Responses** selects `/v1/responses`.

Use a forced mode mainly for provider compatibility, testing, or a known model requirement.

All modes validate supported capabilities before sending. Auto does not discover a provider's capabilities or automatically retry a failed request through another API. An unknown model ID has conservative capabilities; text-only Auto requests use Responses, and function calling is unavailable without explicit model metadata. See [Compatibility](../reference/compatibility.md).

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

A feature being available in Responses mode does not mean every model supports it. The management UI uses model metadata to show supported values and explain unavailable controls. Streaming, function calls, images, structured outputs and some reasoning controls are also available through Chat Completions where supported; PDF input and OpenAI hosted Web Search require Responses in this integration.

## Custom providers

A custom OpenAI-compatible provider must implement a compatible `/v1/responses` endpoint before you force **Responses** mode.

Many providers implement `/v1/chat/completions` without implementing the Responses API. In that situation, use Chat Completions even if the provider otherwise follows the OpenAI API format.

## Azure OpenAI

Azure/provider support varies by endpoint and deployment. OpenAI-hosted Web Search in this integration is specifically limited to direct OpenAI Responses usage.

For Azure, **Chat model** is the deployment name. If it differs from the underlying model ID, set **Azure underlying model** under Advanced request settings so capability checks use the actual model while requests continue using your deployment name.

See [Compatibility](../reference/compatibility.md) for a concise capability matrix.

## Tool calls

Responses mode keeps tool calls and their results as native conversation items. The integration also protects context truncation from splitting a tool call away from its corresponding result.

This matters particularly in long Home Assistant conversations where a model may call several services or functions before answering.

## Reasoning effort

Under **Assistant → Model & responses**, reasoning choices depend on the exact model and API. Supported values can include **None**, **Minimal**, **Low**, **Medium**, **High**, **Xhigh**, or **Max**. Some models permit tool calling or sampling controls only with particular reasoning settings.

Reasoning effort is not a general quality slider. Higher settings can be useful for difficult planning or reasoning tasks, but they can also increase response time and token use.

Reasoning tokens may count against the model's output limit. If a reasoning model repeatedly ends without a useful answer, review **Maximum response length (tokens)** as well as its effort setting.
