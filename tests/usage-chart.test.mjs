import assert from "node:assert/strict";

import { tokenBreakdown } from "../custom_components/extended_openai_conversation_responses/frontend/usage-chart.js";

assert.deepEqual(tokenBreakdown(100, 25), { total: 100, cached: 25, uncached: 75 });
assert.deepEqual(tokenBreakdown(100, 0), { total: 100, cached: 0, uncached: 100 });
assert.deepEqual(tokenBreakdown(100, 125), { total: 100, cached: 100, uncached: 0 });
assert.deepEqual(tokenBreakdown(undefined, undefined), { total: 0, cached: 0, uncached: 0 });
