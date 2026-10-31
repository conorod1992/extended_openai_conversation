# Request debugging

Open **Extended OpenAI → Usage & Maintenance → Request debugging** when you need to see what was actually sent to the AI provider for a recent request.

This is mainly a troubleshooting tool. Captured requests can contain private Home Assistant context or conversation content, so treat them as sensitive diagnostic data.

This is separate from Home Assistant's **Enable debug logging**, which records integration operational logs. Neither switch enables the other. For ordinary troubleshooting, start with [HA debug logging](../reference/logging.md).

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

Only administrators can enable or inspect captures. Capture is off by default and records future runs for the selected agent. The default capture limit is **10 runs**; choose **5**, **10**, **25**, or **50** under **Capture limit**. When the limit is reached, older captures are replaced.

Captures are held in memory and disappear when Home Assistant restarts. Turning capture off stops new captures; use **Clear captures** to remove existing ones. The **Usage data retention** settings apply to content-free Usage records, not these captures.

The management view pages through provider requests and bounds large fields. Copying a visible page or the first page does not export every provider request or guarantee an untruncated provider payload. Review copied material before sharing: private text can appear inside prompts, tool arguments, results, or retrieved context even when connection credentials are excluded from request settings.
