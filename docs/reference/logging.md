# Logging

Home Assistant logging can help diagnose provider errors, function failures, unexpected conversation behavior, and integration setup issues.

## Enable debug logging

Add the following to `configuration.yaml`:

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

Debug logs can be verbose and may contain request/response details or Home Assistant context that you would not normally share publicly.

Before posting logs in an issue:

- remove API keys, tokens, credentials, and private URLs
- review entity names and state data for personal information
- redact conversation text if necessary

Do not leave verbose debug logging enabled indefinitely unless you need it.
