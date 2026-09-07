# Token-only usage statistics

Extended OpenAI records provider-reported token usage. It does not contain a pricing catalogue, estimate cost, convert currencies, or project bills.

## Provider requests and conversation runs

A **provider request** is one API call. A **conversation run** is one complete user turn, from the start of processing until the final response or error. Tool loops and context summarisation can cause one run to contain several provider requests. Every request made during a run shares its run ID.

Provider-request counters and tokens are committed when each real provider request finishes; the conversation-run counter is committed once when the complete user turn finishes. This keeps one user turn distinct from any additional provider rounds it triggers and preserves completed request usage even if the run is interrupted before its normal final response.

Missing provider metadata is handled safely: the request and run are still counted, while unavailable token fields remain zero. Failed, cancelled, interrupted-stream, and exception paths finalize their run once.

## Sensors

Each conversation-agent device has four disabled-by-default diagnostic entities:

- **Lifetime usage** keeps the existing lifetime-token unique ID and cumulative counters.
- **Usage today** uses Home Assistant's local calendar day. Provider requests are assigned to the local date on which that request completes; run-level metrics use the local date on which the run completes.
- **Usage this month** is derived from daily aggregates.
- **Last response usage** contains the latest run's bounded token, request, model, duration, and success details.

No sensor is created per day, model, request, or transcript session. Updates are listener-driven and do not poll.

## Management panel

Open **Extended OpenAI → Usage & Maintenance → Usage** to see daily token charts, input/output/cached/reasoning splits, run counts, recent runs, request details, and provider/model/API-mode breakdowns. There are no costs or prices.

The **Usage diagnostics** section summarizes the same visible daily window as the chart. It highlights cache reuse, run and provider-request success rates, average provider requests per run, average run duration, token mix, tool-call and web-search activity, and token distribution by model, provider, and API mode. This makes it easier to spot repeated provider calls, unexpectedly slow turns, poor cache reuse, or a model/API route accounting for most token use.

Recent failed runs are called out separately. When retained request detail exists, **View requests** shows the individual provider calls in that run, including stage, provider, model, API mode, token counts, cache/reasoning metadata, duration, requested tool-call count, web-search use, and success/error state. This detail remains content-free.

Recent-run timestamps are displayed in Home Assistant's configured timezone using the browser's locale, while the exact provider timestamp remains available on the semantic time element. **Cached input** is request content the provider has seen before and can reuse. It remains part of total tokens rather than additional usage, and may cost less than uncached input when the provider supports discounted caching.

Detailed request and run records are content-free. They never contain prompts, replies, tool arguments, tool results, memories, knowledge text, attachments, provider payloads, web-search bodies, or hidden reasoning.

## Storage and retention

Compact lifetime totals and daily aggregates are persisted as one authoritative aggregate snapshot when daily Usage storage is available; the existing lifetime-totals store is retained as a compatibility mirror. Bounded request/run detail remains separate, so missing, expired, or pruned detail never reconstructs or changes aggregate counters.

- request details: 30 days by default
- run details: 90 days by default
- daily aggregates: indefinite
- lifetime totals: indefinite

Detailed retention can be disabled or set to 7, 30, 90, 180, or 365 days. Pruning or clearing recent detail records does not alter daily, monthly, or lifetime totals. Existing cumulative usage is loaded without losing its lifetime counters, and initialization retries do not append already loaded detail a second time.
