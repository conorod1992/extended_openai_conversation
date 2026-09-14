# Request debugging

Open **Extended OpenAI → Usage & Maintenance → Request debugging** to inspect complete recent provider requests when ordinary usage summaries are not enough to explain what the model received.

Request debugging is intended for targeted troubleshooting of prompts, context, tool definitions, request parameters and provider-facing structure. Because captured requests can contain private Home Assistant context or conversation content, treat them as sensitive diagnostic data.

## Preview effective request vs request debugging

**Preview effective request** is a safe local preview for a brand-new message. It assembles the inspectable system/context blocks, first-request custom tools, Function Group catalogue/loader, provider-hosted tools and non-secret request settings without calling the provider or mutating agent state.

It deliberately cannot include information that exists only for a real request, such as the actual user input, prior conversation/tool-call history, query-selected memories, credentials, or opaque provider framing.

**Request debugging** records real request data and is therefore the better tool when you need to understand an actual failed or surprising provider call.

## What to check

When diagnosing a request, compare:

- the system prompt and generated Home Assistant context
- which Function Tools and hosted tools were actually exposed
- model/API mode and supported request parameters
- conversation and tool-call history relevant to the request
- whether current memory, Knowledge or archive retrieval contributed context
- provider errors or malformed responses shown alongside the request/run

For token and request totals rather than request contents, use [Usage statistics](usage-statistics.md). For provider connectivity and selected-agent health checks, use **Usage & Maintenance → Diagnostics**.

## Privacy and retention

Captured request details are diagnostic records, not a replacement conversation archive. Use the retention controls under **Usage & Maintenance → Retention & maintenance** to limit how long detailed request/run data is kept.
