# Logging

Home Assistant logging can help diagnose provider errors, function failures, unexpected conversation behavior, and integration setup issues.

## Enable debug logging

For ordinary troubleshooting, use Home Assistant's UI:

1. Open **Settings → Devices & services → Extended OpenAI Conversation → Enable debug logging** (in the integration's menu).
2. Reproduce the problem.
3. **Disable debug logging** in the same menu. Home Assistant then offers the log download.

HA debug logging captures integration operational logs: stages, counts, timings, failure categories, tool names and safe stack locations. It does not automatically enable EOAI's **Request debugging** feature.

For deeper per-request inspection of prompts, tool arguments and provider responses, explicitly enable [Request debugging](../features/request-debugging.md) in EOAI. Enabling Request debugging does not automatically enable HA debug logging either. Treat its captures as private conversation data.

Advanced users can alternatively configure the integration logger in `configuration.yaml`:

```yaml
logger:
  logs:
    custom_components.extended_openai_conversation_responses: debug
```

Restart or reload the relevant Home Assistant configuration as required.

## When to use it

Debug logging is particularly useful when:

- an API request fails
- a custom provider returns an unexpected response
- a tool call is rejected or fails
- a custom function does not behave as expected
- the agent works in one API mode but not another

## Privacy

Review downloaded logs before sharing them publicly. Home Assistant debug logging is **not a guarantee of automatic redaction**: other integrations and Home Assistant itself may include private data. EOAI operational logging avoids full provider events, response text, tool arguments, rendered SQL and scraped content, but safe identifiers can still reveal information about your installation.

Before posting logs in an issue:

- remove API keys, tokens, credentials, and private URLs
- review entity names and state data for personal information
- redact conversation text if necessary

Do not leave verbose debug logging enabled indefinitely unless you need it.
