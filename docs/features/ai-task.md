# AI Task

The integration can provide Home Assistant AI Task agents in addition to conversation agents.

AI Task is intended for model-backed tasks that do not need the full interactive conversation feature set.

## Configuration

AI Task agents use a deliberately smaller options screen focused on:

- completion model
- API mode
- maximum output tokens
- optional Web Search
- supported advanced model settings

Conversation-only features are not shown for AI Task agents.

## Web Search

AI Task agents can optionally use OpenAI's native Web Search tool. Enable **Web search** in the AI Task agent's normal Home Assistant configuration dialog.

Web Search uses the same provider path as conversation agents and remains disabled by default. It requires the direct OpenAI Responses API; Azure, custom base URLs, and Chat Completions do not support the hosted tool.

This works with both free-text and structured AI Task output, so an automation can ask for current information and still receive its normal requested result shape.

Structured output requires objects with named fields. A free-form object selector that accepts arbitrary keys is rejected before contacting the provider, because strict Structured Outputs cannot preserve that contract. The integration does not silently discard arbitrary keys by closing the object schema.

Strict output also requires an object at the root, without root-level `anyOf`, and does not support `allOf` intersections. These caller schemas are rejected before contacting the provider; nested `anyOf` alternatives inside named fields remain supported. See [OpenAI’s Structured Outputs schema contract](https://developers.openai.com/api/docs/guides/structured-outputs).

## Features not available in AI Task options

The AI Task configuration does not expose conversation-specific controls such as:

- voice follow-ups
- persistent memory
- skills
- custom Functions

This keeps AI Task configuration focused on generating task output rather than maintaining an interactive assistant session.

## API mode

Like conversation agents, AI Task agents can use the configured Chat Completions or Responses path according to the selected mode and provider compatibility.

For provider limitations, see [Compatibility](../reference/compatibility.md).
# Azure deployments and structured output

For an Azure deployment with a custom name, keep the deployment name in **Model**
and enter its actual model ID in **Azure underlying model**. For example, use
`ha-production` and `gpt-4.1`. Requests use the deployment name; capability checks
use the underlying model. This setting is also available in the assistant's
advanced request settings. Other providers do not use the Azure binding.

Strict structured outputs normalize nested `oneOf` only when alternatives have
disjoint types or a required discriminator with disjoint values. Ambiguous
alternatives are rejected before submission. Caller validation remains
authoritative when removing optional null placeholders from a task result.
