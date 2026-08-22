import assert from "node:assert/strict";

import {formatUsageTimestamp, tokenBreakdown} from "../custom_components/extended_openai_conversation_responses/frontend/usage-chart.js";

assert.deepEqual(tokenBreakdown(100, 25), { total: 100, cached: 25, uncached: 75 });
assert.deepEqual(tokenBreakdown(100, 0), { total: 100, cached: 0, uncached: 100 });
assert.deepEqual(tokenBreakdown(100, 125), { total: 100, cached: 100, uncached: 0 });
assert.deepEqual(tokenBreakdown(undefined, undefined), { total: 0, cached: 0, uncached: 0 });

const timestamp = "2026-08-17T18:43:58.806979+00:00";
const friendly = formatUsageTimestamp(timestamp, "en-US");
assert.equal(friendly.datetime, timestamp);
assert.notEqual(friendly.display, timestamp);
assert.match(friendly.display, /2026/);
assert.deepEqual(formatUsageTimestamp("not-a-date", "en-US"), {display:"not-a-date",datetime:"not-a-date"});

const panelSource = await (await import("node:fs/promises")).readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/management-panel.js", import.meta.url), "utf8");
assert.match(panelSource, /<time datetime=/);
assert.match(panelSource, /Cached input<\/strong> is request content the provider has seen before and can reuse/);
assert.match(panelSource, /included in the total token count/);
assert.match(panelSource, /usually cheaper than uncached input when the provider supports discounted caching/);
