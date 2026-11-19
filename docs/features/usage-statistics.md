# Token-only usage statistics

Extended OpenAI records provider-reported token usage. It does not contain a pricing catalogue, estimate cost, convert currencies, or project bills.

## Provider requests and conversation runs

A **provider request** is one API call. A **conversation run** is one complete user turn, from the start of processing until the final response or error. Tool loops and context summarisation can cause one run to contain several provider requests. Foreground requests, including a preflight context summary needed by the current turn, share that run’s ID. Deferred summaries after a reply remain detached from finalized runs and contribute to aggregate usage. Hybrid Memory embedding calls also count as provider requests, including failed attempts; provider-reported embedding input tokens are included.

Provider-request counters and tokens are committed when each real provider request finishes; the conversation-run counter is committed once when the complete user turn finishes. This keeps one user turn distinct from any additional provider rounds it triggers and preserves completed request usage even if the run is interrupted before its normal final response.

Missing provider metadata is handled safely: the request and run are still counted, while unavailable token fields remain zero. Failed, cancelled, interrupted-stream, and exception paths finalize their run once.

Local context-size estimates are used only for history management. They are never substituted for provider-reported Usage tokens, so successful context trimming does not mean a provider returned token counts.

Detail expiry runs daily during inactivity as well as after requests. Expired details are hidden from reads immediately; lifetime and daily aggregates remain intact.

## Sensors

Each conversation-agent device has four disabled-by-default diagnostic entities:

- **Lifetime usage** keeps the existing lifetime-token unique ID and cumulative counters.
- **Usage today** uses Home Assistant's local calendar day. Provider requests are assigned to the local date on which that request completes; run-level metrics use the local date on which the run completes.
- **Usage this month** is derived from daily aggregates.
- **Last response usage** contains the latest retained completed run's bounded token, request, model, duration, and success details. It has no value when run-detail retention is disabled, after that detail expires, or after a successful **Clear details**. A failed clear preserves the retained detail. Lifetime, Today, and This month aggregate sensors remain intact.

No sensor is created per day, model, request, or transcript session. Lifetime,
Today, and This month update through accounting/restore listeners without polling.
Today and This month also refresh at Home Assistant's local midnight, including
across daylight-saving changes. Timezone changes refresh the period sensors and
their next midnight callback.
Last response usage receives accounting updates
and retains Home Assistant polling so an idle run that passes its retention limit
loses its value even without further usage. It then has no token value or run
attributes (Home Assistant displays an unknown state). This expiry may take up
to the normal sensor polling interval. The selectable management-panel history
period does not change the Today or This month sensor periods.

Lifetime usage uses Home Assistant's `total` state class without `last_reset`.
Restoring an older backup can reduce the lifetime counter; HA records that as a
correction rather than inventing a new consumption cycle. Today and This month
retain their explicit calendar reset markers. This change prevents new restore
inflation; it does not rewrite previously recorded HA statistics.

Changing the timezone keeps the reset marker for the currently selected calendar
period. When a period change selects a bucket that already contains tokens,
Usage today or Usage this month can briefly publish a signed correction before
the actual period total. This keeps Recorder from counting those tokens again.
State-change automations and raw history can observe the correction, which can
be negative.

## Management panel

Open **Extended OpenAI → Usage & Maintenance → Usage** to see daily token charts, input/output/cached/reasoning splits, run counts, recent runs, request details, and provider/model/API-mode breakdowns. There are no costs or prices.

The aggregate view can be switched between **7 days**, **30 days**, **90 days**, **Year to date**, and **All available**. Year to date means January 1 through the current Home Assistant local calendar date; it is not a trailing twelve-month period. The 7/30/90-day periods are inclusive Home Assistant local-calendar periods ending on the current local date. Date-only history keys are used for period arithmetic, so browser UTC conversion does not move usage into a neighbouring day at midnight or across daylight-saving changes.

The selected period drives the aggregate token totals, chart, model/provider/API-mode breakdowns, run/request counts, and diagnostic ratios together. Year-to-date and all-available charts group the underlying daily aggregates by month for readability; the totals still include every stored daily aggregate in the selected period.

**All available** means all stored daily aggregate history, not the lifetime counter. Lifetime counters are persisted separately and can include accounting from before daily aggregate history became available, so all-available daily totals and lifetime totals can legitimately differ. The Usage page shows the first and last stored daily aggregate and calls out when lifetime counters are not represented exactly by the available daily history.

The **Usage diagnostics** section summarizes the same selected daily-aggregate period as the chart. It highlights cache reuse, run and provider-request success rates, average provider requests per run, average run duration, token mix, tool-call and web-search activity, and token distribution by model, provider, and API mode. This makes it easier to spot repeated provider calls, unexpectedly slow turns, poor cache reuse, or a model/API route accounting for most token use.

Run averages count requests and tokens from runs that finished during the selected period. A run that crosses local midnight contributes to its completion day. Standalone provider calls contribute to request and token totals only. Older Usage data may carry timestamps for individual runs without their original Home Assistant calendar dates. A period that includes runs without recorded attribution displays **Unavailable** for average tokens and requests per run. Daily and lifetime totals remain available.

Recent failed runs are called out separately. Recent-run and provider-request detail stays within its configured retention periods. Use **Requests** on a recent run to see its individual provider calls, or **Browse retained requests** to include calls whose run detail has expired and calls recorded independently of a run. The detail includes stage, provider, model, API mode, token counts, cache and reasoning metadata, duration, requested tool-call count, web-search use, and success or error state.

**Retained runs without provider requests** uses the same recorded completion day as the daily totals, including after a Home Assistant timezone change or restore. If a legacy run could fall on either side of the selected period’s boundary, this count displays **Unavailable**. Clearing or expiring run details leaves the aggregate totals available.

Recent-run timestamps are displayed in Home Assistant's configured timezone using the browser's locale, while the exact provider timestamp remains available on the semantic time element. **Cached input** is request content the provider has seen before and can reuse. It remains part of total tokens rather than additional usage, and may cost less than uncached input when the provider supports discounted caching.

Detailed request and run records are content-free. They never contain prompts, replies, tool arguments, tool results, memories, knowledge text, attachments, provider payloads, web-search bodies, or hidden reasoning.

## Storage and retention

Compact lifetime totals and daily aggregates are persisted as one authoritative aggregate snapshot when daily Usage storage is available; the existing lifetime-totals store is retained as a compatibility mirror. Request and run details have their own retention policy. Recorded completed-run averages survive clearing or expiring those details.

The daily records remain readable after rolling back to 6.8.3. Saving Usage in 6.8.3 removes newer run attribution from the snapshot. After upgrading again, affected average tokens and requests per completed run show **Unavailable** until the selected period contains only runs with recorded attribution. Daily totals, lifetime totals, and retained run records remain readable.

After a completed save by the current version, re-upgrading can recover 6.8.3 standalone requests such as **Test assistant** when the compatibility mirror identifies the same saved aggregate. These requests count toward lifetime Usage because 6.8.3 recorded no daily entry for them. For older snapshots without that identifier, the combined aggregate remains authoritative because an interrupted save or restore can leave the mirror with different totals. The identifier may appear in 6.8.3's diagnostic details. Current diagnostics and backups omit it.

- request details: 30 days by default
- run details: 90 days by default
- daily aggregates: indefinite
- lifetime totals: indefinite

Detailed retention can be disabled or set to 7, 30, 90, 180, or 365 days. Pruning or clearing recent detail records does not alter daily, monthly, selectable-period, or lifetime totals. Existing cumulative usage is loaded without losing its lifetime counters, and initialization retries do not append already loaded detail a second time.
