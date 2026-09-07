import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

import {
  addUsageCalendarDays,
  loadAllUsageDays,
  localUsageDateKey,
  selectUsageHistory,
  usageChartBuckets,
  usageLifetimeDiffersFromDaily,
  usageWindowBounds,
} from "../custom_components/extended_openai_conversation_responses/frontend/usage-chart.js";

const today = "2026-09-07";
assert.deepEqual(usageWindowBounds("7", today), {
  id:"7", label:"7 days", startDate:"2026-09-01", endDate:today,
});
assert.deepEqual(usageWindowBounds("30", today), {
  id:"30", label:"30 days", startDate:"2026-08-09", endDate:today,
});
assert.deepEqual(usageWindowBounds("90", today), {
  id:"90", label:"90 days", startDate:"2026-06-10", endDate:today,
});
assert.deepEqual(usageWindowBounds("year", today), {
  id:"year", label:"Year to date", startDate:"2026-01-01", endDate:today,
});
assert.deepEqual(usageWindowBounds("all", today), {
  id:"all", label:"All available", startDate:null, endDate:today,
});

// Calendar-date arithmetic is deliberately timezone-free once HA's local date key
// has been chosen, so DST transitions cannot skip or duplicate a calendar day.
assert.equal(addUsageCalendarDays("2026-03-28", 1), "2026-03-29");
assert.equal(addUsageCalendarDays("2026-03-29", 1), "2026-03-30");
assert.equal(addUsageCalendarDays("2026-10-24", 1), "2026-10-25");
assert.equal(addUsageCalendarDays("2026-10-25", 1), "2026-10-26");
assert.equal(localUsageDateKey(new Date("2026-03-29T23:30:00Z"), "Europe/Dublin"), "2026-03-30");
assert.equal(localUsageDateKey(new Date("2026-10-24T23:30:00Z"), "Europe/Dublin"), "2026-10-25");

const makeDay = (date, total = 10) => ({
  date,
  run_count:1, successful_run_count:1, failed_run_count:0,
  api_request_count:1, successful_request_count:1, failed_request_count:0,
  input_tokens:8, output_tokens:2, total_tokens:total, cached_input_tokens:4,
  reasoning_tokens:1, tool_call_count:1, web_search_run_count:0,
  total_run_duration_ms:1000,
  provider_breakdown:{OpenAI:total},
  model_breakdown:{"gpt-test":total},
  api_mode_breakdown:{responses:total},
});

const days = Array.from({length:120}, (_, index) => makeDay(addUsageCalendarDays("2026-05-11", index)));
for (const [window, expectedDays] of [["7",7],["30",30],["90",90],["year",120],["all",120]]) {
  const history = selectUsageHistory(days, window, today);
  assert.equal(history.days.length, expectedDays, `${window} day count`);
  assert.equal(history.summary.total_tokens, expectedDays * 10, `${window} totals use the selected rows`);
  assert.deepEqual(history.summary.provider_breakdown, {OpenAI:expectedDays * 10});
  assert.deepEqual(history.summary.model_breakdown, {"gpt-test":expectedDays * 10});
  assert.deepEqual(history.summary.api_mode_breakdown, {responses:expectedDays * 10});
}

const empty = selectUsageHistory([], "30", today);
assert.equal(empty.days.length, 0);
assert.equal(empty.summary.total_tokens, 0);
assert.equal(empty.availableStart, null);
assert.equal(empty.availableEnd, null);

const partialDays = days.slice(-5);
const partial = selectUsageHistory(partialDays, "30", today);
assert.equal(partial.days.length, 5);
assert.equal(partial.partialStart, true);
assert.equal(partial.availableStart, "2026-09-03");
assert.equal(partial.availableEnd, today);

const allHistory = selectUsageHistory(days, "all", today);
const matchingLifetime = {
  conversation_count:120, api_request_count:120, successful_request_count:120,
  failed_request_count:0, input_tokens:960, output_tokens:240, total_tokens:1200,
  cached_input_tokens:480, reasoning_tokens:120,
};
assert.equal(usageLifetimeDiffersFromDaily(matchingLifetime, allHistory.allSummary), false);
assert.equal(usageLifetimeDiffersFromDaily({...matchingLifetime,total_tokens:1300}, allHistory.allSummary), true);

const shortBuckets = usageChartBuckets(selectUsageHistory(days, "90", today).days, "90");
assert.equal(shortBuckets.length, 90);
assert.equal(shortBuckets.reduce((sum, bucket) => sum + bucket.total_tokens, 0), 900);
const yearHistory = selectUsageHistory(days, "year", today);
const monthBuckets = usageChartBuckets(yearHistory.days, "year");
assert.ok(monthBuckets.length <= 12);
assert.equal(monthBuckets.reduce((sum, bucket) => sum + bucket.total_tokens, 0), yearHistory.summary.total_tokens);

// All-available history is transferred in bounded pages and then deduplicated.
const pagedDays = ["2026-09-01","2026-09-02","2026-09-03","2026-09-04","2026-09-05"].map((date) => makeDay(date));
const starts = [];
const paged = await loadAllUsageDays(async (startDate) => {
  starts.push(startDate);
  return {days:pagedDays.filter((day) => day.date >= startDate).slice(0, 2)};
}, {pageSize:2,maxPages:4});
assert.deepEqual(paged.days.map((day) => day.date), pagedDays.map((day) => day.date));
assert.deepEqual(starts, ["0000-01-01","2026-09-03","2026-09-05"]);

// Range changes are local renders over one loaded aggregate snapshot. Agent loads are
// still generation-guarded, and the paging wrapper captures one agent identity before
// issuing any page so a mid-load selector change cannot mix agents.
const panelSource = await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/management-panel.js", import.meta.url), "utf8");
const usageSource = await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/usage-chart.js", import.meta.url), "utf8");
assert.match(panelSource, /const loadToken = \+\+this\._loadToken/);
assert.match(panelSource, /if \(loadToken !== this\._loadToken\) return/);
assert.match(usageSource, /const agent = this\._selectedAgent\?\.\(\)/);
assert.match(usageSource, /const identity = agent \? \{entry_id: agent\.entry_id, subentry_id: agent\.subentry_id\} : \{\}/);
assert.match(usageSource, /this\._usageHistoryWindow = normalizeUsageWindow\(event\.target\.value\);\s*this\._render\(\)/);

// The management-window feature must not replace or reinterpret Today / Month sensor semantics.
const sensorSource = await readFile(new URL("../custom_components/extended_openai_conversation_responses/sensor.py", import.meta.url), "utf8");
assert.match(sensorSource, /return self\._usage\.today_summary\(\)/);
assert.match(sensorSource, /return self\._usage\.month_summary\(\)/);
assert.match(sensorSource, /async_track_time_change\([\s\S]*hour=0,[\s\S]*minute=0,[\s\S]*second=0/);
