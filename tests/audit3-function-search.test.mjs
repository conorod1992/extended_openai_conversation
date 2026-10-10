import assert from "node:assert/strict";
import {matchesFunctionSearch as tools} from "../custom_components/extended_openai_conversation_responses/frontend/agent-config-tools-base.js";
import {matchesFunctionSearch as editor} from "../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor-base.js";
for (const matches of [tools, editor]) {
  for (const query of ["开灯", "مصباح", "лампа"]) {
    assert.equal(matches(query, "unrelated weather forecast"), false);
    assert.equal(matches(query, query + " controls"), true);
  }
  assert.equal(matches("", "anything"), true);
  assert.equal(matches("  ", "anything"), true);
  assert.equal(matches("!!!", "anything"), false);
  assert.equal(matches("cafe", "Café controls"), true);
  assert.equal(matches("Reminder", "Reminders"), true);
}
