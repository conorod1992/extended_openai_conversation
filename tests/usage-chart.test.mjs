import assert from "node:assert/strict";

import {
  formatUsageNumber,
  formatUsageTimestamp,
  renderUsageDiagnostics,
  sortedUsageBreakdown,
  summarizeUsageDiagnostics,
  tokenBreakdown,
} from "../custom_components/extended_openai_conversation_responses/frontend/usage-chart.js";

assert.deepEqual(tokenBreakdown(100, 25), { total: 100, cached: 25, uncached: 75 });
assert.deepEqual(tokenBreakdown(100, 0), { total: 100, cached: 0, uncached: 100 });
assert.deepEqual(tokenBreakdown(100, 125), { total: 100, cached: 100, uncached: 0 });
assert.deepEqual(tokenBreakdown(undefined, undefined), { total: 0, cached: 0, uncached: 0 });
assert.equal(formatUsageNumber(2017442, "en-US"), "2,017,442");
assert.equal(formatUsageNumber("not-a-number", "en-US"), "not-a-number");

const timestamp = "2026-08-17T18:43:58.806979+00:00";
const friendly = formatUsageTimestamp(timestamp, "en-US", "UTC");
assert.equal(friendly.datetime, timestamp);
assert.notEqual(friendly.display, timestamp);
assert.match(friendly.display, /2026/);
const dateBoundary = "2026-08-17T12:30:00+00:00";
assert.match(formatUsageTimestamp(dateBoundary, "en-US", "Pacific/Kiritimati").display, /Aug 18/);
assert.match(formatUsageTimestamp(dateBoundary, "en-US", "Pacific\/Honolulu").display, /Aug 17/);
assert.deepEqual(formatUsageTimestamp("not-a-date", "en-US", "UTC"), {display:"not-a-date",datetime:"not-a-date"});

const diagnostics = summarizeUsageDiagnostics([
  {
    date:"2026-08-28",run_count:2,successful_run_count:2,failed_run_count:0,
    api_request_count:3,successful_request_count:3,failed_request_count:0,
    input_tokens:1000,output_tokens:200,total_tokens:1200,cached_input_tokens:400,
    reasoning_tokens:50,tool_call_count:2,web_search_run_count:1,total_run_duration_ms:5000,
    provider_breakdown:{OpenAI:1200},model_breakdown:{"gpt-test":1200},api_mode_breakdown:{responses:1200},
  },
  {
    date:"2026-08-29",run_count:1,successful_run_count:0,failed_run_count:1,
    api_request_count:1,successful_request_count:0,failed_request_count:1,
    input_tokens:500,output_tokens:100,total_tokens:600,cached_input_tokens:100,
    reasoning_tokens:25,tool_call_count:0,web_search_run_count:0,total_run_duration_ms:4000,
    provider_breakdown:{OpenAI:600},model_breakdown:{"gpt-test":300,"gpt-other":300},api_mode_breakdown:{responses:600},
  },
]);
assert.equal(diagnostics.run_count, 3);
assert.equal(diagnostics.total_tokens, 1800);
assert.equal(diagnostics.cached_input_tokens, 500);
assert.equal(diagnostics.cache_percent, 500 / 1500 * 100);
assert.equal(diagnostics.run_success_percent, 2 / 3 * 100);
assert.equal(diagnostics.average_requests_per_run, 4 / 3);
assert.equal(diagnostics.average_duration_ms, 3000);
assert.deepEqual(diagnostics.provider_breakdown, {OpenAI:1800});
assert.deepEqual(sortedUsageBreakdown({beta:20,alpha:20,gamma:40}), [
  {name:"gamma",tokens:40},{name:"alpha",tokens:20},{name:"beta",tokens:20},
]);

const escape = (value) => String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");
const diagnosticHtml = renderUsageDiagnostics({
  _e:escape,
  _hass:{config:{time_zone:"UTC"}},
}, {
  days:{days:[{
    date:"2026-08-29",run_count:2,successful_run_count:1,failed_run_count:1,
    api_request_count:3,successful_request_count:2,failed_request_count:1,
    input_tokens:1000,output_tokens:250,total_tokens:1250,cached_input_tokens:500,
    reasoning_tokens:40,tool_call_count:2,web_search_run_count:1,total_run_duration_ms:5000,
    provider_breakdown:{OpenAI:1250},model_breakdown:{"gpt-test":1250},api_mode_breakdown:{responses:1250},
  }]},
  runs:{runs:[{run_id:"run-1",completed_at:"2026-08-29T12:00:00+00:00",successful:false,error_type:"TimeoutError",request_count:2,duration_ms:3000}]},
});
assert.match(diagnosticHtml, /Usage diagnostics/);
assert.match(diagnosticHtml, /Cached input/);
assert.match(diagnosticHtml, /50\.0%/);
assert.match(diagnosticHtml, /Models/);
assert.match(diagnosticHtml, /gpt-test/);
assert.match(diagnosticHtml, /Recent failed runs/);
assert.match(diagnosticHtml, /data-usage-run-id="run-1"/);
assert.match(diagnosticHtml, /do not estimate API cost/);

const panelSource = await (await import("node:fs/promises")).readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/management-panel.js", import.meta.url), "utf8");
const usageSource = await (await import("node:fs/promises")).readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/usage-chart.js", import.meta.url), "utf8");
assert.match(panelSource, /<time datetime=/);
assert.match(panelSource, /Cached input<\/strong> is request content the provider has seen before and can reuse/);
assert.match(panelSource, /included in the total token count/);
assert.match(panelSource, /usually cheaper than uncached input when the provider supports discounted caching/);
assert.match(panelSource, /formatUsageTimestamp\(run\.completed_at, undefined, this\._hass\?\.config\?\.time_zone\)/);
assert.match(panelSource, /formatUsageNumber\(tokens\.total\)/);
assert.match(panelSource, /Promise\.allSettled/);
assert.match(usageSource, /_call\("usage", "requests"/);
assert.match(usageSource, /usage-request-dialog/);
assert.match(usageSource, /cached_input_tokens \/ summary\.input_tokens/);
