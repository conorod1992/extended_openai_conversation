import assert from "node:assert/strict";
import test from "node:test";
import {cachedFunctionSearchTokens, matchesFunctionSearch, searchTokens} from "../custom_components/extended_openai_conversation_responses/frontend/function-search.js";
import {bindConfiguration} from "../custom_components/extended_openai_conversation_responses/frontend/agent-config-editor-base.js";

test("cached tokens follow node identity and changed text", () => {
  const node = {};
  const tokens = cachedFunctionSearchTokens(node, "Café Reminders");
  assert.deepEqual(tokens, searchTokens("Café Reminders"));
  assert.equal(cachedFunctionSearchTokens(node, "Café Reminders"), tokens);
  assert.notEqual(cachedFunctionSearchTokens({}, "Café Reminders"), tokens);
  const changed = cachedFunctionSearchTokens(node, "Kitchen lights");
  assert.notEqual(changed, tokens);
  assert.deepEqual(changed, searchTokens("Kitchen lights"));
});

test("the active Local Handling binder retains short-prefix and empty-state behavior", () => {
  const search = new EventTarget();
  const empty = {hidden:true};
  const choices = ["Turn on lights", "Café reminders", "开灯", "مصباح", "лампа"].map(text => ({dataset:{choiceSearch:text}, hidden:false}));
  const root = new EventTarget();
  root.querySelector = selector => selector === "#local-intent-search" ? search : selector === "#local-intents-empty" ? empty : null;
  root.querySelectorAll = selector => selector === "[data-local-intent-choice]" ? choices : [];
  bindConfiguration({shadowRoot:root});
  for (const query of ["t", "ca", "cafe", "reminder", "开灯", "مصباح", "лампа", "!!!", "no match", "", "  "]) {
    search.value = query;
    search.dispatchEvent(new Event("input"));
    assert.deepEqual(choices.map(choice => choice.hidden), choices.map(choice => !matchesFunctionSearch(query, choice.dataset.choiceSearch, 1)));
    assert.equal(empty.hidden, !query.trim() || choices.some(choice => !choice.hidden));
  }
  choices[0].dataset.choiceSearch = "Updated weather";
  search.value = "wea";
  search.dispatchEvent(new Event("input"));
  assert.equal(choices[0].hidden, false);
});
