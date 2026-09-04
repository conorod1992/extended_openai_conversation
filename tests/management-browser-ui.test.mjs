import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";

const ui = await import("../custom_components/extended_openai_conversation_responses/frontend/guest-mode-ui.js");

assert.equal(ui.memorySearchProjection({content:"Likes Tea", category:"Preference", source:"Explicit"}), "likes tea preference explicit");
assert.equal(ui.formatManagementTimestamp("not-a-date"), "not-a-date");
assert.equal(ui.formatManagementTimestamp(null), "Unknown date");

class FakePanel {}
FakePanel.prototype._loadSection = async function() {};
FakePanel.prototype._memories = function() { return "temporary"; };
FakePanel.prototype._conversations = function() { return "conversations"; };
FakePanel.prototype._bindActions = function() {};
FakePanel.prototype._updateVisibleList = function() {};
assert.equal(ui.installManagementBrowser(FakePanel), true);
assert.equal(ui.installManagementBrowser(FakePanel), false);
const panel = new FakePanel();
panel._hass = {config:{time_zone:"UTC"}};
assert.match(panel._formatDate("2026-09-04T12:00:00Z"), /2026|9\/4|04\/09/);

const source = await readFile(new URL("../custom_components/extended_openai_conversation_responses/frontend/guest-mode-ui.js", import.meta.url), "utf8");
assert.match(source, /MEMORY_SEARCH_DEBOUNCE_MS = 250/);
assert.match(source, /id="load-more-memories"/);
assert.match(source, /id="load-more-conversations"/);
assert.match(source, /id="load-more-turns"/);
assert.match(source, /start_turn: state\.turns\.length/);
assert.match(source, /conversations", "search"/);
assert.match(source, /memories", action/);
