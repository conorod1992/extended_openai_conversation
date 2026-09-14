# Request debugging

Open **Extended OpenAI → Usage & Maintenance → Request debugging** when you need to see what was actually sent to the AI provider for a recent request.

This is mainly a troubleshooting tool. Captured requests can contain private Home Assistant context or conversation content, so treat them as sensitive diagnostic data.

## Preview effective request vs request debugging

**Preview effective request** shows a safe local preview for a brand-new message. It can show the main prompts/context, available tools and non-secret request settings without calling the provider or changing agent state.

Because it is only a preview, it cannot include details that exist only during a real conversation, such as the actual user message, previous conversation/tool-call history, memories chosen for that request, credentials, or provider-only framing.

**Request debugging** records a real request. Use it when you need to understand why an actual provider call behaved unexpectedly.

## What to check

When diagnosing a request, look at:

- the system prompt and generated Home Assistant context
- which Function Tools and hosted tools were actually available
- the selected model/API mode and request parameters
- conversation and tool-call history relevant to the request
- whether memory, Knowledge or archive retrieval added context
- any provider error or malformed response shown with the request/run

For token and request totals rather than request contents, use [Usage statistics](usage-statistics.md). For provider connectivity and general agent health checks, use **Usage & Maintenance → Diagnostics**.

## Privacy and retention

Captured request details are diagnostic records, not a replacement conversation archive. Use the retention controls under **Usage & Maintenance → Retention & maintenance** to limit how long detailed request/run data is kept.
