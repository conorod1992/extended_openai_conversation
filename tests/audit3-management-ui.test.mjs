import assert from "node:assert/strict";
import {scheduleWallTime, scheduleInstant} from "../custom_components/extended_openai_conversation_responses/frontend/schedule-time.js";
import {loadUsageWindow, loadUsageDaily, loadAllUsageDays} from "../custom_components/extended_openai_conversation_responses/frontend/usage-data.js";
import {renderUsageDiagnostics} from "../custom_components/extended_openai_conversation_responses/frontend/usage-chart.js";
import {renderConversations, searchSessionRows, bindConversationActions} from "../custom_components/extended_openai_conversation_responses/frontend/management-history-pagination.js";
import {GUIDE_TOPICS} from "../custom_components/extended_openai_conversation_responses/frontend/guide-content.js";

process.env.TZ = "America/New_York";
assert.equal(scheduleWallTime("2026-07-01T09:00:00Z", "Europe/Dublin"), "2026-07-01T10:00");
assert.equal(scheduleInstant("2026-07-01T10:00", "Europe/Dublin"), "2026-07-01T09:00:00.000Z");
assert.equal(scheduleInstant("2026-01-01T00:00", "Asia/Kolkata"), "2025-12-31T18:30:00.000Z");
assert.equal(scheduleInstant("2026-01-01T00:00", "UTC"), "2026-01-01T00:00:00.000Z");
assert.throws(() => scheduleInstant("2026-03-29T01:30", "Europe/Dublin"), /does not exist/);
assert.throws(() => scheduleInstant("2026-10-25T01:30", "Europe/Dublin"), /occurs twice/);
for (const original of ["2026-10-25T00:30:27Z", "2026-10-25T01:30:43Z"]) {
  assert.equal(scheduleInstant("2026-10-25T01:30", "Europe/Dublin", original), original);
}
assert.throws(() => scheduleInstant("2026-02-30T10:00", "UTC"), /valid/);
assert.equal(GUIDE_TOPICS.find(topic => topic.id === "request-debugging").action.section, "request-debug");

const today = "2026-10-10";
let calls = 0;
const panel = {_agentId: "one", _hass: {config: {time_zone: "UTC"}}, _call: async () => ({days: [], revision: ++calls})};
await loadUsageWindow(panel, "7", today);
await loadUsageWindow(panel, "30", today);
panel._agentId = "two";
const other = await loadUsageWindow(panel, "30", today);
panel._agentId = "one";
await loadUsageWindow(panel, "7", today, {useCache: false});
assert.equal((await loadUsageWindow(panel, "30", today)).revision, 5, "refresh invalidates overlapping windows");
panel._agentId = "two";
assert.equal(await loadUsageWindow(panel, "30", today), other, "other agent cache survives");
panel._agentId = "one";
let resolve;
panel._call = () => new Promise(done => { resolve = done; });
const stale = loadUsageWindow(panel, "90", today);
const finishStale = resolve;
panel._call = async () => ({days: [], revision: "fresh"});
await loadUsageWindow(panel, "7", today, {useCache: false});
finishStale({days: [], revision: "stale"});
await stale;
assert.equal((await loadUsageWindow(panel, "90", today)).revision, "fresh", "late response cannot repopulate invalidated cache");
const local_run_summary = {count: 46, start_date: "2026-10-04", end_date: today, retention_days: 30};
const all = await loadAllUsageDays(async () => ({days: [], local_run_summary}));
assert.deepEqual(all.local_run_summary, local_run_summary);
const e = value => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;");
const ui = {_e: e, _usageHistoryWindow: "7", _hass: {config: {time_zone: "UTC"}}};
const markup = renderUsageDiagnostics(ui, {summary: {today: {date: today}}, days: {days: [], local_run_summary}, runs: {runs: [{request_count: 0}]}});
assert.match(markup, /Retained runs without provider requests<\/span><strong>46<\/strong>/);
assert.match(markup, /retention may exclude older runs/);
ui._usageHistoryWindow = "30";
assert.match(renderUsageDiagnostics(ui, {summary: {today: {date: today}}, days: {days: [], local_run_summary}}), /<strong>Unavailable<\/strong>/);

const hit = {session_id: "long", turn_index: 25, turn_id: "t25", excerpt: "needle <script>", timestamp: today};
const conversation = {_e: e, _formatDate: x => x, _empty: x => x, _contentData: {sessions: {sessions: searchSessionRows({results: [hit]})}}};
const html = renderConversations(conversation);
assert.match(html, /needle &lt;script>/);
assert.equal((html.match(/data-start-turn="20"/g) || []).length, 2);
const button = {dataset: {id: "long", startTurn: "20"}};
const host = {};
const dialog = {open: false, showModal() {this.open = true;}};
let request;
let activate;
conversation._ensureOwnedDialog = () => {};
conversation._loading = () => "loading";
conversation._activate = (_el, callback) => {activate = callback;};
conversation._call = async (_section, _action, extra) => {request = extra; throw new Error("stop after checking request");};
conversation.shadowRoot = {
 querySelector(selector) {
   if (selector === "#archive-query") return {closest: () => host, addEventListener() {}};
   if (selector === "#session-dialog") return dialog;
   if (selector === "#session-title" || selector === "#session-body") return {};
   return null;
 },
 querySelectorAll: () => [button],
};
bindConversationActions(conversation);
await activate();
assert.equal(request.start_turn, 20);
console.log("audit3 management fixes passed");
