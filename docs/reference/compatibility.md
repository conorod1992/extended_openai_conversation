# Compatibility

**Home Assistant 2026.7.0 or newer is required.** Earlier releases have incompatible
dependency constraints. CI checks requirement installation and runtime behaviour
against the minimum supported Home Assistant version.

Feature availability depends on the configured provider, endpoint, and model.

## Capability matrix

| Capability | Direct OpenAI | Custom OpenAI-compatible provider | Azure OpenAI |
| --- | :---: | :---: | :---: |
| Chat Completions | ✓ | If implemented | ✓ |
| Responses API | ✓ | If implemented | Provider/deployment-dependent |
| Home Assistant functions | ✓ | If tool calling is compatible | ✓ where tool calling is supported |
| Web Search | ✓, Responses only | — | — in this integration |
| Reasoning effort | Model-dependent | Provider/model-dependent | Provider/model-dependent |
| Service tier | Account/model-dependent | Provider-dependent | Provider-dependent |
| Image input | Model/API-dependent | Provider/model-dependent | Provider/deployment-dependent |
| PDF input | Responses/model-dependent | Compatible Responses required | Provider/deployment-dependent |
| Persistent memory | ✓ | Compatible model/tools required | Compatible model/tools required |
| Voice follow-ups | Compatible voice client required | Compatible voice client required | Compatible voice client required |
| Skills | Tool-capable model required | Compatible model/tools required | Compatible model/tools required |

The last three features are implemented primarily by the Home Assistant integration rather than by a special hosted OpenAI endpoint, although selected memory/skill context is sent to the configured model when used. Conditional voice follow-ups also need model tool calling; Always and HA Default do not require a model continuation decision.

## Model IDs and API selection

The integration validates requests using an exact-ID model catalogue, not model-name prefixes. A provider can implement an endpoint while the selected model still lacks a compatible tool, reasoning, streaming or output-token contract.

Unknown and custom IDs are retained as entered, but use conservative capabilities: text-only Auto requests use Responses, and unknown models are not assumed to support Function Tools, Skills or hosted Web Search. For a text-only custom endpoint that implements only Chat Completions, select that format explicitly, set **Tool-call limit per request** to `0`, and turn off tool-dependent capabilities, including selected Skills and Conditional follow-ups. A positive limit requires tool support even when no custom Function Tools are configured. For tool use, confirm that both the active catalogue metadata and the provider support the exact model and request format; changing API format alone does not add tool support.

For Azure, enter the deployment name as **Chat model**. When that is not a catalogued model ID, set **Azure underlying model** under **Assistant → Basics → Advanced request settings** to the actual model ID. This selects capability metadata without replacing the deployment name sent to Azure. Leave this field blank for other providers.

See [Model data](../features/model-data.md) for catalogue updates and [Responses API](../features/responses-api.md) for API selection. Auto chooses a supported path before sending; it does not probe a provider or retry an error through the other API.

## Base URL

Leave **Base URL** unchanged for direct OpenAI usage.

Set a custom Base URL only when your compatible provider requires it.

A provider calling itself “OpenAI-compatible” may support only part of the OpenAI API. In particular, support for `/v1/chat/completions` does not imply support for `/v1/responses`.

## Recommended troubleshooting order

When a provider does not work:

1. confirm the provider's documented model name
2. start with **Chat Completions** if Responses compatibility is uncertain
3. disable optional hosted features such as Web Search
4. test the agent from the integration UI
5. inspect Home Assistant logs
6. only then add model-specific advanced settings

## Web Search

The integration's OpenAI hosted Web Search integration is specifically for direct OpenAI Responses requests. It is not exposed for Chat Completions, custom Base URLs, or Azure OpenAI.

## Short tool-call IDs

Some compatible providers have stricter tool-call identifier requirements. **Use shorter tool-call IDs** under **Assistant → Model & responses** creates 9-character IDs for those deployments.

Leave it disabled for OpenAI unless you have a concrete compatibility reason to change it.

## Streamed tool identities

Chat Completions tool calls are accumulated by their `index`. Nonempty `id` and
`function.name` deltas are appended literally, just like argument fragments;
repeated or overlapping fragments are preserved even after the arguments form
complete JSON. Providers that send a full identity normally send it once, as in
[OpenAI's streaming function-call examples](https://developers.openai.com/api/docs/guides/function-calling#streaming).

Cumulative snapshots or repeated full identities on later chunks cannot be
reliably distinguished from legitimate fragments. Compatible endpoints must
emit identity deltas rather than such snapshots; the integration does not guess
or deduplicate them. Responses API identity events retain their separate rules.
