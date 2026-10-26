import assert from "node:assert/strict";
import test from "node:test";
import {reconcileSavedConfiguration} from "../custom_components/extended_openai_conversation_responses/frontend/configuration-save-dom.js";

test("a dirty form without a save anchor falls back without certifying its markup", () => {
  const main = {querySelector:() => null};
  const panel = {
    _agentId:"agent", _draft:{chat_model:"model"}, _configDirty:true,
    _eocRenderedRoute:"agent|assistant/basics", _eocMainMarkup:"previous markup",
    _viewKey:() => "assistant/basics",
    shadowRoot:{querySelector:selector => selector === "main" ? main : null},
    _content() { throw new Error("A missing anchor cannot certify rendered markup"); },
  };
  assert.equal(reconcileSavedConfiguration(panel, {main, agentId:"agent", view:"assistant/basics",
    route:panel._eocRenderedRoute, markup:"previous markup", model:"model", voiceMappings:{}}), false);
  assert.equal(panel._eocMainMarkup, null);
});

for (const pending of [false, true]) {
  test(`a dirty form restores missing controls before recording its markup, pending=${pending}`, t => {
    const previousDocument = globalThis.document, previousNode = globalThis.Node;
    globalThis.Node = {ELEMENT_NODE:1, TEXT_NODE:3, DOCUMENT_FRAGMENT_NODE:11};
    globalThis.document = {createElement:() => ({content:{nodeType:11, childNodes:[], querySelector:() => null}})};
    t.after(() => { globalThis.document = previousDocument; globalThis.Node = previousNode; });
    const save = {}, discard = {};
    const bar = {querySelector:selector => selector === "#save-config" ? save : discard};
    let mounted, inserted;
    const anchor = {insertAdjacentHTML(position, markup) { inserted = {position, markup}; mounted = bar; }};
    const main = {nodeType:11, childNodes:[], querySelector:selector => selector === ".save-bar" ? mounted : selector === "#save-bar-anchor" ? anchor : null};
    const panel = {
      _agentId:"agent", _draft:{chat_model:"model"}, _configDirty:true, _configurationSaving:pending,
      _eocRenderedRoute:"agent|assistant/basics", _viewKey:() => "assistant/basics",
      shadowRoot:{querySelector:selector => selector === "main" ? main : null},
      _selectedAgent:() => ({}), _content:() => "acknowledged markup",
    };
    assert.equal(reconcileSavedConfiguration(panel, {main, agentId:"agent", view:"assistant/basics",
      route:panel._eocRenderedRoute, markup:"previous markup", model:"model", voiceMappings:{}}), true);
    assert.equal(inserted.position, "beforebegin");
    assert.match(inserted.markup, /id="save-config"/);
    assert.match(inserted.markup, /id="revert-config"/);
    assert.equal(save.disabled, pending);
    assert.equal(discard.disabled, pending);
    assert.equal(save.textContent, pending ? "Saving…" : "Save changes");
    assert.equal(panel._eocMainMarkup, "acknowledged markup");
  });
}
