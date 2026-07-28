# Usage statistics

Each conversation agent can expose a disabled-by-default diagnostic **Usage** sensor with cumulative provider-reported request and token statistics.

## Enable the sensor

Open the device page for the conversation agent and enable the diagnostic Usage entity.

A single sensor is used rather than creating many usage entities.

## What it reports

The sensor state is cumulative total tokens. Attributes can include:

- Home Assistant conversation count
- API request count
- successful API requests
- failed API requests
- input tokens
- output tokens
- total tokens
- cached input tokens when reported
- reasoning tokens when reported
- additional numeric input/output usage fields returned by the provider

## Conversations versus API requests

One Home Assistant conversation can generate several API requests.

For example, the model may call a Home Assistant tool, receive the result, and then make another model request before producing the final answer. Each provider request contributes its own usage.

Context-summary requests and the minimal model request made by **Test agent** are also included.

## Provider limitations

Counters use usage metadata actually returned by the configured model provider.

OpenAI-compatible providers may omit some or all token fields. When that happens, the integration can still count the request and whether it succeeded or failed, while unavailable token counters remain unchanged.

## Persistence

Usage counters are stored using Home Assistant's versioned `.storage` API separately for each conversation-agent subentry.

They survive Home Assistant restarts and integration reloads.

!!! warning "Token usage is not a bill"
    The integration does not estimate monetary cost. Pricing can vary by provider, model, service tier, caching, hosted tools, and account terms. Use provider billing data for actual cost.
