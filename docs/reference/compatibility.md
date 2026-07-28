# Compatibility

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
| Image/PDF input | Responses/model-dependent | Provider-dependent | Provider/deployment-dependent |
| Persistent memory | ✓ | ✓ | ✓ |
| Voice follow-ups | ✓ | ✓ | ✓ |
| Skills | ✓ | ✓ | ✓ |

The last three features are implemented primarily by the Home Assistant integration rather than by a special hosted OpenAI endpoint, although selected memory/skill context is sent to the configured model when used.

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

Some compatible providers have stricter tool-call identifier requirements. The **Shorten tool call IDs** advanced option creates 9-character IDs for those deployments.

Leave it disabled for OpenAI unless you have a concrete compatibility reason to change it.
