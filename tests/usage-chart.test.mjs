import assert from "node:assert/strict";

import {formatUsageNumber, formatUsageTimestamp, tokenBreakdown} from "../custom_components/extended_openai_conversation_responses/frontend/usage-chart.js";

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

const panelSource = await (await import("node:fs/promises")).readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/management-panel.js", import.meta.url), "utf8");
assert.match(panelSource, /<time datetime=/);
assert.match(panelSource, /Cached input<\/strong> is request content the provider has seen before and can reuse/);
assert.match(panelSource, /included in the total token count/);
assert.match(panelSource, /usually cheaper than uncached input when the provider supports discounted caching/);
assert.match(panelSource, /this\._hass\?\.config\?\.time_zone/);
assert.match(panelSource, /formatUsageNumber\(tokens\.total\)/);
assert.match(panelSource, /Promise\.allSettled/);
