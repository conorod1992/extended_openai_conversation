# FAQ and help

## Why does the agent not control an entity?

Expose the entity to Assist in Home Assistant and confirm that the relevant user has permission. Ordinary exposed-entity control uses the built-in Home Assistant path; a custom Function Tool is not required. See [Home Assistant access](../features/home-assistant-access.md) and [exposed entity attributes](../features/exposed-entity-attributes.md).

## Why did a Function Tool not run?

In **Extended OpenAI → Capabilities → Functions**, check that the individual Function Tool is enabled and that its Function Group is enabled. A **Load when needed** group is initially represented by a compact catalogue entry; the model must load it before calling one of its functions. Inspect [Request debugging](../features/request-debugging.md) for the effective tool set and errors.

For service calls, use valid Home Assistant action names and target entities that are exposed and authorized. Test the underlying action in Home Assistant first, then use a small Function Tool wrapper. See [Function Tools](../functions/index.md).

## How should I troubleshoot provider errors?

Confirm the API key, selected provider, and Base URL. Then check whether the provider supports the selected model and API mode. “OpenAI compatible” does not guarantee support for the Responses API or every tool feature; select **Chat Completions** when the configured provider does not implement `/v1/responses`.

For direct OpenAI Web Search, use API mode **Responses**, a direct OpenAI Base URL, and a model that supports the hosted tool. Web Search is not a custom Google-search Function Tool requirement. See [Web Search](../features/web-search.md).

## Why is a local model not calling tools?

Tool calling depends on the model and provider's support for structured tool calls. Check the request diagnostics and provider documentation. Try a model with explicit tool support and confirm the function is available in the effective request.

## Why is context being shortened?

Check **Context Threshold** and **Context truncation strategy**. The threshold uses provider-reported input-token usage; a provider that omits usage metadata cannot reliably trigger the threshold. The configured strategy can keep recent turns, clear older context, or summarize older turns. See [Context management](../features/context-management.md).

## Why are token totals incomplete?

Usage depends on metadata returned by the provider. Some compatible providers omit token counters. The integration records request outcomes while unavailable token counters remain unknown. Token totals are not a monetary-cost estimate. See [Usage](../features/usage-statistics.md).

## Where can I find errors?

Use **Extended OpenAI → Request debugging** to inspect recent request stages and diagnostics. For Home Assistant logs, enable debug logging for `custom_components.extended_openai_conversation_responses`; see [Logging](../reference/logging.md). Redact credentials, private URLs, entity data, and conversation text before sharing logs.

## Still stuck?

Start from [Troubleshooting](../troubleshooting.md), then search or open an issue in the [GitHub repository](https://github.com/conorod1992/extended_openai_conversation/issues).
