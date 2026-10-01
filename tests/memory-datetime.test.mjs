import assert from "node:assert/strict";
import {test} from "node:test";
import {memoryLocalDateTime, memoryExpiryISO} from "../custom_components/extended_openai_conversation_responses/frontend/memory-datetime.js";

const panel = time_zone => ({_hass: {config: {time_zone}}});
test("Memory dates use Home Assistant timezone and round-trip seconds", () => {
  const ha = panel("Europe/Dublin");
  assert.equal(memoryLocalDateTime(ha, "2026-09-02T17:30:15Z"), "2026-09-02T18:30:15");
  assert.equal(memoryExpiryISO(ha, "2026-09-02T18:30:15"), "2026-09-02T17:30:15.000Z");
  assert.equal(memoryExpiryISO(panel("Asia/Kathmandu"), "2026-09-02T18:30"), "2026-09-02T12:45:00.000Z");
  assert.equal(memoryExpiryISO(ha, "2026-12-02T18:30"), "2026-12-02T18:30:00.000Z");
});
test("Memory date conversion rejects DST gaps and preserves repeated-hour edits", () => {
  const ha = panel("Europe/Dublin");
  assert.throws(() => memoryExpiryISO(ha, "2026-03-29T01:30"), /does not exist/);
  assert.equal(memoryExpiryISO(ha, "2026-10-25T01:30"), "2026-10-25T00:30:00.000Z");
  assert.equal(memoryExpiryISO(ha, "2026-10-25T01:30", "2026-10-25T01:30:00Z"), "2026-10-25T01:30:00Z");
  assert.throws(() => memoryExpiryISO(ha, ""), /Choose an expiry/);
});
