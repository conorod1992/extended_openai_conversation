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
